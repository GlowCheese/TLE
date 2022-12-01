import io
import asyncio
import contextlib
import logging
import math
import html
import cairo
import os
import time
import gi
import datetime
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

import disnake
import random, string
from disnake.ext import commands

from tle import constants
from tle.util import cache_system2
from tle.util import codeforces_api as cf
from tle.util import clist_api as clist
from tle.util import codeforces_common as cf_common
from tle.util import discord_common
from tle.util import events
from tle.util import paginator
from tle.util import table
from tle.util import tasks
from tle.util import db
from tle.util import scaper
from tle import constants

from disnake.ext import commands

from PIL import Image, ImageFont, ImageDraw

_NAME_MAX_LEN = 20
_PAGINATE_WAIT_TIME = 5 * 60  # 5 minutes
_PRETTY_HANDLES_PER_PAGE = 10
_TOP_DELTAS_COUNT = 10
_MAX_RATING_CHANGES_PER_EMBED = 15
_UPDATE_HANDLE_STATUS_INTERVAL = 6 * 60 * 60  # 6 hours
_UPDATE_CLIST_CACHE_INTERVAL = 3 * 60 * 60 # 3 hour

_GITGUD_SCORE_DISTRIB = (2, 3, 5, 8, 12, 17, 23, 23, 23)
_GITGUD_MAX_NEG_DELTA_VALUE = -300
_GITGUD_MAX_POS_DELTA_VALUE = 500

_DIVISION_RATING_LOW  = (2100, 1600, -1000)
_DIVISION_RATING_HIGH = (9999, 2099,  1599)

_SUPPORTED_CLIST_RESOURCES = ('codechef.com', 'atcoder.jp',
    'leetcode.com','codingcompetitions.withgoogle.com', 'facebook.com/hackercup', 'codedrills.io')

_CLIST_RESOURCE_SHORT_FORMS = {'cc':'codechef.com','codechef':'codechef.com', 'cf':'codeforces.com',
    'codeforces':'codeforces.com','ac':'atcoder.jp', 'atcoder':'atcoder.jp', 'lc':'leetcode.com', 
    'leetcode':'leetcode.com', 'google':'codingcompetitions.withgoogle.com', 'cd': 'codedrills.io', 'codedrills':'codedrills.io',
    'fb':'facebook.com/hackercup', 'facebook':'facebook.com/hackercup'}

_CP_PLATFORMS = commands.option_enum({'Codechef':'codechef.com', 'Codeforces':'codeforces.com',
    'Atcoder':'atcoder.jp', 'Leetcode':'leetcode.com', 'Google':'codingcompetitions.withgoogle.com',
    'Codedrills':'codedrills.io', 'Facebook':'facebook.com/hackercup'})

_RESOURCE_NAMES = {
    'codeforces.com': 'CodeForces',
    'codechef.com': 'CodeChef', 
    'atcoder.jp': 'AtCoder',
    'leetcode.com': 'LeetCode',
    'codingcompetitions.withgoogle.com': 'Google', 
    'facebook.com/hackercup': 'Facebook', 
    'codedrills.io': 'CodeDrills'
}

CODECHEF_RATED_RANKS = (
    cf.Rank(-10 ** 9, 1400, '1 Star', '1★', '#DADADA', 0x666666),
    cf.Rank(1400, 1600, '2 Star', '2★', '#C9E0CA', 0x1e7d22),
    cf.Rank(1600, 1800, '3 Star', '3★', '#CEDAF3', 0x3366cc),
    cf.Rank(1800, 2000, '4 Star', '4★', '#DBD2DE', 0x684273),
    cf.Rank(2000, 2200, '5 Star', '5★', '#FFF0C2', 0xffbf00),
    cf.Rank(2200, 2500, '6 Star', '6★', '#FFE3C8', 0xff7f00),
    cf.Rank(2500, 10**9, '7 Star', '7★', '#F1C1C8', 0xd0011b)
)

ATCODER_RATED_RANKS = (
    cf.Rank(-10 ** 9, 400, 'Gray', 'Gray', '#DADADA', 0x808080),
    cf.Rank(400, 800, 'Brown', 'Brown', '#D9C5B2', 0x7F3F00),
    cf.Rank(800, 1200, 'Green', 'Green', '#B2D9B2', 0x007F00),
    cf.Rank(1200, 1600, 'Cyan', 'Cyan', '#B2ECEC', 0x00C0C0),
    cf.Rank(1600, 2000, 'Blue', 'Blue', '#B2B2FF', 0x0000FF),
    cf.Rank(2000, 2400, 'Yellow', 'Yellow', '#ECECB2', 0xBFBF00),
    cf.Rank(2400, 2800, 'Orange', 'Orange', '#FFD9B2', 0xF67B00),
    cf.Rank(2800, 10**9, 'Red', 'Red', '#FFB2B2', 0xF70000)
)

class HandleCogError(commands.CommandError):
    pass

