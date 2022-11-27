import disnake

from disnake.ext import commands
from tle.util import discord_common
from tle.util import paginator

_COGS_NAMES = ["Handles", "Codeforces", "Activities", "Reminders", "Moderator"]

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = 0xff8c00 # Cheese's color XD

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        pass

    @commands.slash_command()
    async def help(self, inter, plugin: commands.option_enum(_COGS_NAMES) = None):
        """
        TLE's help assistant for Plugins Commands
        
        Parameters
        ----------
        plugin: Name of the plugin to get commands for
        """
        await inter.response.defer()

        if plugin == None:
            cogs = []
            for cog in self.bot.cogs.values():
                if cog.qualified_name in _COGS_NAMES:
                    cogs.append(cog)

            embed = disnake.Embed(color = self.color)
            avatar = self.bot.user.display_avatar.url

            embed.description = '**Tip:** A **Community Server** for **TLE** has been **opened** [HERE](https://discord.gg/eYNJsDhwdN). Come and say hi! :wave:'

            embed.set_author(name = 'TLE Plugins Commands', icon_url = avatar)
            embed.set_thumbnail(url = avatar)
            for name in _COGS_NAMES:
                embed.add_field(name = name, value = f"`/help {name.lower()}`")

            select = disnake.ui.Select()
            for cog in cogs:
                select.add_option(
                    label = cog.qualified_name,
                    description = cog.description
                )

            async def select_callback(_):
                if inter.author != _.author:
                    return await _.response.defer()
                for cog in cogs:
                    if cog.qualified_name == select.values[0]:
                        await self._send_cog_help(inter, cog)
            select.callback = select_callback

            view = disnake.ui.View()
            view.add_item(select)
            await inter.edit_original_message(embed = embed, view = view)
        else:
            for cog in self.bot.cogs.values():
                if cog.qualified_name == plugin:
                    await self._send_cog_help(inter, cog)

    async def _send_cog_help(self, inter, cog):
        cmds = []
        for cmd in cog.get_application_commands():
            try:
                ok = await cmd.can_run(inter)
                subcmd = disnake.OptionType.sub_command
                for opt in cmd.body.options:
                    ok &= opt.type != subcmd
                if ok: cmds.append(cmd)
            except commands.CommandError:
                pass

        pages = []
        for chunk in paginator.chunkify(cmds, 5):
            desc = cog.description
            for cmd in chunk:
                desc += "\n\n"
                desc += "`/" + cmd.qualified_name
                for opt in cmd.body.options:
                    if opt.required:
                        desc += f" [{opt.name}]"
                    else:
                        desc += f" ({opt.name})"
                desc += "`\n" + cmd.body.description
            embed = disnake.Embed(
                description = desc,
                color = self.color,
                title = cog.qualified_name + " Plugin"
            )
            pages.append(("", embed))
        await paginator.paginate(self.bot, 'edit', inter, pages,
            message = await inter.original_message(),
            wait_time = 5*60, set_pagenum_footers=True)

def setup(bot):
    bot.add_cog(Help(bot))
