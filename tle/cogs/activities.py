import asyncio
import functools
import bisect
import logging
import time
import collections
import pytz
import datetime as dt
import time
import re
import itertools
import math
import disnake
import numpy as np
import pandas as pd
import seaborn as sns
import io

from tle.cogs.handles import ATCODER_RATED_RANKS, CODECHEF_RATED_RANKS, _CLIST_RESOURCE_SHORT_FORMS, _SUPPORTED_CLIST_RESOURCES
from collections import defaultdict, namedtuple
from typing import List
from disnake.ext import commands
from matplotlib import pyplot as plt
from matplotlib import patches as patches
from matplotlib import lines as mlines
from matplotlib import dates as mdates
from matplotlib.ticker import MultipleLocator

from tle import constants
from tle.util import db
from tle.util import tasks
from tle.util import table
from tle.util import paginator
from tle.util import ranklist as rl
from tle.util import cache_system2
from tle.util import codeforces_api as cf
from tle.util import codeforces_common as cf_common
from tle.util import clist_api as clist
from tle.util import discord_common
from tle.util import graph_common as gc

from PIL import Image, ImageFont, ImageDraw
pd.plotting.register_matplotlib_converters()

# A user is considered active if the duration since his last contest is not more than this
CONTEST_ACTIVE_TIME_CUTOFF = 90 * 24 * 60 * 60 # 90 days

def nice_sub_type(types):
    nice_map = {'CONTESTANT':'Contest: {}',
                'OUT_OF_COMPETITION':'Unofficial: {}',
                'VIRTUAL':'Virtual: {}',
                'PRACTICE':'Practice: {}'}
    return [nice_map[t] for t in types]

def _plot_rating(resp, mark='o', resource='codeforces.com'):

    for rating_changes in resp:
        ratings, times = [], []
        for rating_change in rating_changes:
            ratings.append(rating_change.newRating)
            times.append(dt.datetime.fromtimestamp(rating_change.ratingUpdateTimeSeconds))

        plt.plot(times,
                 ratings,
                 linestyle='-',
                 marker=mark,
                 markersize=3,
                 markerfacecolor='white',
                 markeredgewidth=0.5)
    if resource=='codechef.com':
        gc.plot_rating_bg(CODECHEF_RATED_RANKS)
    elif resource=='atcoder.jp':
        gc.plot_rating_bg(ATCODER_RATED_RANKS)
    else:
        gc.plot_rating_bg(cf.RATED_RANKS)
    plt.gcf().autofmt_xdate()

def _plot_perf(resp, mark='o', resource='codeforces.com'):

    for rating_changes in resp:
        ratings, times = [], []
        for rating_change in rating_changes:
            ratings.append(rating_change.oldRating)
            times.append(dt.datetime.fromtimestamp(rating_change.ratingUpdateTimeSeconds))

        plt.plot(times,
                 ratings,
                 linestyle='-',
                 marker=mark,
                 markersize=3,
                 markerfacecolor='white',
                 markeredgewidth=0.5)
    if resource=='codechef.com':
        gc.plot_rating_bg(CODECHEF_RATED_RANKS)
    elif resource=='atcoder.jp':
        gc.plot_rating_bg(ATCODER_RATED_RANKS)
    else:
        gc.plot_rating_bg(cf.RATED_RANKS)
    plt.gcf().autofmt_xdate()    

def _classify_submissions(submissions):
    solved_by_type = {sub_type: [] for sub_type in cf.Party.PARTICIPANT_TYPES}
    for submission in submissions:
        solved_by_type[submission.author.participantType].append(submission)
    return solved_by_type


def _plot_scatter(regular, practice, virtual, point_size):
    for contest in [practice, regular, virtual]:
        if contest:
            times, ratings = zip(*contest)
            plt.scatter(times, ratings, zorder=10, s=point_size)


def _running_mean(x, bin_size):
    n = len(x)

    cum_sum = [0] * (n + 1)
    for i in range(n):
        cum_sum[i + 1] = x[i] + cum_sum[i]

    res = [0] * (n - bin_size + 1)
    for i in range(bin_size, n + 1):
        res[i - bin_size] = (cum_sum[i] - cum_sum[i - bin_size]) / bin_size

    return res


def _get_extremes(contest, problemset, submissions):

    def in_contest(sub):
        return (sub.author.participantType == 'CONTESTANT' or
                (cf_common.is_rated_for_onsite_contest(contest) and
                 sub.author.participantType == 'OUT_OF_COMPETITION'))

    problemset = [prob for prob in problemset if prob.rating is not None]
    submissions = [sub for sub in submissions
                   if in_contest(sub) and sub.problem.rating is not None]
    solved = {sub.problem.index: sub.problem.rating for sub in submissions if
              sub.verdict == 'OK'}
    max_solved = max(solved.values(), default=None)
    min_unsolved = min((prob.rating for prob in problemset if prob.index not in solved),
                       default=None)
    return min_unsolved, max_solved


def _plot_extreme(handle, rating, packed_contest_subs_problemset, solved, unsolved, legend):
    extremes = [
        (dt.datetime.fromtimestamp(contest.end_time), _get_extremes(contest, problemset, subs))
        for contest, problemset, subs in packed_contest_subs_problemset
    ]
    regular = []
    fullsolves = []
    nosolves = []
    for t, (mn, mx) in extremes:
        if mn and mx:
            regular.append((t, mn, mx))
        elif mx:
            fullsolves.append((t, mx))
        elif mn:
            nosolves.append((t, mn))
        else:
            # No rated problems in the contest, which means rating is not yet available for
            # problems in this contest. Skip this data point.
            pass

    solvedcolor = 'tab:orange'
    unsolvedcolor = 'tab:blue'
    linecolor = '#00000022'
    outlinecolor = '#00000022'

    def scatter_outline(*args, **kwargs):
        plt.scatter(*args, **kwargs)
        kwargs['zorder'] -= 1
        kwargs['color'] = outlinecolor
        if kwargs['marker'] == '*':
            kwargs['s'] *= 3
        elif kwargs['marker'] == 's':
            kwargs['s'] *= 1.5
        else:
            kwargs['s'] *= 2
        if 'alpha' in kwargs:
            del kwargs['alpha']
        if 'label' in kwargs:
            del kwargs['label']
        plt.scatter(*args, **kwargs)

    plt.clf()
    time_scatter, plot_min, plot_max = zip(*regular)
    if unsolved:
        scatter_outline(time_scatter, plot_min, zorder=10,
                        s=14, marker='o', color=unsolvedcolor,
                        label='Easiest unsolved')
    if solved:
        scatter_outline(time_scatter, plot_max, zorder=10,
                        s=14, marker='o', color=solvedcolor,
                        label='Hardest solved')

    ax = plt.gca()
    if solved and unsolved:
        for t, mn, mx in regular:
            ax.add_line(mlines.Line2D((t, t), (mn, mx), color=linecolor))

    if fullsolves:
        scatter_outline(*zip(*fullsolves), zorder=15,
                        s=42, marker='*',
                        color=solvedcolor)
    if nosolves:
        scatter_outline(*zip(*nosolves), zorder=15,
                        s=32, marker='X',
                        color=unsolvedcolor)

    if legend:
        plt.legend(title=f'{handle}: {rating}', title_fontsize=plt.rcParams['legend.fontsize'],
                   loc='upper left').set_zorder(20)
    gc.plot_rating_bg(cf.RATED_RANKS)
    plt.gcf().autofmt_xdate()


def _plot_average(practice, bin_size, label: str = ''):
    if len(practice) > bin_size:
        sub_times, ratings = map(list, zip(*practice))

        sub_timestamps = [sub_time.timestamp() for sub_time in sub_times]
        mean_sub_timestamps = _running_mean(sub_timestamps, bin_size)
        mean_sub_times = [dt.datetime.fromtimestamp(timestamp) for timestamp in mean_sub_timestamps]
        mean_ratings = _running_mean(ratings, bin_size)

        plt.plot(mean_sub_times,
                 mean_ratings,
                 linestyle='-',
                 marker='',
                 markerfacecolor='white',
                 markeredgewidth=0.5,
                 label=label)

_CONTESTS_PER_PAGE = 5
_CONTEST_PAGINATE_WAIT_TIME = 5 * 60
_STANDINGS_PER_PAGE = 15
_STANDINGS_PAGINATE_WAIT_TIME = 2 * 60
_FINISHED_CONTESTS_LIMIT = 5
_WATCHING_RATED_VC_WAIT_TIME = 5 * 60  # seconds
_RATED_VC_EXTRA_TIME = 10 * 60  # seconds
_MIN_RATED_CONTESTANTS_FOR_RATED_VC = 50

_PATTERNS = {
    'abc': 'atcoder.jp',
    'arc': 'atcoder.jp',
    'agc': 'atcoder.jp',
    'kickstart': 'codingcompetitions.withgoogle.com',
    'codejam': 'codingcompetitions.withgoogle.com',
    'lunchtime': 'codechef.com',
    'long': 'codechef.com',
    'cookoff': 'codechef.com',
    'starters': 'codechef.com',
    'hackercup': 'facebook.com/hackercup'
}

