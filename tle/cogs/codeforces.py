import datetime
import random
from typing import List
import math
import time
import asyncio
import disnake

from disnake.ext import commands
from matplotlib import pyplot as plt
from collections import defaultdict, namedtuple

from tle import constants
from tle.util import codeforces_api as cf
from tle.util import codeforces_common as cf_common
from tle.util import discord_common
from tle.util.db import user_db_conn
from tle.util.db.user_db_conn import Gitgud
from tle.util.db.user_db_conn import Duel, DuelType, Winner
from tle.util import paginator
from tle.util import table
from tle.util import graph_common as gc
from tle.util import cache_system2

from PIL import Image, ImageFont, ImageDraw

_GITGUD_NO_SKIP_TIME = 1 * 60 * 60
_GITGUD_SCORE_DISTRIB = (2, 3, 5, 8, 12, 17, 23, 23, 23)
_GITGUD_MAX_NEG_DELTA_VALUE = -300
_GITGUD_MAX_POS_DELTA_VALUE = 300

class CodeforcesCogError(commands.CommandError):
    pass

_DUEL_INVALIDATE_TIME = 2 * 60
_DUEL_EXPIRY_TIME = 5 * 60
_DUEL_OFFICIAL_CUTOFF = 3500
_DUEL_NO_DRAW_TIME = 10 * 60
_ELO_CONSTANT = 60

DuelRank = namedtuple(
    'Rank', 'low high title title_abbr color_graph color_embed')

DUEL_RANKS = (
    DuelRank(-10 ** 9, 1300, 'Newbie', 'N', '#CCCCCC', 0x808080),
    DuelRank(1300, 1400, 'Pupil', 'P', '#77FF77', 0x008000),
    DuelRank(1400, 1500, 'Specialist', 'S', '#77DDBB', 0x03a89e),
    DuelRank(1500, 1600, 'Expert', 'E', '#AAAAFF', 0x0000ff),
    DuelRank(1600, 1700, 'Candidate Master', 'CM', '#FF88FF', 0xaa00aa),
    DuelRank(1700, 1800, 'Master', 'M', '#FFCC88', 0xff8c00),
    DuelRank(1800, 1900, 'International Master', 'IM', '#FFBB55', 0xf57500),
    DuelRank(1900, 2000, 'Grandmaster', 'GM', '#FF7777', 0xff3030),
    DuelRank(2000, 2100, 'International Grandmaster',
             'IGM', '#FF3333', 0xff0000),
    DuelRank(2100, 10 ** 9, 'Legendary Grandmaster',
             'LGM', '#AA0000', 0xcc0000)
)

def rating2rank(rating):
    for rank in DUEL_RANKS:
        if rank.low <= rating < rank.high:
            return rank

def elo_prob(player, opponent):
    return (1 + 10**((opponent - player) / 400))**-1

def elo_delta(player, opponent, win):
    return _ELO_CONSTANT * (win - elo_prob(player, opponent))

def get_cf_user(userid, guild_id):
    handle = cf_common.user_db.get_handle(userid, guild_id)
    return cf_common.user_db.fetch_cf_user(handle)

def complete_duel(duelid, guild_id, win_status, winner_id, loser_id, finish_time, score, dtype):
    winner_r = cf_common.user_db.get_duel_rating(winner_id)
    loser_r = cf_common.user_db.get_duel_rating(loser_id)

    delta = round(elo_delta(winner_r, loser_r, score))

    rc = cf_common.user_db.complete_duel(
        duelid, win_status, finish_time, winner_id, loser_id, delta, dtype)
    if rc == 0:
        raise CodeforcesCogError('Hey! No cheating!')

    if dtype == DuelType.UNOFFICIAL:
        return None

    winner_cf = get_cf_user(winner_id, guild_id)
    loser_cf = get_cf_user(loser_id, guild_id)
    desc = f'Rating change after <@{winner_id}> vs <@{loser_id}>:'

    if delta < 0:
        delta = -delta
        winner_r, loser_r = loser_r, winner_r
        winner_cf, loser_cf = loser_cf, winner_cf
    # swap winner with loser if needed

    embed = discord_common.cf_color_embed(description=desc)
    embed.add_field(name=f'{winner_cf.handle}',
                    value=f'{winner_r} \N{LONG RIGHTWARDS ARROW} {winner_r + delta} **(+{delta})**')
    embed.add_field(name=f'{loser_cf.handle}',
                    value=f'{loser_r} \N{LONG RIGHTWARDS ARROW} {loser_r - delta} **(-{delta})**')
    return embed

