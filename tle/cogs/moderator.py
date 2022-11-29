import os
import subprocess
import sys
import time
import functools
import textwrap
import logging
import json
import disnake

from math import *
from tle.util import table
from tle.util import paginator
from disnake.ext import commands
from tle.util import codeforces_common as cf_common
from tle.util import codeforces_api as cf
from tle import constants
from tle.util import discord_common
from tle.cogs.handles import CODECHEF_RATED_RANKS
from tle.util.codeforces_api import RATED_RANKS as CODEFORCES_RATED_RANKS
from tle.util.codeforces_common import pretty_time_format
from tle.util import clist_api

_PAGINATE_WAIT_TIME = 5 * 60  # 5 minutes
_GUILDS_PER_PAGE = 10
_NAME_MAX_LEN = 30
_OWNER_MAX_LEN = 30
_SUCCESS_GREEN = 0x28A745
_ALERT_AMBER = 0xFFBF00

async def _create_roles(guild, ranks):
    roles = [role.name for role in guild.roles]
    for rank in ranks[::-1]:
        if rank.title not in roles:
            await guild.create_role(name=rank.title, colour=disnake.Colour(rank.color_embed))

async def _delete_roles(guild, ranks):
    roles = [rank.title for rank in ranks[::-1]]
    for role in guild.roles:
        if role.name in roles: await role.delete()

def embed_success(desc):
    return disnake.Embed(description=str(desc), color=_SUCCESS_GREEN)

def embed_alert(desc):
    return disnake.Embed(description=str(desc), color=_ALERT_AMBER)

def _make_pages(guilds, title):
    chunks = paginator.chunkify(guilds, _GUILDS_PER_PAGE)
    pages = []
    done = 1

    style = table.Style('{:>} {:<} {:<}')
    for chunk in chunks:
        t = table.Table(style)
        t += table.Header('#', 'Owner', 'Server')
        t += table.Line()
        for i, (name, owner) in enumerate(chunk):
            if len(name) > _NAME_MAX_LEN:
                name = name[:_NAME_MAX_LEN - 1] + '…'
            if len(owner) > _OWNER_MAX_LEN:
                owner = owner[:_OWNER_MAX_LEN - 1] + '…'
            t += table.Data(i + done, owner, name)
        table_str = '```\n'+str(t)+'\n```'
        embed = discord_common.cf_color_embed(description=table_str)
        pages.append((title, embed))
        done += len(chunk)

    return pages

class Moderator(commands.Cog, description = "Control the bot with cool commands and automations"):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.converter = commands.MemberConverter()

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        pass

    @commands.slash_command(description='Bot control')
    async def meta(self, inter):
        pass

    @meta.sub_command(description='Is TLE up?')
    async def ping(self, inter):
        """Replies to a ping."""
        start = time.perf_counter()
        await inter.response.send_message(':ping_pong: Pong!')
        end = time.perf_counter()
        duration = (end - start) * 1000
        await inter.edit_original_message(content=f'REST API latency: {int(duration)}ms\n'
                                                  f'Gateway API latency: {int(self.bot.latency * 1000)}ms')

    @meta.sub_command(description='Prints bot uptime')
    async def uptime(self, inter):
        """Replies with how long TLE has been up."""
        await inter.response.send_message('TLE has been running for ' +
                       pretty_time_format(time.time() - self.start_time))

    @meta.sub_command(description='Print bot guilds')
    @commands.is_owner()
    async def guilds(self, inter):
        """Replies with info on the bot's guilds"""
        await inter.response.defer()

        guilds = [(guild.name, guild.owner.name) for guild in self.bot.guilds]
        guilds.sort(key=lambda x: (x[1]))
        title = f'I\'m in {len(guilds)} server!'

        pages = _make_pages(guilds, title)
        await paginator.paginate(self.bot, 'edit', inter, pages,
                                 message = await inter.original_message(),
                                 wait_time=_PAGINATE_WAIT_TIME, set_pagenum_footers=True)
    
    @commands.slash_command(description='(unofficial) Calculate math expressions')
    @commands.is_owner()
    async def calc(self, inter, expression: str):
        """
        Calculate a math expression.

        e.g /calc (10**9 + 7) % 17
        The bot should respond with 14

        Parameters
        ----------
        expression: Any valid math expression
        """
        try:
            await inter.response.send_message(f"{expression} = {eval(expression)}")
        except TypeError as e:
            await inter.response.send_message(e)
        except Exception as e:
            await inter.response.send_message('Invalid math expression. Please try again!')

    @commands.slash_command(description = 'Automatically create roles for CodeForces handles')
    @commands.check_any(discord_common.is_guild_owner(), commands.has_permissions(administrator = True), commands.is_owner())
    async def createrole(self, inter):
        await inter.response.defer()

        try:
            await _create_roles(inter.guild, CODEFORCES_RATED_RANKS)
            await inter.edit_original_message(embed = embed_success("Role created successfully."))
        except Exception as e:
            await inter.edit_original_message(embed = embed_alert(f"Cannot create roles: {e!r}"))

def setup(bot):
    bot.add_cog(Moderator(bot))