def parse_date(arg):
    try:
        if len(arg) == 8:
            fmt = '%d%m%Y'
        elif len(arg) == 6:
            fmt = '%m%Y'
        elif len(arg) == 4:
            fmt = '%Y'
        else:
            raise ValueError
        return dt.datetime.strptime(arg, fmt)
    except ValueError:
        raise ActivitiesCogError(f'{arg} is an invalid date argument')



def _contest_start_time_format(contest, tz):
    start = dt.datetime.fromtimestamp(contest.startTimeSeconds, tz)
    tz = str(tz)
    if tz=='Asia/Kolkata':
        tz = 'IST'
    return f'{start.strftime("%d %b %y, %H:%M")} {tz}'


def _contest_duration_format(contest):
    duration_days, duration_hrs, duration_mins, _ = cf_common.time_format(contest.durationSeconds)
    duration = f'{duration_hrs}h {duration_mins}m'
    if duration_days > 0:
        duration = f'{duration_days}d ' + duration
    return duration


def _get_formatted_contest_desc(id_str, start, duration, url, max_duration_len):
    em = '\N{EN SPACE}'
    sq = '\N{WHITE SQUARE WITH UPPER RIGHT QUADRANT}'
    desc = (f'`{em}{id_str}{em}|'
            f'{em}{start}{em}|'
            f'{em}{duration.rjust(max_duration_len, em)}{em}|'
            f'{em}`[`link {sq}`]({url} "Link to contest page")')
    return desc


def _get_embed_fields_from_contests(contests):
    infos = [(contest.name, str(contest.id), _contest_start_time_format(contest, dt.timezone.utc),
              _contest_duration_format(contest), contest.register_url)
             for contest in contests]

    max_duration_len = max(len(duration) for _, _, _, duration, _ in infos)

    fields = []
    for name, id_str, start, duration, url in infos:
        value = _get_formatted_contest_desc(id_str, start, duration, url, max_duration_len)
        fields.append((name, value))
    return fields


def _get_ongoing_vc_participants():
    """ Returns a set containing the `member_id`s of users who are registered in an ongoing vc.
    """
    ongoing_vc_ids = cf_common.user_db.get_ongoing_rated_vc_ids()
    ongoing_vc_participants = set()
    for vc_id in ongoing_vc_ids:
        vc_participants = set(cf_common.user_db.get_rated_vc_user_ids(vc_id))
        ongoing_vc_participants |= vc_participants
    return ongoing_vc_participants

def discord_color_to_hex(color):
    h = str(hex(color))
    h = h[2:]
    return ('0'*(6-len(h)))+h

def rating_to_color(rating):
    """returns (r, g, b) pixels values corresponding to rating"""
    rank = cf.rating2rank(rating)
    h = discord_color_to_hex(rank.color_embed)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def get_leaderboard_image(rows, font):
    """return PIL image for rankings"""
    SMOKE_WHITE = (250, 250, 250)
    BLACK = (0, 0, 0)
    img = Image.new('RGB', (900, 450), color=SMOKE_WHITE)
    draw = ImageDraw.Draw(img)

    START_X, START_Y = 20, 20
    Y_INC = 32
    WIDTH_RANK = 64
    WIDTH_NAME = 340

    def draw_row(pos, username, handle, rating, color, y):
        x = START_X
        draw.text((x, y), pos, fill=color, font=font)
        x += WIDTH_RANK
        draw.text((x, y), username, fill=color, font=font)
        x += WIDTH_NAME
        draw.text((x, y), handle, fill=color, font=font)
        x += WIDTH_NAME
        draw.text((x, y), rating, fill=color, font=font)

    y = START_Y
    # draw header
    draw_row('#', 'Username', 'Problems (Rating)', 'Points', BLACK, y)
    y += int(Y_INC * 1.5)

    # trim name to fit in the column width
    def _trim(name):
        width = WIDTH_NAME - 10
        while font.getsize(name)[0] > width:
            name = name[:-4] + '...'  # "…" is printed as floating dots
        return name

    for pos, name, handle, rating, points in rows:
        name = _trim(name)
        handle = _trim(handle)
        color = rating_to_color(rating)
        draw_row(str(pos), name, handle, str(points) if points else 'N/A', color, y)
        if rating and rating >= 3000:  # nutella
            nutella_x = START_X + WIDTH_RANK
            draw.text((nutella_x, y), name[0], fill=BLACK, font=font)
            nutella_x += WIDTH_NAME
            draw.text((nutella_x, y), handle[0], fill=BLACK, font=font)
        y += Y_INC

    return img

class ActivitiesCogError(commands.CommandError):
    pass