def ac_rating_to_color(rating):
    h = discord_color_to_hex(rating2acrank(rating).color_embed)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def cc_rating_to_color(rating):
    h = discord_color_to_hex(rating2star(rating).color_embed)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def discord_color_to_hex(color):
    h = str(hex(color))
    h = h[2:]
    return ('0'*(6-len(h)))+h

def rating_to_color(rating):
    """returns (r, g, b) pixels values corresponding to rating"""
    rank = cf.rating2rank(rating)
    h = discord_color_to_hex(rank.color_embed)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def rating2star(rating):
    for rank in CODECHEF_RATED_RANKS:
        if rank.low <= rating < rank.high:
            return rank

def rating2acrank(rating):
    for rank in ATCODER_RATED_RANKS:
        if rank.low <= rating < rank.high:
            return rank

def randomword(length):
   letters = string.ascii_lowercase
   return ''.join(random.choice(letters) for i in range(length))

def resource_name(resource):
    if resource is None:
        return ''
    if resource in _RESOURCE_NAMES:
        return _RESOURCE_NAMES[resource]
    return resource

FONTS = [
    'Noto Sans',
    'Noto Sans CJK JP',
    'Noto Sans CJK SC',
    'Noto Sans CJK TC',
    'Noto Sans CJK HK',
    'Noto Sans CJK KR',
]

def get_gudgitters_image(rankings):
    """return PIL image for rankings"""
    SMOKE_WHITE = (250, 250, 250)
    BLACK = (0, 0, 0)

    DISCORD_GRAY = (.212, .244, .247)

    ROW_COLORS = ((0.95, 0.95, 0.95), (0.9, 0.9, 0.9))

    WIDTH = 900
    #HEIGHT = 900
    BORDER_MARGIN = 20
    COLUMN_MARGIN = 10
    HEADER_SPACING = 1.25
    WIDTH_RANK = 0.08*WIDTH
    WIDTH_NAME = 0.38*WIDTH
    LINE_HEIGHT = 40#(HEIGHT - 2*BORDER_MARGIN)/(20 + HEADER_SPACING)
    HEIGHT = int((len(rankings) + HEADER_SPACING) * LINE_HEIGHT + 2*BORDER_MARGIN)
    # Cairo+Pango setup
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    context = cairo.Context(surface)
    context.set_line_width(1)
    context.set_source_rgb(*DISCORD_GRAY)
    context.rectangle(0, 0, WIDTH, HEIGHT)
    context.fill()
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.font_description_from_string(','.join(FONTS) + ' 20'))
    layout.set_ellipsize(Pango.EllipsizeMode.END)

    def draw_bg(y, color_index):
        nxty = y + LINE_HEIGHT

        # Simple
        context.move_to(BORDER_MARGIN, y)
        context.line_to(WIDTH, y)
        context.line_to(WIDTH, nxty)
        context.line_to(0, nxty)
        context.set_source_rgb(*ROW_COLORS[color_index])
        context.fill()

    def draw_row(pos, username, handle, rating, color, y, bold=False):
        context.set_source_rgb(*[x/255.0 for x in color])

        context.move_to(BORDER_MARGIN, y)

        def draw(text, width=-1):
            text = html.escape(text)
            if bold:
                text = f'<b>{text}</b>'
            layout.set_width((width - COLUMN_MARGIN)*1000) # pixel = 1000 pango units
            layout.set_markup(text, -1)
            PangoCairo.show_layout(context, layout)
            context.rel_move_to(width, 0)

        draw(pos, WIDTH_RANK)
        draw(username, WIDTH_NAME)
        draw(handle, WIDTH_NAME)
        draw(rating)

    #

    y = BORDER_MARGIN

    # draw header
    draw_row('#', 'Name', 'Handle', 'Points', SMOKE_WHITE, y, bold=True)
    y += LINE_HEIGHT*HEADER_SPACING

    for i, (pos, name, handle, rating, score) in enumerate(rankings):
        color = rating_to_color(rating)
        draw_bg(y, i%2)
        draw_row(str(pos+1), f'{name} ({rating if rating else "N/A"})', handle, str(score), color, y)
        if rating and rating >= 3000:  # nutella
            draw_row('', name[0], handle[0], '', BLACK, y)
        y += LINE_HEIGHT

    image_data = io.BytesIO()
    surface.write_to_png(image_data)
    image_data.seek(0)
    discord_file = disnake.File(image_data, filename='gudgitters.png')
    return discord_file

def get_prettyhandles_image(rows, font, color_converter=rating_to_color):
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
    draw_row('#', 'Username', 'Handle', 'Rating', BLACK, y)
    y += int(Y_INC * 1.5)

    # trim name to fit in the column width
    def _trim(name):
        width = WIDTH_NAME - 10
        while font.getsize(name)[0] > width:
            name = name[:-4] + '...'  # "…" is printed as floating dots
        return name

    for pos, name, handle, rating in rows:
        name = _trim(name)
        handle = _trim(handle)
        color = color_converter(rating)
        draw_row(str(pos + 1), name, handle, str(rating) if rating else 'N/A', color or BLACK, y)
        if rating and rating >= 3000:  # nutella
            nutella_x = START_X + WIDTH_RANK
            draw.text((nutella_x, y), name[0], fill=BLACK, font=font)
            nutella_x += WIDTH_NAME
            draw.text((nutella_x, y), handle[0], fill=BLACK, font=font)
        y += Y_INC

    return img