class Codeforces(commands.Cog, description = "Ask for or challenge your friends with recommended problems"):
    def __init__(self, bot):
        self.bot = bot
        self.converter = commands.MemberConverter()
        self.draw_offers = {}

    async def _validate_gitgud_status(self, inter, delta):
        if delta is not None and delta % 100 != 0:
            await inter.edit_original_message(embed = discord_common.embed_alert('Delta must be a multiple of 100.'))
            raise Exception

        if delta is not None and (delta < _GITGUD_MAX_NEG_DELTA_VALUE or delta > _GITGUD_MAX_POS_DELTA_VALUE):
            await inter.edit_original_message(embed = discord_common.embed_alert(f'Delta must range from {_GITGUD_MAX_NEG_DELTA_VALUE} to {_GITGUD_MAX_POS_DELTA_VALUE}.'))
            raise Exception

        user_id = inter.author.id
        active = cf_common.user_db.check_challenge(user_id)
        if active is not None:
            _, _, name, contest_id, index, _ = active
            url = f'{cf.CONTEST_BASE_URL}{contest_id}/problem/{index}'
            await inter.edit_original_message(embed = discord_common.embed_alert(f'You have an active challenge [{name}]({url}).\nType `/nogud` to give up the challenge.'))
            raise Exception

    async def _gitgud(self, inter, handle, problem, delta):
        # The caller of this function is responsible for calling `_validate_gitgud_status` first.
        user_id = inter.author.id

        issue_time = datetime.datetime.now().timestamp()
        rc = cf_common.user_db.new_challenge(user_id, issue_time, problem, delta)
        if rc != 1:
            return await inter.edit_original_message('Your challenge has already been added to the database!')

        title = f'{problem.index}. {problem.name}'
        desc = cf_common.cache2.contest_cache.get_contest(problem.contestId).name
        embed = disnake.Embed(title=title, url=problem.url, description=desc)
        embed.add_field(name='Rating', value=problem.rating)
        await inter.edit_original_message(f'Challenge problem for `{handle}`', embed=embed)

    @commands.slash_command(description='Request an unsolved problem from a contest you participated in')
    @cf_common.user_guard(group='gitgud')
    async def upsolve(self, inter, index: int = -1):
        """
        Parameters
        ----------
        index: Index of the problem to upsolve
        """
        await inter.response.defer()

        await self._validate_gitgud_status(inter,delta=None)
        handle, = await cf_common.resolve_handles(inter, self.converter, ('!' + str(inter.author),))
        user = cf_common.user_db.fetch_cf_user(handle)
        rating = round(user.effective_rating, -2)
        resp = await cf.user.rating(handle=handle)
        contests = {change.contestId for change in resp}
        submissions = await cf.user.status(handle=handle)
        solved = {sub.problem.name for sub in submissions if sub.verdict == 'OK'}
        problems = [prob for prob in cf_common.cache2.problem_cache.problems
                    if prob.name not in solved and prob.contestId in contests
                    and ((prob.rating - rating) >= _GITGUD_MAX_NEG_DELTA_VALUE and (prob.rating - rating) <= _GITGUD_MAX_POS_DELTA_VALUE)]

        if not problems:
            return await inter.edit_original_message('Problems not found within the search parameters')

        problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(
            problem.contestId).startTimeSeconds, reverse=True)

        if index > 0 and index <= len(problems):
            problem = problems[index - 1]
            await self._gitgud(inter, handle, problem, problem.rating - rating)
        else:
            problems = problems[:100]
              
            def make_line(i, prob):
                data = (f'{i + 1}: [{prob.name}]({prob.url}) [{prob.rating}]')
                return data

            def make_page(chunk, pi, num):
                title = f'Select a problem to upsolve (1-{num}).\nThen type `/upsolve <problem index>` to get started.'
                msg = '\n'.join(make_line(10*pi+i, prob) for i, prob in enumerate(chunk))
                embed = discord_common.cf_color_embed(description=msg)
                return title, embed
                  
            pages = [make_page(chunk, pi, len(problems)) for pi, chunk in enumerate(paginator.chunkify(problems, 10))]
            await paginator.paginate(self.bot, 'edit', inter, pages,
                               message = await inter.original_message(),
                               wait_time=5 * 60, set_pagenum_footers=True)   

    @commands.slash_command(description='Recommend a CodeForces problem')
    @cf_common.user_guard(group='gitgud')
    async def gimme(self, inter, rating: commands.Range[800, 3500] = None, tags: str = ""):
        """
        Parameters
        ----------
        tags: Tags of them problem (separated by spaces)
        rating: Rating of the problem to be recommended
        """
        await inter.response.defer()

        tags = list(tags.split())
        handle, = await cf_common.resolve_handles(inter, self.converter, ('!' + str(inter.author),))
        if rating == None: rating = round(cf_common.user_db.fetch_cf_user(handle).effective_rating, -2)
        if rating % 100 != 0: return await inter.edit_original_message('Problem rating should be a multiple of 100.')

        submissions = await cf.user.status(handle=handle)
        solved = {sub.problem.name for sub in submissions if sub.verdict == 'OK'}

        problems = [prob for prob in cf_common.cache2.problem_cache.problems
                    if prob.rating == rating and prob.name not in solved and
                    not cf_common.is_contest_writer(prob.contestId, handle)]
        if tags:
            problems = [prob for prob in problems if prob.tag_matches(tags)]

        if not problems:
            return await inter.edit_original_message('Problems not found within the search parameters')

        problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(
            problem.contestId).startTimeSeconds)

        choice = max([random.randrange(len(problems)) for _ in range(2)])
        problem = problems[choice]

        title = f'{problem.index}. {problem.name}'
        desc = cf_common.cache2.contest_cache.get_contest(problem.contestId).name
        embed = disnake.Embed(title=title, url=problem.url, description=desc)
        embed.add_field(name='Rating', value=problem.rating)
        if tags:
            tagslist = ', '.join(problem.tag_matches(tags))
            embed.add_field(name='Matched tags', value=tagslist)
        await inter.edit_original_message(f'Recommended problem for `{handle}`', embed=embed)

    @commands.slash_command(description='Create a mashup contest')
    async def mashup(self, inter, handles: str = None, tags: str = "", delta: commands.option_enum(["-300", "-200", "-100", "0", "+100", "+200", "+300"]) = "0"):
        """
        Parameters
        ----------
        handles: List of handles (separated by spaces)
        tags: Tags of the problems (separated by spaces)
        delta: Ratings difference to average ratings.
        """
        await inter.response.defer()

        if handles == None: handles = f"!{str(inter.author)}"
        handles = tuple(handles.split())
        tags = list(tags.split())
        delta = int(delta)
        
        handles = await cf_common.resolve_handles(inter, self.converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        submissions = [sub for user in resp for sub in user]
        solved = {sub.problem.name for sub in submissions}
        info = await cf.user.info(handles=handles)
        rating = int(round(sum(user.effective_rating for user in info) / len(handles), -2))
        rating += delta
        rating = max(800, rating)
        rating = min(3500, rating)
        problems = [prob for prob in cf_common.cache2.problem_cache.problems
                    if abs(prob.rating - rating) <= 300 and prob.name not in solved
                    and not any(cf_common.is_contest_writer(prob.contestId, handle) for handle in handles)
                    and not cf_common.is_nonstandard_problem(prob)]
        if tags:
            problems = [prob for prob in problems if prob.tag_matches(tags)]

        if len(problems) < 4:
            return await inter.edit_original_message('Problems not found within the search parameters')

        problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(
            problem.contestId).startTimeSeconds)

        choices = []
        for i in range(4):
            k = max(random.randrange(len(problems) - i) for _ in range(2))
            for c in choices:
                if k >= c:
                    k += 1
            choices.append(k)
            choices.sort()

        problems = sorted([problems[k] for k in choices], key=lambda problem: problem.rating)
        msg = '\n'.join(f'{"ABCD"[i]}: [{p.name}]({p.url}) [{p.rating}]' for i, p in enumerate(problems))
        str_handles = '`, `'.join(handles)
        embed = discord_common.cf_color_embed(description=msg)
        await inter.edit_original_message(f'Mashup contest for `{str_handles}`', embed=embed)

    @commands.slash_command(description='Challenge yourself and earn points')
    @cf_common.user_guard(group='gitgud')
    async def gitgud(self, inter, delta: commands.option_enum(["-300", "-200", "-100", "0", "+100", "+200", "+300"]) = "0"):
        """
        Request a problem for gitgud points.
        delta  | -300 | -200 | -100 |  0  | +100 | +200 | +300 |
        points |   2  |   3  |   5  |  8  |  12  |  17  |  23  |
        
        Parameters
        ----------
        delta: Rating difference to average rating.
        """
        await inter.response.defer()

        delta = int(delta)
        await self._validate_gitgud_status(inter, delta)

        handle, = await cf_common.resolve_handles(inter, self.converter, ('!' + str(inter.author),))
        user = cf_common.user_db.fetch_cf_user(handle)
        rating = round(user.effective_rating, -2)
        rating = max(rating, 1200)
        submissions = await cf.user.status(handle=handle)
        solved = {sub.problem.name for sub in submissions}
        noguds = cf_common.user_db.get_noguds(inter.author.id)

        problems = [prob for prob in cf_common.cache2.problem_cache.problems
                    if (prob.rating == rating + delta and
                        prob.name not in solved and
                        prob.name not in noguds)]

        def check(problem):
            return (not cf_common.is_nonstandard_problem(problem) and
                    not cf_common.is_contest_writer(problem.contestId, handle))

        problems = list(filter(check, problems))
        if not problems:
            return await inter.edit_original_message('No problem to assign')

        problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(
            problem.contestId).startTimeSeconds)

        choice = max(random.randrange(len(problems)) for _ in range(2))
        await self._gitgud(inter, handle, problems[choice], delta)

    @commands.slash_command(description = 'Print user gitgud history')
    async def gitlog(self, inter, member: disnake.Member = None):
        """
        Displays the list of gitgud problems issued to the specified member, excluding those noguded by admins.
        If the challenge was completed, time of completion and amount of points gained will also be displayed.

        Parameters
        ----------
        member: Server member to print user gitgud history of
        """
        await inter.response.defer()

        def make_line(entry):
            issue, finish, name, contest, index, delta, status = entry
            problem = cf_common.cache2.problem_cache.problem_by_name[name]
            line = f'[{name}]({problem.url})\N{EN SPACE}[{problem.rating}]'
            if finish:
                time_str = cf_common.days_ago(finish)
                points = f'{_GITGUD_SCORE_DISTRIB[delta // 100 + 3]:+}'
                line += f'\N{EN SPACE}{time_str}\N{EN SPACE}[{points}]'
            return line

        def make_page(chunk,score):
            message = f'Gitgud log for `{member}` (total score: {score})'
            log_str = '\n'.join(make_line(entry) for entry in chunk)
            embed = discord_common.cf_color_embed(description=log_str)
            return message, embed

        member = member or inter.author
        data = cf_common.user_db.gitlog(member.id)
        if not data: return await inter.edit_original_message(f'`{member}` has no gitgud history.')

        score = 0
        for entry in data:
            issue, finish, name, contest, index, delta, status = entry
            if finish: score += _GITGUD_SCORE_DISTRIB[delta // 100 + 3]

        pages = [make_page(chunk, score) for chunk in paginator.chunkify(data, 7)]

        await paginator.paginate(self.bot, 'edit', inter, pages,
                   message = await inter.original_message(),
                   wait_time=5 * 60, set_pagenum_footers=True)

    @commands.slash_command(description='Report challenge completion')
    @cf_common.user_guard(group='gitgud')
    async def gotgud(self, inter):
        await inter.response.defer()

        handle, = await cf_common.resolve_handles(inter, self.converter, ('!' + str(inter.author),))
        user_id = inter.author.id
        active = cf_common.user_db.check_challenge(user_id)
        if not active:
            return await inter.edit_original_message(f'You do not have an active challenge')

        submissions = await cf.user.status(handle=handle)
        solved = {sub.problem.name for sub in submissions if sub.verdict == 'OK'}

        challenge_id, issue_time, name, contestId, index, delta = active
        if not name in solved:
            return await inter.edit_original_message('You haven\'t completed your challenge.')

        delta = _GITGUD_SCORE_DISTRIB[delta // 100 + 3]
        finish_time = int(datetime.datetime.now().timestamp())
        rc = cf_common.user_db.complete_challenge(user_id, challenge_id, finish_time, delta)
        if rc == 1:
            duration = cf_common.pretty_time_format(finish_time - issue_time)
            await inter.edit_original_message(f'Challenge completed in {duration}. {handle} gained {delta} points.')
        else:
            await inter.edit_original_message('You have already claimed your points')

    @commands.slash_command(description='Skip challenge')
    @cf_common.user_guard(group='gitgud')
    async def nogud(self, inter, member: disnake.Member = None):
        """
        Parameters
        ----------
        member: Member to skip challenge
        """
        await inter.response.defer()

        member = member or inter.author
        has_perm = await self.bot.is_owner(inter.author) \
            or inter.author.guild_permissions.administrator \
            or discord_common.is_guild_owner_predicate(inter)

        if not has_perm and member != inter.author:
            return await inter.edit_original_message('You don\'t have permission to skip other members\' gitgud challenge.')

        await cf_common.resolve_handles(inter, self.converter, ('!' + str(member),))
        active = cf_common.user_db.check_challenge(member.id)
        if not active:
            revoker = 'You' if member == inter.author else f'`{member}`'
            return await inter.edit_original_message(f'{revoker} do not have an active challenge')

        challenge_id, issue_time, name, contestId, index, delta = active
        finish_time = int(datetime.datetime.now().timestamp())
        if not has_perm and finish_time - issue_time < _GITGUD_NO_SKIP_TIME:
            skip_time = cf_common.pretty_time_format(issue_time + _GITGUD_NO_SKIP_TIME - finish_time)
            return await inter.edit_original_message(f'Think more. You can skip your challenge in {skip_time}.')
        rc = cf_common.user_db.skip_challenge(member.id, challenge_id, Gitgud.NOGUD)
        if rc == 1:
            await inter.edit_original_message(f'Challenge skipped.')
        else:
            await inter.edit_original_message(f'Failed to skip challenge.')

    @commands.slash_command(description='Recommend a contest')
    async def vc(self, inter, handles: str = None, pattern: str = ""):
        """
        Recommends a contest based on Codeforces rating of the handle provided.
        e.g ;vc mblazev c1729 +global +hello +goodbye +avito

        Parameters
        ----------
        handles: List of handles (separated by spaces)
        pattern: e.g: global edu div3 goodbye (separated by spaces)
        """
        await inter.response.defer()

        pattern = list(pattern.split())
        if handles == None: handles = f"!{str(inter.author)}"
        handles = list(handles.split())

        handles = await cf_common.resolve_handles(inter, self.converter, handles, maxcnt=25)
        info = await cf.user.info(handles=handles)
        contests = cf_common.cache2.contest_cache.get_contests_in_phase('FINISHED')

        if not pattern:
            divr = sum(user.effective_rating for user in info) / len(handles)
            div1_indicators = ['div1', 'global', 'avito', 'goodbye', 'hello']
            pattern = ['div3'] if divr < 1600 else ['div2'] if divr < 2100 else div1_indicators

        recommendations = {contest.id for contest in contests if
                           contest.matches(pattern) and
                           not cf_common.is_nonstandard_contest(contest) and
                           not any(cf_common.is_contest_writer(contest.id, handle)
                                       for handle in handles)}

        # Discard contests in which user has non-CE submissions.
        visited_contests = await cf_common.get_visited_contests(handles)
        recommendations -= visited_contests

        if not recommendations:
            return await inter.edit_original_message('Unable to recommend a contest')

        recommendations = list(recommendations)
        random.shuffle(recommendations)
        contests = [cf_common.cache2.contest_cache.get_contest(contest_id) for contest_id in recommendations[:25]]

        def make_line(c):
            return f'[{c.name}]({c.url}) {cf_common.pretty_time_format(c.durationSeconds)}'

        def make_page(chunk):
            str_handles = '`, `'.join(handles)
            message = f'Recommended contest(s) for `{str_handles}`'
            vc_str = '\n'.join(make_line(contest) for contest in chunk)
            embed = discord_common.cf_color_embed(description=vc_str)
            return message, embed

        pages = [make_page(chunk) for chunk in paginator.chunkify(contests, 5)]
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message = await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    @commands.slash_command(description='Compete coding with your friend')
    async def duel(self, inter):
        """Group for commands pertaining to duels"""
        pass

    async def register(self, member: disnake.Member):
        """Register a duelist"""
        rc = cf_common.user_db.register_duelist(member.id)

    @duel.sub_command(description='Challenge another server member to a duel')
    async def challenge(self, inter, opponent: disnake.Member, rating: commands.Range[800, 3500] = None):
        """
        Parameters
        ----------
        opponent: A server member you want to challenge
        rating: Rating of the problem to challenge to
        """
        await inter.response.defer()

        challenger_id = inter.author.id
        challengee_id = opponent.id

        await cf_common.resolve_handles(inter, self.converter, ('!' + str(inter.author), '!' + str(opponent)))
        userids = [challenger_id, challengee_id]
        handles = [cf_common.user_db.get_handle(
            userid, inter.guild.id) for userid in userids]
        submissions = [await cf.user.status(handle=handle) for handle in handles]

        if not cf_common.user_db.is_duelist(challenger_id):
            await self.register(inter.author)
        if not cf_common.user_db.is_duelist(challengee_id):
            await self.register(opponent)

        if challenger_id == challengee_id:
            return await inter.edit_original_message(
                f'{inter.author.mention}, you cannot challenge yourself!')
        if cf_common.user_db.check_duel_challenge(challenger_id):
            return await inter.edit_original_message(
                f'{inter.author.mention}, you are currently in a duel!')
        if cf_common.user_db.check_duel_challenge(challengee_id):
            return await inter.edit_original_message(
                f'`{opponent}` is currently in a duel!')

        users = [cf_common.user_db.fetch_cf_user(handle) for handle in handles]
        lowest_rating = min(user.rating or 0 for user in users)
        suggested_rating = max(round(lowest_rating, -2) - 200, 800)
        rating = round(rating, -2) if rating else suggested_rating

        solved = {
            sub.problem.name for subs in submissions for sub in subs if sub.verdict != 'COMPILATION_ERROR'}
        seen = {name for userid in userids for name,
                in cf_common.user_db.get_duel_problem_names(userid)}

        def get_problems(rating):
            return [prob for prob in cf_common.cache2.problem_cache.problems
                    if prob.rating == rating and prob.name not in solved and prob.name not in seen
                    and not any(cf_common.is_contest_writer(prob.contestId, handle) for handle in handles)
                    and not cf_common.is_nonstandard_problem(prob)]

        problems = []
        for problems in map(get_problems, range(rating, 400, -100)):
            if problems:
                break

        if not problems:
            return await inter.edit_original_message(
                f'No unsolved {rating} rated problems left for `{handles[0]}` vs `{handles[1]}`.')

        problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(
            problem.contestId).startTimeSeconds)

        choice = max(random.randrange(len(problems)) for _ in range(2))
        problem = problems[choice]

        issue_time = datetime.datetime.now().timestamp()
        duelid = cf_common.user_db.create_duel(
            challenger_id, challengee_id, issue_time, problem, DuelType.OFFICIAL)

        await inter.edit_original_message(f'{inter.author.mention} is challenging {opponent.mention} to a {rating} rated duel!\nType `/duel accept` to accept or `/duel decline` to decline the challenge.')
        await asyncio.sleep(_DUEL_EXPIRY_TIME)
        if cf_common.user_db.cancel_duel(duelid, Duel.EXPIRED):
            await inter.channel.send(f'{inter.author.mention}, your request to duel `{opponent}` has expired!')

    @duel.sub_command(description='Decline a duel')
    async def decline(self, inter):
        await inter.response.defer()

        active = cf_common.user_db.check_duel_decline(inter.author.id)
        if not active:
            return await inter.edit_original_message(
                f'{inter.author.mention}, you are not being challenged!')

        duelid, challenger = active
        challenger = inter.guild.get_member(challenger)
        cf_common.user_db.cancel_duel(duelid, Duel.DECLINED)
        await inter.edit_original_message(f'{inter.author.mention} declined a challenge by {challenger.mention}.')

    @duel.sub_command(description='Withdraw a challenge')
    async def withdraw(self, inter):
        await inter.response.defer()

        active = cf_common.user_db.check_duel_withdraw(inter.author.id)
        if not active:
            return await inter.edit_original_message(
                f'{inter.author.mention}, you are not challenging anyone.')

        duelid, challengee = active
        challengee = inter.guild.get_member(challengee)
        cf_common.user_db.cancel_duel(duelid, Duel.WITHDRAWN)
        await inter.edit_original_message(f'{inter.author.mention} withdrew a challenge to `{challengee}`.')

    @duel.sub_command(description='Accept a duel')
    async def accept(self, inter):
        await inter.response.defer()

        active = cf_common.user_db.check_duel_accept(inter.author.id)
        if not active:
            return await inter.edit_original_message(f'{inter.author.mention}, you are not being challenged.')

        duelid, challenger_id, name = active
        challenger = inter.guild.get_member(challenger_id)
        await inter.edit_original_message(f'Duel between {challenger.mention} and {inter.author.mention} starting in 15 seconds!')
        await asyncio.sleep(15)

        start_time = datetime.datetime.now().timestamp()
        rc = cf_common.user_db.start_duel(duelid, start_time)
        if rc != 1: return await inter.channel.send(embed = discord_common.embed_alert(f'Unable to start the duel between {challenger.mention} and {inter.author.mention}.'))

        problem = cf_common.cache2.problem_cache.problem_by_name[name]
        title = f'{problem.index}. {problem.name}'
        desc = cf_common.cache2.contest_cache.get_contest(
            problem.contestId).name
        embed = disnake.Embed(title=title, url=problem.url, description=desc)
        embed.add_field(name='Rating', value=problem.rating)
        await inter.channel.send(f'Starting duel: {challenger.mention} vs {inter.author.mention}', embed=embed)

    @duel.sub_command(description='Complete a duel')
    async def complete(self, inter):
        await inter.response.defer()

        active = cf_common.user_db.check_duel_complete(inter.author.id)
        if not active: return await inter.edit_original_message(f'{inter.author.mention}, you are not in a duel.')

        duelid, challenger_id, challengee_id, start_time, problem_name, contest_id, index, dtype = active

        if challengee_id == inter.author.id:
            challenger_id, challengee_id = challengee_id, challenger_id

        UNSOLVED = 0
        TESTING = -1

        async def get_solve_time(userid):
            handle = cf_common.user_db.get_handle(userid, inter.guild.id)
            subs = [sub for sub in await cf.user.status(handle=handle)
                    if (sub.verdict == 'OK' or sub.verdict == 'TESTING')
                    and sub.problem.contestId == contest_id
                    and sub.problem.index == index]

            if not subs:
                return UNSOLVED
            if 'TESTING' in [sub.verdict for sub in subs]:
                return TESTING
            return min(subs, key=lambda sub: sub.creationTimeSeconds).creationTimeSeconds

        challenger_time = await get_solve_time(challenger_id)
        challengee_time = await get_solve_time(challengee_id)

        if challenger_time == TESTING or challengee_time == TESTING:
            return await inter.edit_original_message(f'Wait a bit, {inter.author.mention}. A submission is still being judged.')

        if challenger_time and challengee_time:
            if challenger_time != challengee_time:
                diff = cf_common.pretty_time_format(
                    abs(challengee_time - challenger_time), always_seconds=True)
                winner = challenger_id if challenger_time < challengee_time else challengee_id
                loser  = challenger_id if challenger_time > challengee_time else challengee_id
                win_status = Winner.CHALLENGER if winner == challenger_id else Winner.CHALLENGEE
                embed = complete_duel(duelid, inter.guild.id, win_status, winner, loser, min(challenger_time, challengee_time), 1, dtype)
                await inter.edit_original_message(f'Both <@{winner}> and <@{loser}> solved it but <@{winner}> was {diff} faster!', embed=embed)
            else:
                embed = complete_duel(duelid, inter.guild.id, Winner.DRAW, challenger_id, challengee_id, challenger_time, 0.5, dtype)
                await inter.edit_original_message(f"<@{challenger_id}> and <@{challengee_id}> solved the problem in the exact same amount of time! It's a draw!", embed=embed)
        elif challenger_time:
            diff = cf_common.pretty_time_format(abs(challenger_time - start_time), always_seconds=True)
            embed = complete_duel(duelid, inter.guild.id, Winner.CHALLENGER, challenger_id, challengee_id, challenger_time, 1, dtype)
            await inter.edit_original_message(f'<@{challenger_id}> beat <@{challengee_id}> in a duel after {diff}!', embed=embed)
        elif challengee_time:
            diff = cf_common.pretty_time_format(abs(challengee_time - start_time), always_seconds=True)
            embed = complete_duel(duelid, inter.guild.id, Winner.CHALLENGEE, challengee_id, challenger_id, challengee_time, 1, dtype)
            await inter.edit_original_message(f'<@{challengee_id}> beat <@{challenger_id}> in a duel after {diff}!', embed=embed)
        else:
            await inter.edit_original_message('Nobody solved the problem yet.')

    @duel.sub_command(description='Offer/Accept a draw')
    async def draw(self, inter):
        await inter.response.defer()

        active = cf_common.user_db.check_duel_draw(inter.author.id)
        if not active: return await inter.edit_original_message(f'{inter.author.mention}, you are not in a duel.')

        duelid, challenger_id, challengee_id, start_time, dtype = active
        now = datetime.datetime.now().timestamp()
        if now - start_time < _DUEL_NO_DRAW_TIME:
            draw_time = cf_common.pretty_time_format(
                start_time + _DUEL_NO_DRAW_TIME - now)
            return await inter.edit_original_message(f'Think more {inter.author.mention}. You can offer a draw in {draw_time}.')

        if not duelid in self.draw_offers:
            self.draw_offers[duelid] = inter.author.id
            offeree_id = challenger_id if inter.author.id != challenger_id else challengee_id
            offeree = inter.guild.get_member(offeree_id)
            if offeree == None:
                cf_common.user_db.invalidate_duel(duelid)
                return await inter.edit_original_message(f'You can offer draw because your challenger is in this server. If you can\'t complete this duel challenge, please try `/duel invalidate`')
            return await inter.edit_original_message(f'{inter.author.mention} is offering a draw to {offeree.mention}!')

        if self.draw_offers[duelid] == inter.author.id:
            return await inter.edit_original_message(f'{inter.author.mention}, you\'ve already offered a draw.')

        offerer = inter.guild.get_member(self.draw_offers[duelid])
        embed = complete_duel(duelid, inter.guild.id, Winner.DRAW, offerer.id, inter.author.id, now, 0.5, dtype)
        await inter.edit_original_message(f'{inter.author.mention} accepted draw offer by {offerer.mention}.', embed=embed)

    @duel.sub_command(description='Show duelist profile')
    async def profile(self, inter, member: disnake.Member = None):
        """
        Parameters
        ----------
        member: Member to show duelist profile
        """
        await inter.response.defer()

        member = member or inter.author
        if not cf_common.user_db.is_duelist(member.id):
            await self.register(member)

        user = get_cf_user(member.id, inter.guild.id)
        if not user:
            embed = discord_common.embed_neutral(f'Handle for `{member}` not found in database')
            return await inter.edit_original_message(embed = embed)

        rating = cf_common.user_db.get_duel_rating(member.id)
        desc = f'Duelist profile of {rating2rank(rating).title} {member.mention} aka **[{user.handle}]({user.url})**'
        embed = disnake.Embed(
            description=desc, color=rating2rank(rating).color_embed)
        embed.add_field(name='Rating', value=rating, inline=True)

        wins = cf_common.user_db.get_duel_wins(member.id)
        num_wins = len(wins)
        embed.add_field(name='Wins', value=num_wins, inline=True)
        num_losses = cf_common.user_db.get_num_duel_losses(member.id)
        embed.add_field(name='Losses', value=num_losses, inline=True)
        num_draws = cf_common.user_db.get_num_duel_draws(member.id)
        embed.add_field(name='Draws', value=num_draws, inline=True)
        num_declined = cf_common.user_db.get_num_duel_declined(member.id)
        embed.add_field(name='Declined', value=num_declined, inline=True)
        num_rdeclined = cf_common.user_db.get_num_duel_rdeclined(member.id)
        embed.add_field(name='Got declined', value=num_rdeclined, inline=True)

        def duel_to_string(duel):
            start_time, finish_time, problem_name, challenger, challengee = duel
            duel_time = cf_common.pretty_time_format(
                finish_time - start_time, shorten=True, always_seconds=True)
            when = cf_common.days_ago(start_time)
            loser_id = challenger if member.id != challenger else challengee
            loser = inter.guild.get_member(loser_id)
            problem = cf_common.cache2.problem_cache.problem_by_name[problem_name]
            if loser is None:
                return f'**[{problem.name}]({problem.url})** [{problem.rating}] versus `unknown` {when} in {duel_time}'
            return f'**[{problem.name}]({problem.url})** [{problem.rating}] versus `{loser}` {when} in {duel_time}'

        if wins:
            # sort by finish_time - start_time
            wins.sort(key=lambda duel: duel[1] - duel[0])
            embed.add_field(name='Fastest win',
                            value=duel_to_string(wins[0]), inline=False)
            embed.add_field(name='Slowest win',
                            value=duel_to_string(wins[-1]), inline=False)

        tmp = str(user.titlePhoto)
        if tmp[:2] == "//": tmp = "https:" + tmp

        embed.set_thumbnail(url=f'{tmp}')
        await inter.edit_original_message(embed=embed)

    async def _paginate_duels(self, fake_data, message, inter, show_id):
        data = []
        for d in fake_data:
            duelid, start_time, finish_time, name, challenger, challengee, winner = d
            challenger = inter.guild.get_member(challenger)
            challengee = inter.guild.get_member(challengee)
            if challenger != None and challengee != None: data.append(d)
        if not data: return await inter.edit_original_message(f'There are no duels to show.')

        def make_line(entry):
            duelid, start_time, finish_time, name, challenger, challengee, winner = entry
            duel_time = cf_common.pretty_time_format(finish_time - start_time, shorten=True, always_seconds=True)
            problem = cf_common.cache2.problem_cache.problem_by_name[name]
            when = cf_common.days_ago(start_time)
            idstr = f'{duelid}: '
            if winner != Winner.DRAW:
                loser = inter.guild.get_member(challenger if winner == Winner.CHALLENGEE else challengee)
                winner = inter.guild.get_member(challenger if winner == Winner.CHALLENGER else challengee)
                return f'{idstr if show_id else str()}[{name}]({problem.url}) [{problem.rating}] won by `{winner}` vs `{loser}` {when} in {duel_time}'
            else:
                challenger = inter.guild.get_member(challenger)
                challengee = inter.guild.get_member(challengee)
                return f'{idstr if show_id else str()}[{name}]({problem.url}) [{problem.rating}] drawn by `{challenger}` and `{challengee}` {when} after {duel_time}'

        def make_page(chunk):
            log_str = '\n'.join(make_line(entry) for entry in chunk)
            embed = discord_common.cf_color_embed(description=log_str)
            return message, embed

        return [make_page(chunk) for chunk in paginator.chunkify(data, 7)]

    @duel.sub_command(description='Print user dueling history')
    async def history(self, inter, member: disnake.Member = None):
        """
        Parameters
        ----------
        member: Member to show dueling history
        """
        await inter.response.defer()

        member = member or inter.author
        data = cf_common.user_db.get_duels(member.id)
        pages = await self._paginate_duels(
            data, f'Dueling history of {member.display_name}', inter, False)
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message=await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    @duel.sub_command(description='Print recent duels')
    async def recent(self, inter):
        await inter.response.defer()

        data = cf_common.user_db.get_recent_duels()
        pages = await self._paginate_duels(
            data, 'List of recent duels', inter, True)
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message=await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    @duel.sub_command(description='Print list of ongoing duels')
    async def ongoing(self, inter):
        await inter.response.defer()

        def make_line(entry):
            start_time, name, challenger, challengee = entry
            problem = cf_common.cache2.problem_cache.problem_by_name[name]
            now = datetime.datetime.now().timestamp()
            when = cf_common.pretty_time_format(
                now - start_time, shorten=True, always_seconds=True)
            challenger = inter.guild.get_member(challenger)
            challengee = inter.guild.get_member(challengee)
            return f'`{challenger}` vs `{challengee}`: [{name}]({problem.url}) [{problem.rating}] {when}'

        def make_page(chunk):
            message = f'List of ongoing duels:'

            log_str = '\n'.join(make_line(entry) for entry in chunk)
            embed = discord_common.cf_color_embed(description=log_str)
            return message, embed

        fake_data = cf_common.user_db.get_ongoing_duels()
        data = []

        for d in fake_data:
            start_time, name, challenger, challengee = d
            challenger = inter.guild.get_member(challenger)
            challengee = inter.guild.get_member(challengee)
            if challenger != None and challengee != None: data.append(d)

        if not data:
            return await inter.edit_original_message('There are no ongoing duels.')

        pages = [make_page(chunk) for chunk in paginator.chunkify(data, 7)]
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message=await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    @duel.sub_command(description="Show duelists")
    async def ranklist(self, inter):
        """Show the list of duelists with their duel rating."""
        await inter.response.defer()

        users = [(inter.guild.get_member(user_id), rating)
                 for user_id, rating in cf_common.user_db.get_duelists()]
        users = [(member, cf_common.user_db.get_handle(member.id, inter.guild.id), rating)
                 for member, rating in users
                 if member is not None and cf_common.user_db.get_num_duel_completed(member.id) > 0]

        _PER_PAGE = 10

        def make_page(chunk, page_num):
            style = table.Style('{:>}  {:<}  {:<}  {:<}')
            t = table.Table(style)
            t += table.Header('#', 'Name', 'Handle', 'Rating')
            t += table.Line()
            for index, (member, handle, rating) in enumerate(chunk):
                rating_str = f'{rating} ({rating2rank(rating).title_abbr})'

                handlestr = 'Unknown'
                if (handle is not None):
                    handlestr = handle
                t += table.Data(_PER_PAGE * page_num + index + 1,
                                f'{member.display_name}', handlestr, rating_str)

            table_str = f'```\n{t}\n```'
            embed = discord_common.cf_color_embed(description=table_str)
            return 'List of duelists', embed

        if not users:
            return await inter.edit_original_message('There are no active duelists.')

        pages = [make_page(chunk, k) for k, chunk in enumerate(
            paginator.chunkify(users, _PER_PAGE))]
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message = await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    async def invalidate_duel(self, inter, duelid, challenger_id, challengee_id):
        rc = cf_common.user_db.invalidate_duel(duelid)
        if rc == 0:
            return await inter.edit_original_message(f'Unable to invalidate duel {duelid}.')

        challenger = inter.guild.get_member(challenger_id)
        challengee = inter.guild.get_member(challengee_id)
        if challenger == None or challengee == None:
            await inter.edit_original_message(f'Duel challenge invalidated.')
        else:
            await inter.edit_original_message(f'Duel between {challenger.mention} and {challengee.mention} has been invalidated.')

    @duel.sub_command(description='Invalidate the duel')
    async def invalidate(self, inter, member: disnake.Member = None):
        """
        Declare your duel invalid. Use this if you've solved the problem prior to the duel.
        You can only use this functionality during the first 120 seconds of the duel.
        """
        await inter.response.defer()

        member = member or inter.author
        has_perm = await self.bot.is_owner(inter.author) \
            or inter.author.guild_permissions.administrator \
            or discord_common.is_guild_owner_predicate(inter)
        if not has_perm and member != inter.author:
            return await inter.edit_original_message(f'You don\'t have permission to invalidate other members\' duel.')

        active = cf_common.user_db.check_duel_complete(member.id)
        if not active: return await inter.edit_original_message(f'Member `{member}` is not in a duel.')

        duelid, challenger_id, challengee_id, start_time, _, _, _, _ = active
        if not has_perm and datetime.datetime.now().timestamp() - start_time > _DUEL_INVALIDATE_TIME:
            return await inter.edit_original_message(f'{inter.author.mention}, you can no longer invalidate your duel.\nPlease offer a draw or ask a moderator to invalidate your duel.')
        await self.invalidate_duel(inter, duelid, challenger_id, challengee_id)

    @duel.sub_command(description='Plot duel rating')
    async def rating(self, inter, member: disnake.Member = None):
        """
        Plot duelist's rating.

        Parameters
        ----------
        member: Member to plot duel rating
        """
        await inter.response.defer()

        if member == None: member = inter.author
        duelists = [member.id]
        duels = cf_common.user_db.get_complete_official_duels()
        rating = dict()
        plot_data = defaultdict(list)
        time_tick = 0
        for challenger, challengee, winner, finish_time in duels:
            challenger_r = rating.get(challenger, 1500)
            challengee_r = rating.get(challengee, 1500)
            if winner == Winner.CHALLENGER:
                delta = round(elo_delta(challenger_r, challengee_r, 1))
            elif winner == Winner.CHALLENGEE:
                delta = round(elo_delta(challenger_r, challengee_r, 0))
            else:
                delta = round(elo_delta(challenger_r, challengee_r, 0.5))

            rating[challenger] = challenger_r + delta
            rating[challengee] = challengee_r - delta
            if challenger in duelists or challengee in duelists:
                if challenger in duelists:
                    plot_data[challenger].append(
                        (time_tick, rating[challenger]))
                if challengee in duelists:
                    plot_data[challengee].append(
                        (time_tick, rating[challengee]))
                time_tick += 1

        if time_tick == 0:
            return await inter.edit_original_message(f'Nothing to plot.')

        plt.clf()
        # plot at least from mid gray to mid purple
        min_rating = 1350
        max_rating = 1550
        for rating_data in plot_data.values():
            for tick, rating in rating_data:
                min_rating = min(min_rating, rating)
                max_rating = max(max_rating, rating)

            x, y = zip(*rating_data)
            plt.plot(x, y,
                     linestyle='-',
                     marker='o',
                     markersize=2,
                     markerfacecolor='white',
                     markeredgewidth=0.5)

        gc.plot_rating_bg(DUEL_RANKS)
        plt.xlim(0, time_tick - 1)
        plt.ylim(min_rating - 100, max_rating + 100)

        labels = [
            gc.StrWrap('{} ({})'.format(
                inter.guild.get_member(duelist).display_name,
                rating_data[-1][1]))
            for duelist, rating_data in plot_data.items()
        ]
        plt.legend(labels, loc='upper left', prop=gc.fontprop)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Duel rating graph')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @discord_common.send_error_if(CodeforcesCogError, cf_common.ResolveHandleError,
                                  cf_common.FilterError)
    async def cog_slash_command_error(self, inter, error):
        pass


def setup(bot):
    bot.add_cog(Codeforces(bot))