class Activities(commands.Cog, description = "Analyzing activities with graphs and ranklists"):
    def __init__(self, bot):
        self.bot = bot
        self.member_converter = commands.MemberConverter()
        self.role_converter = commands.RoleConverter()

        self.future_contests = None
        self.active_contests = None
        self.finished_contests = None
        self.start_time_map = defaultdict(list)
        self.task_map = defaultdict(list)

        self.font = ImageFont.truetype(constants.NOTO_SANS_CJK_BOLD_FONT_PATH, size=26)
        self.logger = logging.getLogger(self.__class__.__name__)

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        pass

    @commands.slash_command(description='List solved CodeForces problems')
    async def stalk(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Print problems solved by user sorted by time (default) or rating.
        All submission types are included by default (practice, contest, etc.)

        Parameters
        ----------
        handles: CodeForces handles (separated by spaces)
        args: (+hardest) (+practice) (+contest) (+tags...) (r>=rating) (r<=rating)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """

        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (hardest,), args = cf_common.filter_flags(args, ['+hardest'])
        filt = cf_common.SubFilter(False)
        args = filt.parse(args)
        handles = args or ('!' + str(inter.author),)
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
        submissions = [await cf.user.status(handle=handle) for handle in handles]
        submissions = [sub for subs in submissions for sub in subs]
        submissions = filt.filter_subs(submissions)

        if not submissions:
            return await inter.edit_original_message('Submissions not found within the search parameters')

        if hardest:
            submissions.sort(key=lambda sub: (sub.problem.rating or 0, sub.creationTimeSeconds), reverse=True)
        else:
            submissions.sort(key=lambda sub: sub.creationTimeSeconds, reverse=True)

        def make_line(sub):
            data = (f'[{sub.problem.name}]({sub.problem.url})',
                    f'[{sub.problem.rating if sub.problem.rating else "?"}]',
                    f'({cf_common.days_ago(sub.creationTimeSeconds)})')
            return '\N{EN SPACE}'.join(data)

        def make_page(chunk):
            title = '{} solved problems by `{}`'.format('Hardest' if hardest else 'Recently',
                                                        '`, `'.join(handles))
            hist_str = '\n'.join(make_line(sub) for sub in chunk)
            embed = discord_common.cf_color_embed(description=hist_str)
            return title, embed

        pages = [make_page(chunk) for chunk in paginator.chunkify(submissions[:100], 10)]
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message = await inter.original_message(),
                           wait_time=5 * 60, set_pagenum_footers=True)

    @commands.cooldown(1, 120, type = commands.BucketType.guild)
    @commands.slash_command(description='Show weekly leaderboard')
    async def leaderboard(self, inter):
        await inter.response.send_message('This may take a while...')
        handles = {handle for discord_id, handle
                            in cf_common.user_db.get_handles_for_guild(inter.guild.id)}
        date = dt.datetime.now()-dt.timedelta(days=7)
        filt = cf_common.SubFilter(False)
        filt.parse('');
        filt.dlo = date.timestamp()
        rows = []
        i = 1
        for handle in handles:
            user = cf_common.user_db.fetch_cf_user(handle)
            submissions = await cf.user.status(handle=handle)
            submissions = filt.filter_subs(submissions)
            points = 0
            problemCount = 0
            averageRating = 0
            for submission in submissions:
                if submission.problem.rating is None:
                    continue
                problemCount += 1
                averageRating += submission.problem.rating
                points += (submission.problem.rating//100)-7
            if points > 5:
                averageRating /= problemCount
                mod = averageRating%100
                averageRating = (averageRating//100)*100
                averageRating += 100 if mod>50 else 0 
                rows.append([i, user.handle, str(problemCount)+" ("+str(int(averageRating))+")", user.rating, points])
            i += 1
        rows.sort(key=lambda row: row[4], reverse=True)
        for i in range(len(rows)):
            row = rows[i]
            row[0] = i+1 
            rows[i] = tuple(row)

        rows_to_display = rows[: min(10, len(rows))]
        img = get_leaderboard_image(rows_to_display, self.font)
        buffer = io.BytesIO()
        img.save(buffer, 'png')
        buffer.seek(0)
        msg = "Weekly Leaderboard"
        await inter.edit_original_message(msg, file=disnake.File(buffer, 'leaderboard_p1.png'))

    @commands.slash_command(description='Graphs for analyzing activities')
    async def plot(self, inter):
        """Plot various graphs. Wherever handles are accepted it is possible to use a server member's name instead by prefixing it with '!', for name with spaces use "!{name with spaces}" (with quotes)."""
        pass
    
    @plot.sub_command(description='Show speed of solving problems by rating')
    async def speed(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Plot average time spent on problems of particular rating during contest.

        Parameters
        ----------
        handles: CodeForces handles (separated by spaces)
        args: (+contest) (+virtual) (+scatter) (r>=rating) (r<=rating)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (add_scatter,), args = cf_common.filter_flags(args, ['+scatter'])
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        if 'PRACTICE' in filt.types:
            filt.types.remove('PRACTICE')  # can't estimate time for practice submissions

        handles, point_size = [], 3
        for arg in args:
            if arg[0:2] == 's=':
                point_size = int(arg[2:])
            else:
                handles.append(arg)

        handles = handles or ['!' + str(inter.author)]
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        plt.clf()
        plt.xlabel('Rating')
        plt.ylabel('Minutes spent')

        max_time = 0  # for ylim

        for submissions in all_solved_subs:
            scatter_points = []  # only matters if +scatter

            solved_by_contest = collections.defaultdict(lambda: [])
            for submission in submissions:
                # [solve_time, problem rating, problem index] for each solved problem
                solved_by_contest[submission.contestId].append([
                    submission.relativeTimeSeconds,
                    submission.problem.rating,
                    submission.problem.index
                ])

            time_by_rating = collections.defaultdict(lambda: [])
            for events in solved_by_contest.values():
                events.sort()
                solved_subproblems = dict()
                last_ac_time = 0

                for (current_ac_time, rating, problem_index) in events:
                    time_to_solve = current_ac_time - last_ac_time
                    last_ac_time = current_ac_time

                    # if there are subproblems, add total time for previous subproblems to current one
                    if len(problem_index) == 2 and problem_index[1].isdigit():
                        time_to_solve += solved_subproblems.get(problem_index[0], 0)
                        solved_subproblems[problem_index[0]] = time_to_solve

                    time_by_rating[rating].append(time_to_solve / 60)  # in minutes

            for rating in time_by_rating.keys():
                times = time_by_rating[rating]
                time_by_rating[rating] = sum(times) / len(times)
                if add_scatter:
                    for t in times:
                        scatter_points.append([rating, t])
                        max_time = max(max_time, t)

            xs = sorted(time_by_rating.keys())
            ys = [time_by_rating[rating] for rating in xs]

            max_time = max(max_time, max(ys, default=0))
            plt.plot(xs, ys)
            if add_scatter:
                plt.scatter(*zip(*scatter_points), s=point_size)

        labels = [gc.StrWrap(handle) for handle in handles]
        plt.legend(labels)
        plt.ylim(0, max_time + 5)

        # make xticks divisible by 100
        ticks = plt.gca().get_xticks()
        base = ticks[1] - ticks[0]
        plt.gca().get_xaxis().set_major_locator(MultipleLocator(base = max(base // 100 * 100, 100)))

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Plot of average time spent on a problem')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)

        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot CodeChef rating graph excluding long challenges')
    async def nolongrating(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+zoom) (+peak)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (zoom, peak), args = cf_common.filter_flags(args, ['+zoom' , '+peak'])
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        resource = 'codechef.com'
        resp = None
        if args:
            handles = args
            account_ids = await cf_common.resolve_handles(inter, self.member_converter, handles, resource=resource)
            data = dict()
            for change in await clist.fetch_rating_changes(account_ids):
                if change.handle in data:
                    data[change.handle].append(change)
                else:
                    data[change.handle] = [change,]
            resp = []
            months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
            for key in data:
                changes = data[key]
                filtered_changes = []
                current_rating = 1500
                for change in changes:
                    data = {'contestId':change.contestId, 'contestName':change.contestName, 'handle':change.handle, 'rank':change.rank, 
                            'ratingUpdateTimeSeconds':change.ratingUpdateTimeSeconds, 'oldRating':change.oldRating, 'newRating':change.newRating}
                    if any([re.search(month+' Challenge ....', data['contestName']) for month in months]):
                        continue
                    performance = int(data['oldRating'])+4*(int(data['newRating'])-int(data['oldRating']))
                    delta = (performance-current_rating)//4
                    new_rating = current_rating+delta
                    data['oldRating'] = current_rating
                    data['newRating'] = new_rating
                    current_rating = new_rating
                    change = cf.make_from_dict(cf.RatingChange, data)
                    filtered_changes.append(change)
                resp.append(filtered_changes)
        else:
            handles = []
            account_id = cf_common.user_db.get_account_id(inter.author.id, inter.guild.id, resource)
            if account_id!=None:
                resp = [await clist.fetch_rating_changes([account_id])]
                handles.append(inter.author.display_name)
            else:
                raise cf_common.HandleNotRegisteredError(inter.author, resource)

        resp = [filt.filter_rating_changes(rating_changes) for rating_changes in resp]

        def max_prefix(user):
            max_rate = 0
            res = []
            for data in user:
                old_rating = data.oldRating
                if old_rating == 0:
                    old_rating = 1500
                if data.newRating - old_rating >= 0 and data.newRating >= max_rate:
                    max_rate = data.newRating
                    res.append(data)
            return(res)

        if peak:
            resp = [max_prefix(user) for user in resp]

        plt.clf()
        plt.axes().set_prop_cycle(gc.rating_color_cycler)
        _plot_rating(resp, resource=resource)
        current_ratings = [rating_changes[-1].newRating if rating_changes else 'Unrated' for rating_changes in resp]
        handles = [rating_changes[-1].handle for rating_changes in resp]
        labels = [gc.StrWrap(f'{handle} ({rating})') for handle, rating in zip(handles, current_ratings)]
        plt.legend(labels, loc='upper left')

        if not zoom:
            min_rating = 1100
            max_rating = 1800
            for rating_changes in resp:
                for rating in rating_changes:
                    min_rating = min(min_rating, rating.newRating)
                    max_rating = max(max_rating, rating.newRating)
            plt.ylim(min_rating - 100, max_rating + 200)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Rating graph on '+resource)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot rating graph')
    async def rating(self, inter, handles: str = None, resource: commands.option_enum(["Codeforces", "Codechef", "Atcoder"]) = "Codeforces", args: str = "", since: str = None, before: str = None):
        """
        Plots rating graph for the handles provided.

        Parameters
        ----------
        handles: List of handles separated by spaces
        resource: Competitive Programming platform (default is CodeForces)
        args: (+zoom) (+peak)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        handles += " " + resource.lower()
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (zoom, peak), args = cf_common.filter_flags(args, ['+zoom' , '+peak'])
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        resource = 'codeforces.com'
        for key in _CLIST_RESOURCE_SHORT_FORMS:
            if key in args:
                args.remove(key)
                resource = _CLIST_RESOURCE_SHORT_FORMS[key]
        for key in _SUPPORTED_CLIST_RESOURCES:
            if key in args:
                args.remove(key)
                resource = key
        resp = None
        if resource=='codeforces.com':
            handles = args or ('!' + str(inter.author),)
            handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
            resp = [await cf.user.rating(handle=handle) for handle in handles]
            if not any(resp):
                handles_str = ', '.join(f'`{handle}`' for handle in handles)
                if len(handles) == 1:
                    message = f'User {handles_str} is not rated'
                else:
                    message = f'None of the given users {handles_str} are rated'
                raise ActivitiesCogError(message)
        else:
            if resource not in ['codechef.com', 'atcoder.jp']:
                raise ActivitiesCogError('You cannot plot rating of '+resource+' as of now')
            if args:
                handles = args
                account_ids = await cf_common.resolve_handles(inter, self.member_converter, handles, resource=resource)
                data = dict()
                for change in await clist.fetch_rating_changes(account_ids):
                    if change.handle in data:
                        data[change.handle].append(change)
                    else:
                        data[change.handle] = [change,]
                resp = []
                for key in data:
                    resp.append(data[key])
            else:
                handles = []
                account_id = cf_common.user_db.get_account_id(inter.author.id, inter.guild.id, resource)
                if account_id!=None:
                    resp = [await clist.fetch_rating_changes([account_id])]
                    handles.append(inter.author.display_name)
                else:
                    raise cf_common.HandleNotRegisteredError(inter.author, resource)

        resp = [filt.filter_rating_changes(rating_changes) for rating_changes in resp]

        def max_prefix(user):
            max_rate = 0
            res = []
            for data in user:
                old_rating = data.oldRating
                if old_rating == 0:
                    old_rating = 1500
                if data.newRating - old_rating >= 0 and data.newRating >= max_rate:
                    max_rate = data.newRating
                    res.append(data)
            return(res)

        if peak:
            resp = [max_prefix(user) for user in resp]

        plt.clf()
        plt.axes().set_prop_cycle(gc.rating_color_cycler)
        _plot_rating(resp, resource=resource)
        current_ratings = [rating_changes[-1].newRating if rating_changes else 'Unrated' for rating_changes in resp]
        if resource!='codeforces.com':
            handles = [rating_changes[-1].handle for rating_changes in resp]
        labels = [gc.StrWrap(f'{handle} ({rating})') for handle, rating in zip(handles, current_ratings)]
        plt.legend(labels, loc='upper left')

        if not zoom:
            min_rating = 1100
            max_rating = 1800
            for rating_changes in resp:
                for rating in rating_changes:
                    min_rating = min(min_rating, rating.newRating)
                    max_rating = max(max_rating, rating.newRating)
            plt.ylim(min_rating - 100, max_rating + 200)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Rating graph on '+resource)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot performance graph')
    async def performance(self, inter, handles: str = None, resource: commands.option_enum(["Codeforces", "Codechef", "Atcoder"]) = "Codeforces", args: str = "", since: str = None, before: str = None):
        """
        Plots performance graph for the handles provided.

        Parameters
        ----------
        handles: List of handles separated by spaces
        resource: Competitive Programming platform (default is CodeForces)
        args: (+zoom)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        handles += " " + resource.lower()
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (zoom,), args = cf_common.filter_flags(args, ['+zoom'])
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        resource = 'codeforces.com'
        for key in _CLIST_RESOURCE_SHORT_FORMS:
            if key in args:
                args.remove(key)
                resource = _CLIST_RESOURCE_SHORT_FORMS[key]
        for key in _SUPPORTED_CLIST_RESOURCES:
            if key in args:
                args.remove(key)
                resource = key
        resp = None
        if resource=='codeforces.com':
            handles = args or ('!' + str(inter.author),)
            handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
            resp = [await cf.user.rating(handle=handle) for handle in handles]
            if not any(resp):
                handles_str = ', '.join(f'`{handle}`' for handle in handles)
                if len(handles) == 1:
                    message = f'User {handles_str} is not rated'
                else:
                    message = f'None of the given users {handles_str} are rated'
                raise ActivitiesCogError(message)
        else:
            if resource not in ['codechef.com', 'atcoder.jp']:
                raise ActivitiesCogError('You cannot plot performance of '+resource+' as of now')
            if args:
                handles = args
                account_ids = await cf_common.resolve_handles(inter, self.member_converter, handles, resource=resource)
                data = dict()
                for change in await clist.fetch_rating_changes(account_ids, resource=='atcoder.jp'):
                    if change.handle in data:
                        data[change.handle].append(change)
                    else:
                        data[change.handle] = [change,]
                resp = []
                for key in data:
                    resp.append(data[key])
            else:
                handles = []
                account_id = cf_common.user_db.get_account_id(inter.author.id, inter.guild.id, resource)
                if account_id!=None:
                    resp = [await clist.fetch_rating_changes([account_id],  resource=='atcoder.jp')]
                    handles.append(inter.author.display_name)
                else:
                    raise cf_common.HandleNotRegisteredError(inter.author, resource)
        # extract last rating before corrections
        current_ratings = [rating_changes[-1].newRating if rating_changes else 'Unrated' for rating_changes in resp]
        if resource!='codeforces.com':
            handles = [rating_changes[-1].handle for rating_changes in resp]
        resp = cf.user.correct_rating_changes(resp=resp, resource=resource)
        resp = [filt.filter_rating_changes(rating_changes) for rating_changes in resp]
        
        if not any(resp):
            handles_str = ', '.join(f'`{handle}`' for handle in handles)
            if len(handles) == 1:
                message = f'User {handles_str} is not rated'
            else:
                message = f'None of the given users {handles_str} are rated'
            raise ActivitiesCogError(message)

        plt.clf()
        plt.axes().set_prop_cycle(gc.rating_color_cycler)
        _plot_perf(resp, resource=resource)
        labels = [gc.StrWrap(f'{handle} ({rating})') for handle, rating in zip(handles, current_ratings)]
        plt.legend(labels, loc='upper left')

        if not zoom:
            min_rating = 1100
            max_rating = 1800
            for rating_changes in resp:
                for rating in rating_changes:
                    min_rating = min(min_rating, rating.oldRating)
                    max_rating = max(max_rating, rating.oldRating)
            plt.ylim(min_rating - 100, max_rating + 200)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Performance graph on '+resource)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot Codeforces extremes graph')
    async def extreme(self, inter, handles: str = None, args: str = ""):
        """
        Plots pairs of lowest rated unsolved problem and highest rated solved problem for every
        contest that was rated for the given user.

        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+solved) (+unsolved) (+nolegend)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        args = tuple(handles.split() + args.split())
        handles = None

        (solved, unsolved, nolegend), args = cf_common.filter_flags(args, ['+solved', '+unsolved', '+nolegend'])
        legend, = cf_common.negate_flags(nolegend)
        if not solved and not unsolved:
            solved = unsolved = True

        handles = args or ('!' + str(inter.author),)
        handle, = await cf_common.resolve_handles(inter, self.member_converter, handles)
        ratingchanges = await cf.user.rating(handle=handle)
        if not ratingchanges:
            raise ActivitiesCogError(f'User {handle} is not rated')

        contest_ids = [change.contestId for change in ratingchanges]
        subs_by_contest_id = {contest_id: [] for contest_id in contest_ids}
        for sub in await cf.user.status(handle=handle):
            if sub.contestId in subs_by_contest_id:
                subs_by_contest_id[sub.contestId].append(sub)

        packed_contest_subs_problemset = [
            (cf_common.cache2.contest_cache.get_contest(contest_id),
             cf_common.cache2.problemset_cache.get_problemset(contest_id),
             subs_by_contest_id[contest_id])
            for contest_id in contest_ids
        ]

        rating = max(ratingchanges, key=lambda change: change.ratingUpdateTimeSeconds).newRating
        _plot_extreme(handle, rating, packed_contest_subs_problemset, solved, unsolved, legend)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Codeforces extremes graph')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description="Show histogram of solved problems' rating on CF")
    async def solved(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Shows a histogram of solved problems' rating on Codeforces for the handles provided.
        e.g. ;plot solved meooow +contest +virtual +outof +dp

        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+practice) (+contest) (+virtual) (+tags...) (r>=rating) (r<=rating)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handles = args or ('!' + str(inter.author),)
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise ActivitiesCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Problem rating')
        plt.ylabel('Number solved')
        if len(handles) == 1:
            # Display solved problem separately by type for a single user.
            handle, solved_by_type = handles[0], _classify_submissions(all_solved_subs[0])
            all_ratings = [[sub.problem.rating for sub in solved_by_type[sub_type]]
                           for sub_type in filt.types]

            nice_names = nice_sub_type(filt.types)
            labels = [name.format(len(ratings)) for name, ratings in zip(nice_names, all_ratings)]

            step = 100
            # shift the range to center the text
            hist_bins = list(range(filt.rlo - step // 2, filt.rhi + step // 2 + 1, step))
            plt.hist(all_ratings, stacked=True, bins=hist_bins, label=labels)
            total = sum(map(len, all_ratings))
            plt.legend(title=f'{handle}: {total}', title_fontsize=plt.rcParams['legend.fontsize'],
                       loc='upper right')

        else:
            all_ratings = [[sub.problem.rating for sub in solved_subs]
                           for solved_subs in all_solved_subs]
            labels = [gc.StrWrap(f'{handle}: {len(ratings)}')
                      for handle, ratings in zip(handles, all_ratings)]

            step = 200 if filt.rhi - filt.rlo > 3000 // len(handles) else 100
            hist_bins = list(range(filt.rlo - step // 2, filt.rhi + step // 2 + 1, step))
            plt.hist(all_ratings, bins=hist_bins)
            plt.legend(labels, loc='upper right')

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Histogram of problems solved on Codeforces')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Show histogram of solved problems on CF over time')
    async def hist(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Shows the histogram of problems solved on Codeforces over time for the handles provided
    
        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+practice) (+contest) (+virtual) (+tags...) (r>=rating) (r<=rating)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        filt = cf_common.SubFilter()
        args = filt.parse(args)
        phase_days = 1
        handles = []
        for arg in args:
            if arg[0:11] == 'phase_days=':
                phase_days = int(arg[11:])
            else:
                handles.append(arg)

        if phase_days < 1:
            raise ActivitiesCogError('Invalid parameters')
        phase_time = dt.timedelta(days=phase_days)

        handles = handles or ['!' + str(inter.author)]
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise ActivitiesCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Time')
        plt.ylabel('Number solved')
        if len(handles) == 1:
            handle, solved_by_type = handles[0], _classify_submissions(all_solved_subs[0])
            all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_by_type[sub_type]]
                         for sub_type in filt.types]

            nice_names = nice_sub_type(filt.types)
            labels = [name.format(len(times)) for name, times in zip(nice_names, all_times)]

            dlo = min(itertools.chain.from_iterable(all_times)).date()
            dhi = min(dt.datetime.today() + dt.timedelta(days=1), dt.datetime.fromtimestamp(filt.dhi)).date()
            phase_cnt = math.ceil((dhi - dlo) / phase_time)
            plt.hist(
                all_times,
                stacked=True,
                label=labels,
                range=(dhi - phase_cnt * phase_time, dhi),
                bins=min(40, phase_cnt))

            total = sum(map(len, all_times))
            plt.legend(title=f'{handle}: {total}', title_fontsize=plt.rcParams['legend.fontsize'])
        else:
            all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_subs]
                         for solved_subs in all_solved_subs]

            # NOTE: matplotlib ignores labels that begin with _
            # https://matplotlib.org/api/pyplot_api.html#matplotlib.pyplot.legend
            # Add zero-width space to work around this
            labels = [gc.StrWrap(f'{handle}: {len(times)}')
                      for handle, times in zip(handles, all_times)]

            dlo = min(itertools.chain.from_iterable(all_times)).date()
            dhi = min(dt.datetime.today() + dt.timedelta(days=1), dt.datetime.fromtimestamp(filt.dhi)).date()
            phase_cnt = math.ceil((dhi - dlo) / phase_time)
            plt.hist(
                all_times,
                range=(dhi - phase_cnt * phase_time, dhi),
                bins=min(40 // len(handles), phase_cnt))
            plt.legend(labels)

        # NOTE: In case of nested list, matplotlib decides type using 1st sublist,
        # it assumes float when 1st sublist is empty.
        # Hence explicitly assigning locator and formatter is must here.
        locator = mdates.AutoDateLocator()
        plt.gca().xaxis.set_major_locator(locator)
        plt.gca().xaxis.set_major_formatter(mdates.AutoDateFormatter(locator))

        plt.gcf().autofmt_xdate()
        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Histogram of number of solved problems over time')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot count of solved CF problems over time')
    async def curve(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Plots the count of problems solved over time on Codeforces for the handles provided.

        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+practice) (+contest) (+virtual) (+outof) (+tags...) (r>=rating) (r<=rating)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handles = args or ('!' + str(inter.author),)
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles)
        resp = [await cf.user.status(handle=handle) for handle in handles]
        all_solved_subs = [filt.filter_subs(submissions) for submissions in resp]

        if not any(all_solved_subs):
            raise ActivitiesCogError(f'There are no problems within the specified parameters.')

        plt.clf()
        plt.xlabel('Time')
        plt.ylabel('Cumulative solve count')

        all_times = [[dt.datetime.fromtimestamp(sub.creationTimeSeconds) for sub in solved_subs]
                     for solved_subs in all_solved_subs]
        for times in all_times:
            cumulative_solve_count = list(range(1, len(times)+1)) + [len(times)]
            timestretched = times + [min(dt.datetime.now(), dt.datetime.fromtimestamp(filt.dhi))]
            plt.plot(timestretched, cumulative_solve_count)

        labels = [gc.StrWrap(f'{handle}: {len(times)}')
                  for handle, times in zip(handles, all_times)]

        plt.legend(labels)

        plt.gcf().autofmt_xdate()
        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Curve of number of solved problems over time')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Show history of problems solved by rating')
    async def scatter(self, inter, handles: str = None, args: str = "", since: str = None, before: str = None):
        """
        Plot Codeforces rating overlaid on a scatter plot of problems solved.
        Also plots a running average of ratings of problems solved in practice.

        Parameters
        ----------
        handles: Codechef handles (separated by spaces)
        args: (+practice) (+contest) (+virtual) (+tags...) (r>=rating) (r<=rating) (+nolegend)
        since: The first day to be counted (e.g: 13 02 2004)
        before: The last day to be counted (e.g: 13 02 2004)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        if since != None: args += " d>=" + since.replace(" ", "")
        if before != None: args += " d<" + before.replace(" ", "")
        args = tuple(handles.split() + args.split())
        handles = None

        (nolegend,), args = cf_common.filter_flags(args, ['+nolegend'])
        legend, = cf_common.negate_flags(nolegend)
        filt = cf_common.SubFilter()
        args = filt.parse(args)
        handle, bin_size, point_size = None, 10, 3
        for arg in args:
            if arg[0:2] == 'b=':
                bin_size = int(arg[2:])
            elif arg[0:2] == 's=':
                point_size = int(arg[2:])
            else:
                if handle:
                    raise ActivitiesCogError('Only one handle allowed.')
                handle = arg

        if bin_size < 1 or point_size < 1 or point_size > 100:
            raise ActivitiesCogError('Invalid parameters')

        handle = handle or '!' + str(inter.author)
        handle, = await cf_common.resolve_handles(inter, self.member_converter, (handle,))
        rating_resp = [await cf.user.rating(handle=handle)]
        rating_resp = [filt.filter_rating_changes(rating_changes) for rating_changes in rating_resp]
        submissions = filt.filter_subs(await cf.user.status(handle=handle))

        def extract_time_and_rating(submissions):
            return [(dt.datetime.fromtimestamp(sub.creationTimeSeconds), sub.problem.rating)
                    for sub in submissions]

        if not any(submissions):
            raise ActivitiesCogError(f'No submissions for user `{handle}`')

        solved_by_type = _classify_submissions(submissions)
        regular = extract_time_and_rating(solved_by_type['CONTESTANT'] +
                                          solved_by_type['OUT_OF_COMPETITION'])
        practice = extract_time_and_rating(solved_by_type['PRACTICE'])
        virtual = extract_time_and_rating(solved_by_type['VIRTUAL'])

        plt.clf()
        _plot_scatter(regular, practice, virtual, point_size)
        labels = []
        if practice:
            labels.append('Practice')
        if regular:
            labels.append('Regular')
        if virtual:
            labels.append('Virtual')
        if legend:
            plt.legend(labels, loc='upper left')
        _plot_average(practice, bin_size)
        _plot_rating(rating_resp, mark='')

        # zoom
        ymin, ymax = plt.gca().get_ylim()
        plt.ylim(max(ymin, filt.rlo - 100), min(ymax, filt.rhi + 100))

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title=f'Rating vs solved problem rating for {handle}')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Show server rating distribution')
    async def distrib(self, inter):
        """Plots rating distribution of users in this server"""
        await inter.response.defer()

        res = cf_common.user_db.get_cf_users_for_guild(inter.guild.id)
        ratings = [cf_user.rating for user_id, cf_user in res
                   if cf_user.rating is not None]

        binsize=100
        title='Rating distribution of server members'

        ratings = [r for r in ratings if r >= 0]
        assert ratings, 'Cannot histogram plot empty list of ratings'

        assert 100%binsize == 0 # because bins is semi-hardcoded
        bins = 39*100//binsize

        colors = []
        low, high = 0, binsize * bins
        for rank in cf.RATED_RANKS:
            for r in range(max(rank.low, low), min(rank.high, high), binsize):
                colors.append('#' + '%06x' % rank.color_embed)
        assert len(colors) == bins, f'Expected {bins} colors, got {len(colors)}'

        height = [0] * bins
        for r in ratings:
            height[r // binsize] += 1

        csum = 0
        users = sum(height)

        x = [k * binsize for k in range(bins)]
        label = [f'{r}' for r in x]

        l,r = 0,bins-1
        while not height[l]: l += 1
        while not height[r]: r -= 1
        x = x[l:r+1]
        label = label[l:r+1]
        colors = colors[l:r+1]
        height = height[l:r+1]

        plt.clf()
        fig = plt.figure(figsize=(15, 5))

        plt.xticks(rotation=45)
        plt.xlim(l * binsize - binsize//2, r * binsize + binsize//2)
        plt.bar(x, height, binsize*0.9, color=colors, linewidth=0, tick_label=label, log=False)
        plt.xlabel('Rating')
        plt.ylabel('Number of users')

        discord_file = gc.get_current_figure_as_file()
        plt.close(fig)

        embed = discord_common.cf_color_embed(title=title)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot histogram of gudgiting')
    async def howgud(self, inter, member: disnake.Member = None):
        """
        Parameters
        ----------
        member: Server member to plot histogram
        """
        await inter.response.defer()

        member = member or inter.author
        member = (member,)

        # shift the [-300, 500] gitgud range to center the text
        hist_bins = list(range(-300 - 50, 500 + 50 + 1, 100))
        deltas = [[x[0] for x in cf_common.user_db.howgud(member.id)] for member in member]
        labels = [gc.StrWrap(f'{member.display_name}: {len(delta)}')
                  for member, delta in zip(member, deltas)]

        plt.clf()
        plt.margins(x=0)
        plt.hist(deltas, bins=hist_bins, rwidth=1)
        plt.xlabel('Problem delta')
        plt.ylabel('Number solved')
        plt.legend(labels, prop=gc.fontprop)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='Histogram of gudgitting')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Plot distribution of server members by country')
    async def country(self, inter, countries: str = ""):
        """
        Plots distribution of server members by countries. When no countries are specified, plots a bar graph of all members by country. When one or more countries are specified, plots a swarmplot of members by country and rating. Only members with registered handles and countries set on Codeforces are considered.

        Parameters
        ----------
        countries: e.g: "vietnam; united states" (without quotes)
        """
        await inter.response.defer()

        countries = countries.split(';')
        countries = [cf_common.reformat_country_name(country) for country in countries if country != ""]

        if len(countries) > 8:
            raise ActivitiesCogError(f'At most 8 countries may be specified.')

        users = cf_common.user_db.get_cf_users_for_guild(inter.guild.id)
        counter = collections.Counter(user.country for _, user in users if user.country)

        if not countries:
            # list because seaborn complains for tuple.
            countries, counts = map(list, zip(*counter.most_common()))
            plt.clf()
            fig = plt.figure(figsize=(15, 5))
            with sns.axes_style(rc={'xtick.bottom': True}):
                sns.barplot(x=countries, y=counts)

            # Show counts on top of bars.
            ax = plt.gca()
            for p in ax.patches:
                x = p.get_x() + p.get_width() / 2
                y = p.get_y() + p.get_height() + 0.5
                ax.text(x, y, int(p.get_height()), horizontalalignment='center', color='#30304f',
                        fontsize='x-small')

            plt.xticks(rotation=40, horizontalalignment='right')
            ax.tick_params(axis='x', length=4, color=ax.spines['bottom'].get_edgecolor())
            plt.xlabel('Country')
            plt.ylabel('Number of members')
            discord_file = gc.get_current_figure_as_file()
            plt.close(fig)
            embed = discord_common.cf_color_embed(title='Distribution of server members by country')
        else:
            countries = [country.title() for country in countries]
            data = [[user.country, user.rating]
                    for _, user in users if user.rating and user.country and user.country in countries]
            if not data:
                raise ActivitiesCogError('No rated members from the specified countries are present.')

            color_map = {rating: f'#{cf.rating2rank(rating).color_embed:06x}' for _, rating in data}
            df = pd.DataFrame(data, columns=['Country', 'Rating'])
            column_order = sorted((country for country in countries if counter[country]),
                                  key=counter.get, reverse=True)
            plt.clf()
            if len(column_order) <= 5:
                sns.swarmplot(x='Country', y='Rating', hue='Rating', data=df, order=column_order,
                              palette=color_map)
            else:
                # Add ticks and rotate tick labels to avoid overlap.
                with sns.axes_style(rc={'xtick.bottom': True}):
                    sns.swarmplot(x='Country', y='Rating', hue='Rating', data=df,
                                  order=column_order, palette=color_map)
                plt.xticks(rotation=30, horizontalalignment='right')
                ax = plt.gca()
                ax.tick_params(axis='x', color=ax.spines['bottom'].get_edgecolor())
            plt.legend().remove()
            plt.xlabel('Country')
            plt.ylabel('Rating')
            discord_file = gc.get_current_figure_as_file()
            embed = discord_common.cf_color_embed(title='Rating distribution of server members by '
                                                        'country')

        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @plot.sub_command(description='Show rating changes by rank')
    async def visualrank(self, inter, contest_id: int, handles: str = None, args: str = ""):
        """
        Plot rating changes by rank. Add handles to specify a handle in the plot.
        If arguments contains `+server`, it will include just server members and not all codeforces users.
        Specify `+zoom` to zoom to the neighborhood of handles.

        Parameters
        ----------
        contest_id: ID of the contest to plot visualrank
        handles: Codechef handles to be specified in the plot (separated by spaces)
        args: (+server) (+zoom)
        """
        await inter.response.defer()

        if handles == None:
            handles = '!' + str(inter.author)
        args = tuple(handles.split() + args.split())
        handles = None

        args = set(args)
        (in_server, zoom), handles = cf_common.filter_flags(args, ['+server', '+zoom'])
        handles = await cf_common.resolve_handles(inter, self.member_converter, handles, mincnt=0, maxcnt=20)

        rating_changes = await cf.contest.ratingChanges(contest_id=contest_id)
        if in_server:
            guild_handles = set(handle for discord_id, handle
                                in cf_common.user_db.get_handles_for_guild(inter.guild.id))
            rating_changes = [rating_change for rating_change in rating_changes
                              if rating_change.handle in guild_handles or rating_change.handle in handles]

        if not rating_changes:
            raise ActivitiesCogError(f'No rating changes for contest `{contest_id}`')

        users_to_mark = {}
        for rating_change in rating_changes:
            user_delta = rating_change.newRating - rating_change.oldRating
            if rating_change.handle in handles:
                users_to_mark[rating_change.handle] = (rating_change.rank, user_delta)

        ymargin = 50
        xmargin = 50
        if users_to_mark and zoom:
            xmin = min(point[0] for point in users_to_mark.values())
            xmax = max(point[0] for point in users_to_mark.values())
            ymin = min(point[1] for point in users_to_mark.values())
            ymax = max(point[1] for point in users_to_mark.values())
        else:
            ylim = 0
            if users_to_mark:
                ylim = max(abs(point[1]) for point in users_to_mark.values())
            ylim = max(ylim, 200)

            xmin = 0
            xmax = max(rating_change.rank for rating_change in rating_changes)
            ymin = -ylim
            ymax = ylim

        ranks = []
        delta = []
        color = []
        for rating_change in rating_changes:
            user_delta = rating_change.newRating - rating_change.oldRating

            if (xmin - xmargin <= rating_change.rank <= xmax + xmargin
                    and ymin - ymargin <= user_delta <= ymax + ymargin):
                ranks.append(rating_change.rank)
                delta.append(user_delta)
                color.append(cf.rating2rank(rating_change.oldRating).color_graph)

        title = rating_changes[0].contestName

        plt.clf()
        fig = plt.figure(figsize=(12, 8))
        plt.title(title)
        plt.xlabel('Rank')
        plt.ylabel('Rating Changes')

        mark_size = 2e4 / len(ranks)
        plt.xlim(xmin - xmargin, xmax + xmargin)
        plt.ylim(ymin - ymargin, ymax + ymargin)
        plt.scatter(ranks, delta, s=mark_size, c=color)

        for handle, point in users_to_mark.items():
            plt.annotate(handle,
                         xy=point,
                         xytext=(0, 0),
                         textcoords='offset points',
                         ha='left',
                         va='bottom',
                         fontsize='large')
            plt.plot(*point,
                     marker='o',
                     markersize=5,
                     color='black')

        discord_file = gc.get_current_figure_as_file()
        plt.close(fig)

        embed = discord_common.cf_color_embed(title=title)
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, inter.author)
        await inter.edit_original_message(embed=embed, file=discord_file)

    @staticmethod
    def _make_contest_pages(contests, title):
        pages = []
        chunks = paginator.chunkify(contests, _CONTESTS_PER_PAGE)
        for chunk in chunks:
            embed = discord_common.cf_color_embed()
            for name, value in _get_embed_fields_from_contests(chunk):
                embed.add_field(name=name, value=value, inline=False)
            pages.append((title, embed))
        return pages

    @staticmethod
    def _get_cf_or_ioi_standings_table(problem_indices, handle_standings, deltas=None, *, mode):
        assert mode in ('cf', 'ioi')

        def maybe_int(value):
            return int(value) if mode == 'cf' else value

        header_style = '{:>} {:<}    {:^}  ' + '  '.join(['{:^}'] * len(problem_indices))
        body_style = '{:>} {:<}    {:>}  ' + '  '.join(['{:>}'] * len(problem_indices))
        header = ['#', 'Handle', '='] + problem_indices
        if deltas:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']

        body = []
        for handle, standing in handle_standings:
            virtual = '#' if standing.party.participantType == 'VIRTUAL' else ''
            tokens = [standing.rank, handle + ':' + virtual, maybe_int(standing.points)]
            for problem_result in standing.problemResults:
                score = ''
                if problem_result.points:
                    score = str(maybe_int(problem_result.points))
                tokens.append(score)
            body.append(tokens)

        if deltas:
            for tokens, delta in zip(body, deltas):
                tokens.append('' if delta is None else f'{delta:+}')
        return header_style, body_style, header, body

    @staticmethod
    def _get_icpc_standings_table(problem_indices, handle_standings, deltas=None):
        header_style = '{:>} {:<}    {:^}  {:^}  ' + '  '.join(['{:^}'] * len(problem_indices))
        body_style = '{:>} {:<}    {:>}  {:>}  ' + '  '.join(['{:<}'] * len(problem_indices))
        header = ['#', 'Handle', '=', '-'] + problem_indices
        if deltas:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']

        body = []
        for handle, standing in handle_standings:
            virtual = '#' if standing.party.participantType == 'VIRTUAL' else ''
            tokens = [standing.rank, handle + ':' + virtual, int(standing.points), int(standing.penalty)]
            for problem_result in standing.problemResults:
                score = '+' if problem_result.points else ''
                if problem_result.rejectedAttemptCount:
                    penalty = str(problem_result.rejectedAttemptCount)
                    if problem_result.points:
                        score += penalty
                    else:
                        score = '-' + penalty
                tokens.append(score)
            body.append(tokens)

        if deltas:
            for tokens, delta in zip(body, deltas):
                tokens.append('' if delta is None else f'{delta:+}')
        return header_style, body_style, header, body

    def _make_standings_pages(self, contest, problem_indices, handle_standings, deltas=None):
        pages = []
        handle_standings_chunks = paginator.chunkify(handle_standings, _STANDINGS_PER_PAGE)
        num_chunks = len(handle_standings_chunks)
        delta_chunks = paginator.chunkify(deltas, _STANDINGS_PER_PAGE) if deltas else [None] * num_chunks

        if contest.type == 'CF':
            get_table = functools.partial(self._get_cf_or_ioi_standings_table, mode='cf')
        elif contest.type == 'ICPC':
            get_table = self._get_icpc_standings_table
        elif contest.type == 'IOI':
            get_table = functools.partial(self._get_cf_or_ioi_standings_table, mode='ioi')
        else:
            assert False, f'Unexpected contest type {contest.type}'

        num_pages = 1
        for handle_standings_chunk, delta_chunk in zip(handle_standings_chunks, delta_chunks):
            header_style, body_style, header, body = get_table(problem_indices,
                                                               handle_standings_chunk,
                                                               delta_chunk)
            t = table.Table(table.Style(header=header_style, body=body_style))
            t += table.Header(*header)
            t += table.Line('\N{EM DASH}')
            for row in body:
                t += table.Data(*row)
            t += table.Line('\N{EM DASH}')
            page_num_footer = f' # Page: {num_pages} / {num_chunks}' if num_chunks > 1 else ''

            # We use yaml to get nice colors in the ranklist.
            content = f'```yaml\n{t}\n{page_num_footer}```'
            pages.append((content, None))
            num_pages += 1

        return pages
    
    def _make_clist_standings_pages(self, standings, problemset=None, division=None):
        if standings is None or len(standings)==0:
            return "```No handles found inside ranklist```"
        show_rating_changes = False
        problems = []
        problem_indices = []
        if problemset:
            if division!=None:
                problemset = problemset['division'][division]
            for problem in problemset:
                if 'short' in problem:
                    short = problem['short']
                    if len(short)>3:
                        problem_indices = None
                    if problem_indices!=None:
                        problem_indices.append(short)
                    problems.append(short)
                elif 'code' in problem:
                    problem_indices = None
                    problems.append(problem['code'])
        for standing in standings:
            if not show_rating_changes and standing['rating_change']!=None:
                show_rating_changes = True
            if problemset is None and 'problems' in standing:
                for problem_key in standing['problems']:
                    if problem_key not in problems:
                        problems.append(problem_key)
        def maybe_int(value):
            if '.' not in str(value):
                return value
            try:
                return int(value)
            except:
                return value
        show_rating_changes = any([standing['rating_change']!=None for standing in standings])
        pages = []
        standings_chunks = paginator.chunkify(standings, _STANDINGS_PER_PAGE)
        num_chunks = len(standings_chunks)
        problem_indices = problem_indices or [chr(ord('A')+i) for i in range(len(problems))]
        header_style = '{:>} {:<}    {:^}  ' 
        body_style = '{:>} {:<}    {:>}  '
        header = ['#', 'Handle', '='] 
        header_style += '  '.join(['{:^}'] * len(problem_indices))
        body_style += '  '.join(['{:>}'] * len(problem_indices))
        header += problem_indices
        if show_rating_changes:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']
        
        num_pages = 1
        for standings_chunk in standings_chunks:
            body = []
            for standing in standings_chunk:
                score = int(standing['score']) if standing['score'] else ' '
                problem_results = [maybe_int(standing['problems'][problem_key]['result']) 
                                            if standing.get('problems', None) and standing['problems'].get(problem_key, None) and 
                                                    standing['problems'][problem_key].get('result', None) 
                                                        else ' ' for problem_key in problems]
                tokens = [int(standing['place']), standing['handle'], maybe_int(score)]
                tokens += problem_results
                if show_rating_changes:
                    delta = int(standing['rating_change']) if standing['rating_change'] else ' '
                    if delta!=' ':
                        delta = '+'+str(delta) if delta>0 else str(delta)
                    tokens += [delta]
                body.append(tokens)
            t = table.Table(table.Style(header=header_style, body=body_style))
            t += table.Header(*header)
            t += table.Line('\N{EM DASH}')
            for row in body:
                t += table.Data(*row)
            t += table.Line('\N{EM DASH}')
            page_num_footer = f' # Page: {num_pages} / {num_chunks}' if num_chunks > 1 else ''

            # We use yaml to get nice colors in the ranklist.
            content = f'```yaml\n{t}\n{page_num_footer}```'
            pages.append((content, None))
            num_pages += 1
        return pages

    @staticmethod
    def _make_contest_embed_for_ranklist(timezone:pytz.timezone, ranklist=None, contest=None, parsed_at=None):
        contest = ranklist.contest if ranklist else contest
        assert contest.phase != 'BEFORE', f'Contest {contest.id} has not started.'
        embed = discord_common.cf_color_embed(title=contest.name, url=contest.url)
        phase = contest.phase.capitalize().replace('_', ' ')
        embed.add_field(name='Phase', value=phase)
        if ranklist and ranklist.is_rated:
            embed.add_field(name='Deltas', value=ranklist.deltas_status)
        now = time.time()
        en = '\N{EN SPACE}'
        if contest.phase == 'CODING':
            elapsed = cf_common.pretty_time_format(now - contest.startTimeSeconds, shorten=True)
            remaining = cf_common.pretty_time_format(contest.end_time - now, shorten=True)
            msg = f'{elapsed} elapsed{en}|{en}{remaining} remaining'
            embed.add_field(name='Tick tock', value=msg, inline=False)
        else:
            start = _contest_start_time_format(contest, timezone)
            duration = _contest_duration_format(contest)
            since = cf_common.pretty_time_format(now - contest.end_time, only_most_significant=True)
            msg = f'{start}{en}|{en}{duration}{en}|{en}Ended {since} ago'
            embed.add_field(name='When', value=msg, inline=False)
        if parsed_at:
            parsed_at = parsed_at[:parsed_at.index('.')]
            since = cf_common.pretty_time_format(now - int(clist.time_in_seconds(parsed_at)), only_most_significant=True)
            embed.add_field(name='Updated', value=f'{since} ago')
        
        return embed

    @staticmethod
    def _make_contest_embed_for_vc_ranklist(ranklist, vc_start_time=None, vc_end_time=None):
        contest = ranklist.contest
        embed = discord_common.cf_color_embed(title=contest.name, url=contest.url)
        embed.set_author(name='VC Standings')
        now = time.time()
        if vc_start_time and vc_end_time:
            en = '\N{EN SPACE}'
            elapsed = cf_common.pretty_time_format(now - vc_start_time, shorten=True)
            remaining = cf_common.pretty_time_format(max(0,vc_end_time - now), shorten=True)
            msg = f'{elapsed} elapsed{en}|{en}{remaining} remaining'
            embed.add_field(name='Tick tock', value=msg, inline=False)
        return embed

    async def resolve_contest(self, contest_id, resource):
        contest = None
        if resource=='clist.by':
            contest = await clist.contest(contest_id, with_problems=True)
        elif resource=='atcoder.jp':
            prefix = contest_id[:3]
            if prefix=='abc':
                prefix = 'AtCoder Beginner Contest '
            if prefix=='arc':
                prefix = 'AtCoder Regular Contest '
            if prefix=='agc':
                prefix = 'AtCoder Grand Contest '
            suffix = contest_id[3:]
            try:
                suffix = int(suffix)
            except:
                raise ActivitiesCogError('Invalid contest_id provided.') 
            contest_name = prefix+str(suffix)
            contests = await clist.search_contest(regex=contest_name, resource=resource, with_problems=True)
            if contests==None or len(contests)==0:
                raise ActivitiesCogError('Contest not found.')
            contest = contests[0] 
        elif resource=='codechef.com':
            contest_name = None
            if 'lunchtime' in contest_id:
                date = parse_date(contest_id[9:])
                contest_name = str(date.strftime('%B'))+' Lunchtime '+str(date.strftime('%Y'))
            elif 'cookoff' in contest_id:
                date = parse_date(contest_id[7:])
                contest_name = str(date.strftime('%B'))+' Cook-Off '+str(date.strftime('%Y'))
            elif 'long' in contest_id:
                date = parse_date(contest_id[4:])
                contest_name = str(date.strftime('%B'))+' Challenge '+str(date.strftime('%Y'))
            elif 'starters' in contest_id:
                date = parse_date(contest_id[8:])
                contest_name = str(date.strftime('%B'))+' CodeChef Starters '+str(date.strftime('%Y'))
            contests = await clist.search_contest(regex=contest_name, resource=resource, with_problems=True)
            if contests==None or len(contests)==0:
                raise ActivitiesCogError('Contest not found.')
            contest = contests[0] 
        elif resource=='codingcompetitions.withgoogle.com' or resource=='facebook.com/hackercup':
            year,round = None,None
            contest_name = None
            if 'kickstart' in contest_id:
                year = contest_id[9:11]
                round = contest_id[11:]
                contest_name = 'Kick Start.*Round '+round
            elif 'codejam' in contest_id:
                year = contest_id[7:9]
                round = contest_id[9:]
                if round=='WF':
                    round = 'Finals'
                    contest_name = 'Code Jam.*Finals'
                elif round=='QR':
                    round = 'Qualification Round'
                    contest_name = 'Code Jam.*Qualification Round'
                else:
                    contest_name = 'Code Jam.*Round '+round
            elif 'hackercup' in contest_id:
                year = contest_id[9:11]
                round = contest_id[11:]
                if round=='WF':
                    round = 'Finals'
                    contest_name = 'Final Round '
                elif round=='QR':
                    round = 'Qualification Round'
                    contest_name = 'Qualification Round '
                else:
                    contest_name = 'Round '+round

            if not round:
                    raise ActivitiesCogError('Invalid contest_id provided.') 
            try:
                year = int(year)
            except:
                raise ActivitiesCogError('Invalid contest_id provided.') 
            start = dt.datetime(int('20'+str(year)), 1, 1)
            end = dt.datetime(int('20'+str(year+1)), 1, 1)
            date_limit = (start.strftime('%Y-%m-%dT%H:%M:%S'), end.strftime('%Y-%m-%dT%H:%M:%S'))
            contests = await clist.search_contest(regex=contest_name, resource=resource, date_limits=date_limit, with_problems=True)
            if contests==None or len(contests)==0:
                raise ActivitiesCogError('Contest not found.')
            contest = contests[0]
        else:
            contests = await clist.search_contest(regex=contest_id, with_problems=True, order_by='-start')
            if contests==None or len(contests)==0:
                raise ActivitiesCogError('Contest not found.')
            contest = contests[0]
            pass
        return contest

    @commands.slash_command(description='Show ranklist for given contest')
    async def ranklist(self, inter, contest_id: str, handles: str = ""):
        """Shows ranklist for the contest with given contest id/name.
        
        # For codeforces ranklist
        ;ranklist codeforces_contest_id

        # For codechef ranklist
        ;ranklist [long/lunchtime/cookoff][mm][yyyy]

        # For atcoder ranklist
        ;ranklist [abc/arc/agc][number]

        # For google and facebook ranklist
        ;ranklist [kickstart/codejam/hackercup][yy][round]
        Use QR for Qualification Round and WF for World Finals.

        Parameters
        ----------
        contest_id: [contest_name_regex / contest_id / -clist_contest_id]
        handles: List of CF handles to be appeared in the ranklist (separated by spaces)
        """

        await inter.response.defer()

        handles = tuple(handles.split())

        resource = 'codeforces.com'
        timezone = cf_common.user_db.get_guildtz(inter.guild.id)
        timezone = pytz.timezone(timezone or 'Asia/Kolkata')
        for pattern in _PATTERNS:
            if pattern in contest_id:
                resource = _PATTERNS[pattern]
                break
        if resource=='codeforces.com':
            try:
                contest_id = int(contest_id)
                if contest_id<0:
                    contest_id = -1*contest_id
                    resource = 'clist.by'
            except:
                resource = None
        if resource!='codeforces.com':
            contest = await self.resolve_contest(contest_id=contest_id, resource=resource)
            if contest is None:
                raise ActivitiesCogError('Contest not found.') 
            contest_id = contest['id']
            resource = contest['resource']
            parsed_at = contest.get('parsed_at', None);
            selected_divs = []
            handles = list(handles)
            if resource=='codechef.com':
                divs = {'+div1': 'div_1', '+div2': 'div_2', '+div3': 'div_3'}
                for div in divs.keys():
                    if div in handles:
                        handles.remove(div)
                        selected_divs.append(divs[div])
            account_ids = await cf_common.resolve_handles(inter, self.member_converter, handles, maxcnt=None, default_to_all_server=True, resource=contest['resource'])
            users = {}
            if resource=='codedrills.io':
                clist_users = await clist.fetch_user_info(resource, account_ids)
                for clist_user in clist_users:
                    users[clist_user['id']] = clist_user['name']
            standings_to_show = []
            standings = await clist.statistics(contest_id=contest_id, account_ids=account_ids, with_extra_fields=True, with_problems=True, order_by='place', limit=50)
            for standing in standings:
                if not standing['place'] or not standing['handle']:
                    continue
                if resource=='codedrills.io':
                    standing['handle'] = users[standing['account_id']] or ''
                elif resource=='facebook.com/hackercup':
                    more_fields = standing.get('more_fields')
                    if more_fields:
                        name = more_fields['name']
                        if '(' in name and ')' in name:
                            name = name[:name.index('(')]
                        standing['handle'] = name;
                elif resource=='codechef.com':
                    if 'more_fields' in standing and 'division' in standing['more_fields']:
                        if len(selected_divs)!=0 and standing['more_fields']['division'] not in selected_divs:
                            continue
                standings_to_show.append(standing)
            standings_to_show.sort(key=lambda standing: int(standing['place']))
            if len(standings_to_show)==0:
                if parsed_at:
                    name = contest['event']
                    raise ActivitiesCogError(f'None of the handles are present in the ranklist of `{name}`') 
                else:
                    raise ActivitiesCogError('Ranklist for this contest is being parsed, please come back later.') 
            division = selected_divs[0] if len(selected_divs)==1 else None
            problemset = contest.get('problems', None);
            pages = self._make_clist_standings_pages(standings_to_show, problemset=problemset, division=division)
            await inter.edit_original_message(embed=self._make_contest_embed_for_ranklist(contest=clist.format_contest(contest), timezone=timezone, parsed_at=parsed_at))
            await paginator.paginate(self.bot, 'text', inter, pages, wait_time=_STANDINGS_PAGINATE_WAIT_TIME)
        else:
            if (int(contest_id) == 4):
                await inter.edit_original_message("```I\'m not doing that! (╯°□°）╯︵ ┻━┻ ```""")
            else:
                handles = await cf_common.resolve_handles(inter, self.member_converter, handles, maxcnt=None, default_to_all_server=True)
                contest = cf_common.cache2.contest_cache.get_contest(contest_id)
                ranklist = None
                try:
                    ranklist = cf_common.cache2.ranklist_cache.get_ranklist(contest)
                except cache_system2.RanklistNotMonitored:
                    if contest.phase == 'BEFORE':
                        raise ActivitiesCogError(f'Contest `{contest.id} | {contest.name}` has not started')
                    ranklist = await cf_common.cache2.ranklist_cache.generate_ranklist(contest.id,
                                                                                    fetch_changes=True)
                await inter.edit_original_message(embed=self._make_contest_embed_for_ranklist(ranklist = ranklist, timezone = timezone))
                await self._show_ranklist(inter = inter, contest_id=contest_id, handles=handles, ranklist=ranklist)

    async def _show_ranklist(self, inter, contest_id: int, handles: List[str], ranklist, vc: bool = False):
        contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        if ranklist is None:
            raise ActivitiesCogError('No ranklist to show')

        handle_standings = []
        for handle in handles:
            try:
                standing = ranklist.get_standing_row(handle)
            except rl.HandleNotPresentError:
                continue

            # Database has correct handle ignoring case, update to it
            # TODO: It will throw an exception if this row corresponds to a team. At present ranklist doesnt show teams.
            # It should be fixed in https://github.com/cheran-senthil/TLE/issues/72
            handle = standing.party.members[0].handle
            if vc and standing.party.participantType != 'VIRTUAL':
                continue
            handle_standings.append((handle, standing))

        if not handle_standings:
            error = f'None of the handles are present in the ranklist of `{contest.name}`'
            if vc:
                await inter.edit_original_message(embed=discord_common.embed_alert(error))
                return
            raise ActivitiesCogError(error)

        handle_standings.sort(key=lambda data: data[1].rank)
        deltas = None
        if ranklist.is_rated:
            deltas = [ranklist.get_delta(handle) for handle, standing in handle_standings]

        problem_indices = [problem.index for problem in ranklist.problems]
        pages = self._make_standings_pages(contest, problem_indices, handle_standings, deltas)
        await paginator.paginate(self.bot, 'text', inter, pages,
                           message=await inter.original_message(),
                           wait_time=_STANDINGS_PAGINATE_WAIT_TIME)

    @discord_common.send_error_if(ActivitiesCogError, cache_system2.CacheError,
                                  cf_common.FilterError, rl.RanklistError,
                                  cf_common.ResolveHandleError)
    async def cog_slash_command_error(self, inter, error):
        pass

def setup(bot):
    bot.add_cog(Activities(bot))