def _make_profile_embed(member, user, handles={}):
    if user:
        desc = f'Handle for {member.mention} is currently set to **[{user.handle}]({user.url})**'
        if user.rating is None:
            embed = disnake.Embed(description=desc)
            embed.add_field(name='Rating', value='Unrated', inline=True)
        else:
            embed = disnake.Embed(description=desc, color=user.rank.color_embed)
            embed.add_field(name='Rating', value=user.rating, inline=True)
            embed.add_field(name='Rank', value=user.rank.title, inline=True)
    else:
        embed = disnake.Embed(description="CodeForces handle is not set for this user")
    for key in handles:
        if key in ['codeforces.com', 'codedrills.io', 'facebook.com/hackercup']: continue
        title = resource_name(key)
        embed.add_field(name=title, value=handles[key], inline=True)
    if user:
        tmp = str(user.titlePhoto)
        if tmp[:2] == "//": tmp = "https:" + tmp

        embed.set_thumbnail(url=f'{tmp}')
    return embed


def _make_pages(users, title, resource='codeforces.com'):
    chunks = paginator.chunkify(users, 10)
    pages = []
    done = 1
    no_rating = resource in ['codingcompetitions.withgoogle.com', 'facebook.com/hackercup']
    no_rating_suffix = resource!='codeforces.com'
    style = table.Style('{:>}  {:<}  {:<}  {:<}')
    for chunk in chunks:
        t = table.Table(style)
        t += table.Header('#', 'Name', 'Handle', 'Contests' if no_rating else 'Rating')
        t += table.Line()
        for i, (member, handle, rating, n_contests) in enumerate(chunk):
            name = member.display_name if member else "unknown"
            if len(name) > _NAME_MAX_LEN:
                name = name[:_NAME_MAX_LEN - 1] + '…'
            rank = cf.rating2rank(rating)
            rating_str = 'N/A' if rating is None else str(rating)
            fourth = n_contests if no_rating else ((f'{rating_str}')+((f'({rank.title_abbr})') if not no_rating_suffix else ''))
            t += table.Data(i + done, name, handle, fourth)
        table_str = '```\n'+str(t)+'\n```'
        embed = discord_common.cf_color_embed(description=table_str)
        pages.append((title, embed))
        done += len(chunk)
    return pages


def parse_date(arg):
    try:
        if len(arg) == 6:
            fmt = '%m%Y'
        # elif len(arg) == 4:
            # fmt = '%Y'
        else:
            raise ValueError
        return datetime.datetime.strptime(arg, fmt)
    except ValueError:
        raise HandleCogError(f'{arg} is an invalid date argument')

class Handles(commands.Cog, description = "Verify and manage your CP handles"):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(self.__class__.__name__)
        self.font = ImageFont.truetype(constants.NOTO_SANS_CJK_BOLD_FONT_PATH, size=26) # font for ;handle pretty
        self.converter = commands.MemberConverter()

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        cf_common.event_sys.add_listener(self._on_rating_changes)
        self._set_ex_users_inactive_task.start()
        self._update_clist_users_cache.start()

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        try:
            await self._remove(member)
        except:
            pass

    @tasks.task_spec(name='RefreshClistUserCache',
                     waiter=tasks.Waiter.fixed_delay(_UPDATE_CLIST_CACHE_INTERVAL))
    async def _update_clist_users_cache(self, _):
        for guild in self.bot.guilds:
            try:
                await self._update_stars_all(guild)
            except:
                pass

    @tasks.task_spec(name='SetExUsersInactive',
                     waiter=tasks.Waiter.fixed_delay(_UPDATE_HANDLE_STATUS_INTERVAL))
    async def _set_ex_users_inactive_task(self, _):
        # To set users inactive in case the bot was dead when they left.
        to_set_inactive = []
        for guild in self.bot.guilds:
            user_id_handle_pairs = cf_common.user_db.get_handles_for_guild(guild.id)
            to_set_inactive += [(guild.id, user_id) for user_id, _ in user_id_handle_pairs
                                if guild.get_member(user_id) is None]
        cf_common.user_db.set_inactive(to_set_inactive)

    @events.listener_spec(name='RatingChangesListener',
                          event_cls=events.RatingChangesUpdate,
                          with_lock=True)
    async def _on_rating_changes(self, event):
        contest, changes = event.contest, event.rating_changes
        change_by_handle = {change.handle: change for change in changes}

        async def update_for_guild(guild):
            with contextlib.suppress(HandleCogError):
                await self._update_ranks_all(guild)
            channel_id = cf_common.user_db.get_rankup_channel(guild.id)
            channel = guild.get_channel(channel_id)
            if channel is not None:
                with contextlib.suppress(HandleCogError):
                    embeds = self._make_rankup_embeds(guild, contest, change_by_handle)
                    await channel.send(embeds = embeds)

        await asyncio.gather(*(update_for_guild(guild) for guild in self.bot.guilds),
                             return_exceptions=True)
        self.logger.info(f'All guilds updated for contest {contest.id}.')

    @commands.slash_command(description='Commands that have to do with handles')
    async def handle(self, inter):
        pass

    @staticmethod
    async def update_member_rank_role(member, role_to_assign, *, reason):
        """Sets the `member` to only have the rank role of `role_to_assign`. All other rank roles
        on the member, if any, will be removed. If `role_to_assign` is None all existing rank roles
        on the member will be removed.
        """
        role_names_to_remove = {rank.title for rank in cf.RATED_RANKS}
        if role_to_assign is not None:
            role_names_to_remove.discard(role_to_assign.name)
        to_remove = [role for role in member.roles if role.name in role_names_to_remove]
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
        if role_to_assign is not None and role_to_assign not in member.roles:
            await member.add_roles(role_to_assign, reason=reason)

    @handle.sub_command(description='Set handle of a user')
    async def set(self, inter, member: disnake.Member, handle: str, resource: _CP_PLATFORMS = "codeforces.com"):
        """
        Set codeforces/codechef/atcoder/google handle of a user.

        Parameters
        ----------
        member: Member to set handle
        handle: User CP handle
        resource: Competitive Programming platform (default is Codeforces)
        """
        await inter.response.defer()

        has_perm = await self.bot.is_owner(inter.author) \
            or inter.author.guild_permissions.administrator \
            or discord_common.is_guild_owner_predicate(inter)

        if not has_perm:
            message = 'You must have Administrator permission to use this command.\nIf you want to set handle for yourself, try `/handle identify` instead.'
            return await inter.edit_original_message(embed = discord_common.embed_alert(message))

        embed = None
        if resource!='codeforces.com':
            users = await clist.account(handle=handle, resource=resource)
            for user in users: await self._set_account_id(member, inter, user)
        else:
            user, = await cf.user.info(handles=[handle])
            await self._set(inter, member, user)
        await self._get(inter, member)
    
    async def _set_account_id(self, member, inter, user):
        guild_id = inter.guild.id
        try:
            cf_common.user_db.set_account_id(member.id, guild_id, user['id'], user['resource'], user['handle'])
        except db.UniqueConstraintFailed:
            raise HandleCogError(f'The handle `{user["handle"]}` is already associated with another user.')

    async def _set(self, inter, member, user):
        handle = user.handle
        try:
            cf_common.user_db.set_handle(member.id, inter.guild.id, handle)
        except db.UniqueConstraintFailed:
            raise HandleCogError(f'The handle `{handle}` is already associated with another user.')
        cf_common.user_db.cache_cf_user(user)

        roles = [role for role in inter.guild.roles if role.name == user.rank.title]
        if not roles: return
        try:
            await self.update_member_rank_role(member, roles[0], reason='New handle set for user')
        except disnake.Forbidden:
            pass

    @handle.sub_command(description='Identify your CP account')
    @cf_common.user_guard(group='handle',
                          get_exception=lambda: HandleCogError('Identification is already running for you'))
    async def identify(self, inter, handle: str, resource: _CP_PLATFORMS = "codeforces.com"):
        """
        Link a codeforces/codechef/atcoder account to discord account.
        
        For linking google/codedrills/leetcode handles, please contact a moderator.

        Parameters
        ----------
        handle: User handle
        resource: Competitive Programming platform (default is CodeForces)
        """
        await inter.response.defer()

        invoker = inter.author.mention
        if resource!='codeforces.com':
            if resource not in ['codechef.com','atcoder.jp']:
                return await inter.edit_original_message(
                    f'{invoker}, you cannot identify handles of {resource} as of now.'
                    f'Please contact a moderator for linking your handle')
            users = await clist.account(handle, resource)
            if users is None or len(users)<0:
                return await inter.edit_original_message(f'{invoker}, I couldn\'t find your handle.')
            user = users[0]
            token = randomword(8)
            field = "name"
            if resource=='atcoder.jp': field = 'affiliation'
            await inter.edit_original_message(f'{invoker}, change your {field} to `{token}` on {resource} within 60 seconds')
            await asyncio.sleep(60)

            if scaper.assert_display_name(handle, token, resource, inter.author.mention):
                member = inter.author
                await self._set_account_id(member, inter, user)
                await self._get(inter, member)
            else:
                await inter.send(f'Sorry {invoker}, can you try again?')
        else:
            if handle in cf_common.HandleIsVjudgeError.HANDLES:
                raise cf_common.HandleIsVjudgeError(handle)

            users = await cf.user.info(handles=[handle])
            handle = users[0].handle
            problems = [prob for prob in cf_common.cache2.problem_cache.problems
                        if prob.rating <= 1200]
            problem = random.choice(problems)
            await inter.edit_original_message(f'{invoker}, submit a compile error to <{problem.url}> within 60 seconds to identify handle')
            await asyncio.sleep(60)

            subs = await cf.user.status(handle=handle, count=5)
            if any(sub.problem.name == problem.name and sub.verdict == 'COMPILATION_ERROR' for sub in subs):
                user, = await cf.user.info(handles=[handle])
                member = inter.author
                await self._set(inter, member, user)
                await self._get(inter, member)
            else:
                await inter.send(f'Sorry {invoker}, can you try again?')

    async def _get(self, inter, member):
        handle = cf_common.user_db.get_handle(member.id, inter.guild.id)
        handles = cf_common.user_db.get_account_id_by_user(member.id, inter.guild.id)
        if not handle and handles is None:
            raise HandleCogError(f'Handle for `{member}` not found in database')
        user = cf_common.user_db.fetch_cf_user(handle) if handle else None
        embed = _make_profile_embed(member, user,handles=handles)
        await inter.send(embed = embed)

    @handle.sub_command(description='Get handle by Discord username')
    async def get(self, inter, member: disnake.Member):
        """
        Show Codeforces handle of a user.

        Parameters
        ----------
        member: Member to get handle
        """
        await inter.response.defer()

        await self._get(inter, member)

    @handle.sub_command(description='Get Discord username by cf handle')
    async def rget(self, inter, handle: str):
        """
        Show Discord username of a cf handle.

        Parameters
        ----------
        handle: Codeforces handle
        """
        await inter.response.defer()

        user_id = cf_common.user_db.get_user_id(handle, inter.guild.id)
        if not user_id: return await inter.edit_original_message(
            f'Discord username for `{handle}` not found in database')
        user = cf_common.user_db.fetch_cf_user(handle)
        member = inter.guild.get_member(user_id)
        embed = _make_profile_embed(member, user)
        await inter.edit_original_message(embed=embed)

    async def _remove(self, member:disnake.Member):
        rc = cf_common.user_db.remove_handle(member.id, member.guild.id)
        if not rc:
            raise HandleCogError(f'Handle for `{member}` not found in database')
            
        try:
            await self.update_member_rank_role(member, role_to_assign=None, reason='Handle removed for user')
        except:
            pass

    @handle.sub_command(description='Remove handle for a user')
    async def remove(self, inter, member: disnake.Member = None):
        """
        Remove all CP handles of a user.

        Parameters
        ----------
        member: Member to remove handles
        """

        await inter.response.defer()

        member = member or inter.author
        has_perm = await self.bot.is_owner(inter.author) \
            or inter.author.guild_permissions.administrator \
            or discord_common.is_guild_owner_predicate(inter)

        if not has_perm and member != inter.author:
            return await inter.edit_original_message(f'You don\'t have permission to remove other members\' handle.')

        try:
            await self._remove(member)
        except:
            pass
        embed = discord_common.embed_success(f'Handle for `{member}` has been removed.')
        await inter.edit_original_message(embed=embed)

    @handle.sub_command(description='Resolve redirect of your handle')
    async def unmagic(self, inter):
        """
        Updates handle of the calling user if they have changed handles
        (typically new year's magic)
        """

        await inter.response.defer()

        member = inter.author
        handle = cf_common.user_db.get_handle(member.id, inter.guild.id)
        if handle == None:
            return await inter.edit_original_message(f'{member.mention}, your CF handle is not already set.')
        await self._unmagic_handles(inter, [handle], {handle: member})

    @handle.sub_command(description='Resolve all CF handles needing redirection')
    @commands.check_any(discord_common.is_guild_owner(), commands.has_permissions(administrator = True), commands.is_owner())
    async def unmagic_all(self, inter):
        """
        Updates handles of all users that have changed handles
        (typically new year's magic)
        """

        await inter.response.defer()

        user_id_and_handles = cf_common.user_db.get_handles_for_guild(inter.guild.id)

        handles = []
        rev_lookup = {}
        for user_id, handle in user_id_and_handles:
            member = inter.guild.get_member(user_id)
            handles.append(handle)
            rev_lookup[handle] = member
        await self._unmagic_handles(inter, handles, rev_lookup)

    async def _unmagic_handles(self, inter, handles, rev_lookup):
        handle_cf_user_mapping = await cf.resolve_redirects(handles)
        mapping = {(rev_lookup[handle], handle): cf_user
                   for handle, cf_user in handle_cf_user_mapping.items()}
        summary_embed = await self._fix_and_report(inter, mapping)
        await inter.edit_original_message(embed=summary_embed)

    async def _fix_and_report(self, inter, redirections):
        fixed = []
        failed = []
        for (member, handle), cf_user in redirections.items():
            if not cf_user:
                failed.append(handle)
            else:
                try:
                    await self._set(inter, member, cf_user)
                except:
                    pass
                fixed.append((handle, cf_user.handle))

        # Return summary embed
        lines = []
        if not fixed and not failed:
            return discord_common.embed_success('No handles updated')
        if fixed:
            lines.append('**Fixed**')
            lines += (f'{old} -> {new}' for old, new in fixed)
        if failed:
            lines.append('**Failed**')
            lines += failed
        return discord_common.embed_success('\n'.join(lines))

    @commands.slash_command(description="Show gitgudders")
    async def gitgudders(self, inter):
        """
        Show the list of users of gitgud with their scores.
        """
        await inter.response.defer()

        res = cf_common.user_db.get_gudgitters()
        res.sort(key=lambda r: r[1], reverse=True)

        rankings = []
        index = 0
        for user_id, score in res:
            member = inter.guild.get_member(int(user_id))
            if member is None:
                continue
            if score > 0:
                handle = cf_common.user_db.get_handle(user_id, inter.guild.id)
                user = cf_common.user_db.fetch_cf_user(handle)
                if user is None:
                    continue
                discord_handle = member.display_name
                rating = user.rating
                rankings.append((index, discord_handle, handle, rating, score))
                index += 1
            if index == 20:
                break

        if not rankings:
            raise HandleCogError('No one has completed a gitgud challenge, send ;gitgud to request and ;gotgud to mark it as complete')
        discord_file = get_gudgitters_image(rankings)
        await inter.edit_original_message(file=discord_file)

    def filter_rating_changes(self, rating_changes):
        rating_changes = [change for change in rating_changes
                    if self.dlo <= change.ratingUpdateTimeSeconds < self.dhi]
        return rating_changes

    @handle.sub_command(description="Show all handles")
    async def list(self, inter, resource: _CP_PLATFORMS = "codeforces.com", countries: str = ""):
        """Shows members of the server who have registered their handles and their ratings. Default platform is CodeForces.

        Parameters
        ----------
        resource: Competitive Programming platform (default is CodeForces)
        countries: e.g: "vietnam; united states" (without quotes)
        """
        await inter.response.defer()

        countries = countries.split(';')
        countries = [cf_common.reformat_country_name(country) for country in countries if country != ""]

        users = None
        if resource == 'codeforces.com':
            res = cf_common.user_db.get_cf_users_for_guild(inter.guild.id)
            users = [
                (inter.guild.get_member(user_id), cf_user.handle, cf_user.rating)
                for user_id, cf_user in res
                    if (not countries) or (cf_user.country in countries)
            ]
            users = [(member, handle, rating, 0) for member, handle, rating in users if member is not None]
        else:
            if not countries: return await inter.edit_original_message(
                "Countries can currently only be specified for CodeForces users.")
            account_ids = cf_common.user_db.get_account_ids_for_resource(inter.guild.id ,resource)
            members = {}
            ids = []
            for user_id, account_id, handle in account_ids:
                ids.append(account_id)
                members[account_id] = inter.guild.get_member(user_id)
            clist_users = await clist.fetch_user_info(resource, ids)
            users = []
            for clist_user in clist_users:
                handle = clist_user['handle']
                if resource in ['codedrills.io', 'facebook.com/hackercup']:
                    name = clist_user['name']
                    if '(' in name and ')' in name:
                        name = name[:name.index('(')]
                    handle = name or ' '
                rating = int(clist_user['rating']) if clist_user['rating']!=None else None
                member = members[int(clist_user['id'])]
                n_contests = clist_user['n_contests']
                users.append((member, handle, rating, n_contests))
        if not users:
            raise HandleCogError('No members with registered handles.')

        users.sort(key=lambda x: (1 if x[2] is None else -x[2], -x[3], x[1]))  # Sorting by (-rating,-contests, handle)
        title = f'Handles of server members ({resource_name(resource)})'
        if countries:
            title += ' from ' + ', '.join(f'`{country}`' for country in countries)
        pages = _make_pages(users, title, resource)
        await paginator.paginate(self.bot, 'edit', inter, pages,
                           message = await inter.original_message(),
                           wait_time=_PAGINATE_WAIT_TIME, set_pagenum_footers=True)

    @handle.sub_command(description="Show handles, but prettier")
    async def pretty(self, inter, page_no:int = None, resource: _CP_PLATFORMS = "codeforces.com"):
        """
        Show members of the server who have registered their handles and their Codeforces
        ratings, in color.

        Parameters
        ----------
        page_no: page number
        resource: Competitive Programming platform (default is CodeForces)
        """
        await inter.response.defer()

        rows = []
        author_idx = None
        if resource!='codeforces.com':
            id_to_member = dict()
            account_ids = cf_common.user_db.get_account_ids_for_resource(inter.guild.id ,resource)
            ids = []
            for user_id, account_id, handle in account_ids:
                ids.append(account_id)
                id_to_member[account_id] = inter.guild.get_member(user_id)
            clist_users = await clist.fetch_user_info(resource, account_ids=ids)
            clist_users.sort(key=lambda user: int(user['rating']) if user['rating'] is not None else -1, reverse=True)
            for user in clist_users:
                if user['id'] not in id_to_member: continue
                member = id_to_member[user['id']]
                if member is None: continue
                idx = len(rows)
                if member == inter.author: author_idx = idx
                rows.append((idx, member.display_name, user['handle'], user['rating']))
        else:
            user_id_cf_user_pairs = cf_common.user_db.get_cf_users_for_guild(inter.guild.id)
            user_id_cf_user_pairs.sort(key=lambda p: p[1].rating if p[1].rating is not None else -1,
                                    reverse=True)
            for user_id, cf_user in user_id_cf_user_pairs:
                member = inter.guild.get_member(user_id)
                if member is None: continue
                idx = len(rows)
                if member == inter.author: author_idx = idx
                rows.append((idx, member.display_name, cf_user.handle, cf_user.rating))

        if not rows:
            return await inter.edit_original_message(embed = discord_common.embed_alert('No members with registered handles.'))
        max_page = math.ceil(len(rows) / _PRETTY_HANDLES_PER_PAGE) - 1

        if (page_no is None and author_idx is None) or (page_no is not None and (page_no < 1 or page_no > max_page + 1)):
            return await inter.edit_original_message(embed = discord_common.embed_alert(f'Please specify a page number between 1 and {max_page + 1}.'))

        msg = None
        if page_no is not None:
            msg = f'Showing page no.{page_no}:'
            start_idx = (page_no-1) * _PRETTY_HANDLES_PER_PAGE
        else:
            msg = f'Showing neighbourhood of user `{inter.author.display_name}`:'
            num_before = (_PRETTY_HANDLES_PER_PAGE - 1) // 2
            start_idx = max(0, author_idx - num_before)
        rows_to_display = rows[start_idx : start_idx + _PRETTY_HANDLES_PER_PAGE]
        img = None
        if resource=='codechef.com':
            img = get_prettyhandles_image(rows_to_display, self.font, color_converter=cc_rating_to_color)
        elif resource=='atcoder.jp':
            img = get_prettyhandles_image(rows_to_display, self.font, color_converter=ac_rating_to_color)
        else:
            img = get_prettyhandles_image(rows_to_display, self.font)
        buffer = io.BytesIO()
        img.save(buffer, 'png')
        buffer.seek(0)
        await inter.edit_original_message(msg, file=disnake.File(buffer, 'handles.png'))

    async def _update_ranks(self, guild, res):
        member_handles = [(guild.get_member(user_id), handle) for user_id, handle in res]
        member_handles = [(member, handle) for member, handle in member_handles if member is not None]
        if not member_handles:
            raise HandleCogError('Handles not set for any user')
        members, handles = zip(*member_handles)
        users = await cf.user.info(handles=handles)
        for user in users:
            cf_common.user_db.cache_cf_user(user)
        required_roles = {user.rank.title for user in users if user.rank != cf.UNRATED_RANK}
        rank2role = {role.name: role for role in guild.roles if role.name in required_roles}
        missing_roles = required_roles - rank2role.keys()
        if missing_roles:
            roles_str = ', '.join(f'`{role}`' for role in missing_roles)
            plural = 's' if len(missing_roles) > 1 else ''
            raise HandleCogError(f'Role{plural} for rank{plural} {roles_str} not present in the server.')

        ok = True
        for member, user in zip(members, users):
            role_to_assign = None if user.rank == cf.UNRATED_RANK else rank2role[user.rank.title]
            try:
                await self.update_member_rank_role(member, role_to_assign, reason='Codeforces rank update')
            except disnake.Forbidden:
                ok = False
        if not ok: raise HandleCogError(f'Cannot update roles for some members: Missing permission.')

    @staticmethod
    def _make_rankup_embeds(guild, contest, change_by_handle):
        """Make an embed containing a list of rank changes and top rating increases for the members
        of this guild.
        """
        user_id_handle_pairs = cf_common.user_db.get_handles_for_guild(guild.id)
        member_handle_pairs = [(guild.get_member(user_id), handle)
                               for user_id, handle in user_id_handle_pairs]

        member_change_pairs = [(member, change_by_handle[handle])
                               for member, handle in member_handle_pairs
                               if member is not None and handle in change_by_handle]
        if not member_change_pairs:
            raise HandleCogError(f'Contest `{contest.id} | {contest.name}` was not rated for any '
                                 'member of this server.')

        member_change_pairs.sort(key=lambda pair: pair[1].newRating, reverse=True)
        rank_to_role = {role.name: role for role in guild.roles}

        def rating_to_displayable_rank(rating):
            rank = cf.rating2rank(rating).title
            role = rank_to_role.get(rank)
            return role.mention if role else rank

        rank_changes_str = []
        for member, change in member_change_pairs:
            cache = cf_common.cache2.rating_changes_cache
            if (change.oldRating == 1500
                    and len(cache.get_rating_changes_for_handle(change.handle)) == 1):
                # If this is the user's first rated contest.
                old_role = 'Unrated'
            else:
                old_role = rating_to_displayable_rank(change.oldRating)
            new_role = rating_to_displayable_rank(change.newRating)
            if new_role != old_role:
                rank_change_str = (f'`{member}` ([{change.handle}]({cf.PROFILE_BASE_URL}{change.handle})): {old_role} '
                                   f'\N{LONG RIGHTWARDS ARROW} {new_role}')
                rank_changes_str.append(rank_change_str)

        member_change_pairs.sort(key=lambda pair: pair[1].newRating - pair[1].oldRating,
                                 reverse=True)
        top_increases_str = []
        for member, change in member_change_pairs[:_TOP_DELTAS_COUNT]:
            delta = change.newRating - change.oldRating
            delta = f'+{delta}' if delta >= 0 else f'-{-delta}'

            increase_str = (f'`{member}` ([{change.handle}]({cf.PROFILE_BASE_URL}{change.handle})): {change.oldRating}'
                            f' \N{LONG RIGHTWARDS ARROW} {change.newRating} **({delta})**')
            top_increases_str.append(increase_str)

        embed_heading = disnake.Embed(
            title=contest.name, url=contest.url, description="")
        embed_heading.set_author(name="Rank updates")
        embeds = [embed_heading]

        for rank_changes_chunk in paginator.chunkify(rank_changes_str, _MAX_RATING_CHANGES_PER_EMBED):
            desc = '\n'.join(rank_changes_chunk)
            embed = disnake.Embed(description=desc)
            embeds.append(embed)

        top_rating_increases_embed = disnake.Embed(description='\n'.join(
            top_increases_str) or 'Nobody has joined the contest :(')
        top_rating_increases_embed.set_author(name='Top rating changes')

        embeds.append(top_rating_increases_embed)
        discord_common.set_same_cf_color(embeds)

        return embeds

    @commands.slash_command(description='Commands for role updates')
    @commands.check_any(discord_common.is_guild_owner(), commands.has_permissions(administrator = True), commands.is_owner())
    async def roleupdate(self, inter):
        """
        Group for commands involving role updates.
        """
        pass

    @roleupdate.sub_command(description='Update roles for Codeforces handles')
    @commands.check_any(discord_common.is_guild_owner(), commands.has_permissions(administrator = True), commands.is_owner())
    async def now(self, inter):
        """
        Update roles for Codeforces handles
        """
        await inter.response.defer()
        await self._update_ranks_all(inter.guild)
        await inter.edit_original_message(embed=discord_common.embed_success('Roles updated successfully.'))
    
    @roleupdate.sub_command_group(description='Group of commands for publishing rank update')
    async def publish(self, inter):
        """
        This is a feature to publish a summary of rank changes and top rating increases in a particular contest for members of this server. 
        """
        pass

    @publish.sub_command(description='Auto publish rank update in this channel')
    @commands.check_any(discord_common.is_guild_owner(), commands.has_permissions(administrator = True), commands.is_owner())
    async def auto(self, inter, choice: str = commands.Param(choices=['here', 'off'])):
        """
        Automatically publish the summary to this channel whenever rating changes on Codeforces are released.

        Parameters
        ----------
        choice: "here" to enable auto publish rank update in this channel, or "off" to turn it off
        """
        await inter.response.defer(ephemeral = True)

        if choice == 'here':
            if inter.channel.type != disnake.ChannelType.text:
                return await inter.edit_original_message(f'This current channel is not a text channel.')
            cf_common.user_db.set_rankup_channel(inter.guild.id, inter.channel.id)
            await inter.send(embed=discord_common.embed_success(f'Auto rank update publishing enabled in this channel {inter.channel.mention}.'))
        else:
            rc = cf_common.user_db.clear_rankup_channel(inter.guild.id)
            if not rc: return await inter.edit_original_message('Auto rank update publishing is already disabled.')
            await inter.send(embed=discord_common.embed_success('Auto rank update publishing disabled.'))

    @publish.sub_command(description='Publish a rank update for the given contest')
    async def contest(self, inter, contest_id: int):
        """
        Specifying contest id will publish the summary immediately.

        Parameters
        ----------
        contest_id: Contest id to publish rank update
        """
        await inter.response.defer()

        try:
            contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        except cache_system2.ContestNotFound as e:
            return await inter.edit_original_message(f'Contest with id `{e.contest_id}` not found.')
        if contest.phase != 'FINISHED':
            return await inter.edit_original_message(f'Contest `{contest_id} | {contest.name}` has not finished.')
        try:
            changes = await cf.contest.ratingChanges(contest_id=contest_id)
        except cf.RatingChangesUnavailableError:
            changes = None
        if not changes:
            return await inter.edit_original_message(f'Rating changes are not available for contest `{contest_id} | '
                                 f'{contest.name}`.')

        change_by_handle = {change.handle: change for change in changes}
        rankup_embeds = self._make_rankup_embeds(inter.guild, contest, change_by_handle)
        
        await inter.edit_original_message(embeds = rankup_embeds)

    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_slash_command_error(self, inter, error):
        pass

def setup(bot):
    bot.add_cog(Handles(bot))
