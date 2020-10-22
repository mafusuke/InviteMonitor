import asyncio
import datetime
import re

import discord
import pytz
from discord.ext import commands

from main import InviteMonitor


class Invite(commands.Cog):
    """__Manage invites__"""

    def __init__(self, bot):
        self.bot = bot  # type: InviteMonitor

    async def cog_command_error(self, ctx, error):
        """コマンド処理でエラーが発生した際の処理"""
        if isinstance(error, commands.CommandOnCooldown):  # クールダウン
            await ctx.send(f":hourglass_flowing_sand: Interval too fast!\nYou can use this command again __**after {error.retry_after:.2f} sec!**__")
        elif isinstance(error, commands.MissingRequiredArgument):  # 引数不足
            await ctx.send(":placard: Missing required arguments!")
        elif isinstance(error, Exception):  # コード内で明示的に発生させているエラーを無視
            pass  # 親コマンドが呼び出されたタイミングでraiseすることで、その後のサブコマンドの処理を止めることができる
        else:  # 予期しないエラー
            await ctx.send(f":tools: Unexpected error has occurred. please contact to bot developer.\n```py{str(error)[:1900]}```")

    async def catch_user(self, user_id: int):
        """効率よくユーザーデータを取得する"""
        if (user := self.bot.get_user(user_id)) is None:  # キャッシュから取得
            try:
                user = await self.bot.fetch_user(user_id)  # APIから取得
            except:
                user = "Unknown"  # 見つからない場合 'Unknown'
        return user

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """招待が作成された際のイベント"""
        if target_channel := self.bot.db.get_log_channel_id():  # サーバーで有効化されている場合
            if invite.guild.me.guild_permissions.manage_guild:  # 権限を確認
                # 招待キャッシュを更新
                await self.bot.update_server_cache(invite.guild)
                # ログを送信
                embed = discord.Embed(title=f"{self.bot.static_data.emoji.invite_add} Invite Created", color=0x00ff7f)
                embed.description = f"Invite [{invite.code}]({invite.url}) has been created by <@{invite.inviter.id}>"
                embed.add_field(name="Channel", value=f"<#{invite.channel.id}>")  # Object型になる可能性があるので
                embed.add_field(name="MaxUses / MaxAge", value=f"{self.parse_max_uses(invite.max_uses)} times | {self.parse_max_age(invite.max_age)}")
                embed.add_field(name="Inviter", value=f"{invite.inviter}")
                await self.bot.get_channel(target_channel).send(embed=embed)
            else:  # 権限不足エラー
                pass  # TODO: manage_guild不足通知

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        """招待が削除された際のイベント"""
        if target_channel := self.bot.db.get_log_channel_id():  # サーバーで有効化されている場合
            if invite.guild.me.guild_permissions.manage_guild:  # 権限を確認
                inviter = None
                if invite.code in self.bot.cache[invite.guild.id]:  # 招待キャッシュから、招待作成者を取得
                    inviter = self.bot.cache[invite.guild.id][invite.code]['author']
                # 招待キャッシュを更新
                await self.bot.update_server_cache(invite.guild)
                # ログを送信
                embed = discord.Embed(title=f"{self.bot.static_data.emoji_invite_del} Invite Deleted", color=0xff8c00)
                embed.description = f"Invite [{invite.code}]({invite.url}) by {'<@' + str(inviter) + '>' if inviter else 'Unknown'} has deleted or expired."
                embed.add_field(name="Channel", value=f"<#{invite.channel.id}>")  # Object型になる可能性があるので
                user = await self.catch_user(inviter)  # 招待者を取得
                embed.add_field(name="Inviter", value=f"{user}")
                await self.bot.get_channel(target_channel).send(embed=embed)
                # Triggerに登録されたコードが削除されていないかどうか確認する
                if invite.code in await self.bot.db.get_code_trigger_list():
                    # 通知文を送信
                    await self.bot.get_channel(target_channel).send(f":warning: Invite `{invite.code}` was deleted, so this trigger is no longer available!")
                    await self.bot.db.remove_code_trigger(invite.guild.id, invite.code)  # 削除する
            else:  # 権限不足エラー
                pass  # TODO: manage_guild不足通知

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """メンバーが参加した際のイベント"""
        if target_channel := self.bot.db.get_log_channel_id():  # サーバーで有効化されている場合
            if member.guild.me.guild_permissions.manage_guild:  # 権限を確認
                old_invite_cache = self.bot.cache[member.guild.id]  # 前の招待キャッシュを取得
                new_invite_cache = await self.bot.update_server_cache(member.guild)  # 後の招待キャッシュを取得
                res = await self.check_invite_diff(old_invite_cache, new_invite_cache)  # 差異から招待者を特定
                # ログを送信
                embed = discord.Embed(title=f"{self.bot.static_data.emoji.member_join} Member Joined", color=0x00ffff)
                embed.set_thumbnail(url=member.avatar_url)
                if res is not None:  # ユーザーが判別できた場合
                    # 招待作成者の招待履歴に記録
                    self.bot.db.add_invited_to_inviter(member.guild.id, res[0], member.id)
                    # 招待された人の招待作成者を記録
                    self.bot.db.add_inviter_to_invited(member.guild.id, res[0], member.id)
                    inviter = await self.catch_user(res[0])  # 招待者を取得
                    # ログを送信
                    embed.description = f"<@{member.id}> has joined through [{res[1]}](https://discord.gg/{res[1]}) made by <@{inviter.id}>"
                    embed.add_field(name="User", value=f"{member}")
                    embed.add_field(name="Invite", value=f"{res[1]} | {inviter}")
                else:
                    embed.description = f"<@{member.id}> has joined"
                    embed.add_field(name="User", value=f"{member}")
                    embed.add_field(name="Invite", value=f"Unknown")
                # 参加者のアカウント作成日時を経過した時間で表示
                embed.timestamp, delta = self.get_delta_time(member.created_at, with_warn=True)
                # ログを送信
                embed.add_field(name="Account Created", value=f"{delta} ago", inline=False)
                embed.set_footer(text=f"{member.guild.name} | {len(member.guild.members)}members", icon_url=member.guild.icon_url)
                await self.bot.get_channel(target_channel).send(embed=embed)
                # UserTriggerを確認
                if member.guild.me.guild_permissions.manage_roles:  # ロール管理権限がある場合
                    if res[0] in await self.bot.db.get_user_trigger_list():  # 招待者が設定されている場合
                        roles = self.bot.db.get_user_trigger_roles(member.guild.id, res[0])
                        target_role = []
                        for role_id in roles:
                            if (role := member.guild.get_role(role_id)) is not None:
                                target_role.append(role)  # 役職を取得できた場合、追加
                        error_msg = ""
                        if not target_role:  # 役職を取得できない場合
                            await self.bot.get_channel(target_channel).send(f":warning: Roles were not found, so code trigger **{res[0]}** is no longer available!")
                            self.bot.db.remove_user_trigger(member.guild.id, res[0])
                        else:
                            try:
                                await member.add_roles(*target_role)  # リスト内のロールオブジェクトをそれぞれ指定
                            except:  # 役職の付与に失敗した場合
                                error_msg += f":x: Failed to add role `{','.join([role.name for role in target_role])}` of user trigger **{res[0]}**\nPlease check position of role! These may be higher role than I have.\n"
                        if error_msg != "":  # エラーが発生した場合
                            await self.bot.get_channel(target_channel).send(error_msg)

                    elif res[1] in await self.bot.db.get_code_trigger_roles(member.guild.id, res[1]):
                        roles = await self.bot.db.get_code_trigger_roles(member.guild.id, res[1])
                        target_role = []
                        for role_id in roles:
                            if (role := member.guild.get_role(role_id)) is not None:
                                target_role.append(role)  # 役職を取得できた場合、追加
                        error_msg = ""
                        if not target_role:  # 役職を取得できない場合
                            await self.bot.get_channel(target_channel).send(f":warning: Roles were not found, so user trigger **{res[1]}** is no longer available!")
                            del self.bot.db[str(member.guild.id)]["roles"]["code"][res[1]]  # 削除する
                        else:
                            try:
                                await member.add_roles(*target_role)  # リストのロールオブジェクトをそれぞれ指定
                            except:  # 役職の付与に失敗した場合
                                error_msg += f":x: Failed to add role `{','.join([role.name for role in target_role])}` of code trigger **{res[1]}**\nPlease check position of role! These may be higher role than I have.\n"
                        if error_msg != "":  # エラーが発生した場合
                            await self.bot.get_channel(target_channel).send(error_msg)
            else:  # 権限不足エラー
                pass  # TODO: manage_guild不足通知

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """メンバーが退出した際のイベント"""
        if member.id == self.bot.user.id:  # BOT自身がサーバーを退出した際の呼び出しを防ぐ
            return
        if target_channel := self.bot.db.get_log_channel_id():  # サーバーで有効化されている場合
            if member.guild.me.guild_permissions.manage_guild:  # 権限を確認
                # ログを送信
                embed = discord.Embed(title=f"{self.bot.static_data.emoji.member_leave} Member Left", color=0xff1493)
                embed.set_thumbnail(url=member.avatar_url)
                # メンバーがデータベース上に存在しないか、招待元がNoneの場合
                invite_from = await self.bot.db.get_user_invite_from(member.guild.id, member.id)
                if await self.bot.db.is_registered_user(member.guild.id, member.id) or invite_from:
                    embed.description = f"<@{member.id}> has left"
                    embed.add_field(name="User", value=f"{member}")
                    embed.add_field(name="Invite", value=f"Unknown")
                else:  # 招待者データがある場合
                    inviter_id = invite_from
                    invite_code = await self.bot.db.get_user_invite_code(member.guild.id, member.id)
                    inviter = await self.catch_user(inviter_id)
                    embed.description = f"<@{member.id}> invited by <@{inviter.id}> has left"
                    embed.add_field(name="User", value=f"{member}")
                    embed.add_field(name="Invite", value=f"{invite_code} | {inviter}")
                # 滞在した時間を何時間経過したかで表示
                embed.timestamp, delta = self.get_delta_time(member.joined_at)
                embed.add_field(name="Stayed Time", value=f"{delta}", inline=False)
                embed.set_footer(text=f"{member.guild.name} | {len(member.guild.members)}members", icon_url=member.guild.icon_url)
                await self.bot.get_channel(target_channel).send(embed=embed)

    @commands.Cog.listener()
    async def check_invite_diff(self, old_invites, new_invites):
        """
        招待キャッシュの差異から、招待者を取得
        :param old_invites: 前の招待キャッシュ
        :param new_invites: 後の招待キャッシュ
        :return: [招待者ID:int, 招待コード: str]
        """
        for invite in old_invites:
            if invite in new_invites:
                # NOTE: 修正したので正常に動くことを確認する必要がある
                if old_invites[invite]["uses"] != new_invites[invite]["uses"]:  # 使用回数が変わっている場合
                    return [old_invites[invite]["author"], invite]
            else:
                return [old_invites[invite]["author"], invite]  # 招待コードがなくなっている場合→使用上限回数に達した場合
        else:
            return None  # 何らかの問題で,変更点が見つからなかった場合

    @commands.command(aliases=["inv"], usage="invite (@bot)", description="Show invite URL of this BOT. If bot mentioned, send invite url of mentioned bot")
    async def invite(self, ctx):
        if not ctx.message.mentions:  # メンションがない場合、このBOTの招待リンクを表示
            await ctx.send(f"__**Add {self.bot.user.name}!**__\n{self.bot.static_data.invite}\n__**Join Official Server!**__\n{self.bot.static_data.server}")
        else:  # メンションがある場合、メンションされたBOTの招待リンクを表示
            invite_text = ""
            for target_user in ctx.message.mentions:
                if target_user.bot:
                    new_invite_text = f"__**{str(target_user)} 's invite link**__\nhttps://discord.com/oauth2/authorize?client_id={target_user.id}&scope=bot&permissions=-8\n"
                else:  # BOT出ない場合
                    new_invite_text = f"{target_user} is not the bot!\n"
                if len(invite_text + new_invite_text) > 1900:  # 1900文字を超える場合、エラー文を追加して中断
                    invite_text += "(Some invites have been omitted due to message length limit)"
                    break
                else:  # 問題ない場合、末尾に追加
                    invite_text += new_invite_text
            await ctx.send(invite_text)

    @commands.group(usage="code_trigger", description="Make trigger to give the role to users who joined with specific invite code.")
    async def code_trigger(self, ctx):
        # 設定前に権限を確認
        if not ctx.guild.me.guild_permissions.manage_roles:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(":no_entry_sign: Missing required permission **__manage_roles__**!\nPlease make sure that BOT has right access.")
            raise Exception("Permission Error")
        if not ctx.author.guild_permissions.manage_roles:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(":no_pedestrians: You don't have **__manage_roles__** permission!\nFor security reasons, this command can only be used by person who have permission.")
            raise Exception("Permission Error")
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(title="Code triggers")
            embed.description = f"If user joined through Invite **Code**, then give the **role**\ntrigger index | trigger name\nTo add/delete code trigger:\n> {self.bot.static_data.emoji.invite_add} {self.bot.PREFIX}{self.bot.get_command('code_trigger add').usage}\n> {self.bot.static_data.emoji.invite_del} {self.bot.PREFIX}{self.bot.get_command('code_trigger remove').usage}"
            count = 1
            for trigger_name in self.bot.db.get_code_trigger_list(ctx.guild.id):
                roles = self.bot.db.get_code_trigger_roles(ctx.guild.id, trigger_name)
                embed.add_field(name=f"{count} | {trigger_name}", value=",".join([f"<@&{ctx.guild.get_role(role).id}>" for role in roles]))
                count += 1
            embed.set_footer(text=f"Total {count - 1} code triggers | {ctx.guild.name}", icon_url=ctx.guild.icon_url)
            await ctx.send(embed=embed)

    @code_trigger.command(name="add", usage="user_trigger add [invite code] [@role]", description="Add new trigger. mention/ID/name are allowed to specify.")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def code_trigger_add(self, ctx, code, *, role):
        # 数を確認
        if self.bot.db.get_code_trigger_count(ctx.guild.id) == 5:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(":x: You already have 5 triggers! Please delete trigger before make new one.")
        # 招待コードを取得
        target_code = re.sub(r"(https?://)?(www.)?(discord.gg|(ptb.|canary.)?discord(app)?.com/invite)/", "", code)
        if target_code not in self.bot.cache[ctx.guild.id]:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(f"{self.bot.static_data.emoji.no_mag} Invalid code was specified.")
        target_role = self.get_roles_from_string(role, ctx.guild)
        if not target_role:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(f"{self.bot.static_data.emoji.no_mag} Role not found. Please make sure that role exists.")
        elif len(target_role) > 5:
            return await ctx.send(":x: Too many roles! You can satisfy roles up to 5.")
        if target_code in await self.bot.db.get_code_trigger_list(ctx.guild.id):  # 既に設定されている場合は確認する
            await ctx.send(f":warning: Invite code **{code}** is already configured.\n**DO YOU WANT TO OVERRIDE PREVIOUS SETTING?**\nType 'yes' to continue.")

            def check(m):
                return m.channel.id == ctx.channel.id and m.author.id == ctx.author.id

            try:
                msg = await self.bot.wait_for('message', check=check, timeout=30)
                if msg.content not in ["yes", "y", "'yes'"]:
                    return await ctx.send(":negative_squared_cross_mark: Command canceled!")
            except asyncio.TimeoutError:
                return await ctx.send(":negative_squared_cross_mark: Command canceled because no text provided for a long time.")
        await self.bot.db.add_code_trigger(ctx.guild.id, target_code, target_role)
        await ctx.send(f"{self.bot.static_data.emoji.invite_add} Code trigger has created successfully!")

    @code_trigger.command(name="remove", usage="user_trigger remove [index]", description="Delete exist trigger.", alias=["delete", "del"])
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def code_trigger_remove(self, ctx, index):
        if index.isdigit() and 1 <= int(index) <= await self.bot.db.get_code_trigger_count(ctx.guild.id):
            key_list = await self.bot.db.get_code_trigger_list(ctx.guild.id)
            await self.bot.db.remove_code_trigger(ctx.guild.id, key_list[int(index) - 1])
            await ctx.send(f"{self.bot.static_data.emoji.invite_del} Code trigger **{index}** has deleted successfully!")
        else:
            await ctx.send(f":warning: Invalid index! Please specify with integer between 1 and {len(self.bot.db[str(ctx.guild.id)]['roles']['code'])}.")

    @commands.group(usage="user_trigger", description="Make trigger to give the role to users who invited by specific user.")
    async def user_trigger(self, ctx):
        # 設定前に権限を確認
        if not ctx.guild.me.guild_permissions.manage_roles:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(":no_entry_sign: Missing required permission **__manage_roles__**!\nPlease make sure that BOT has right access.")
            raise Exception("Permission Error")
        if not ctx.author.guild_permissions.manage_roles:
            ctx.command.reset_cooldown(ctx)
            await ctx.send(":no_pedestrians: You don't have **__manage_roles__** permission!\nFor security reasons, this command can only be used by person who have permission.")
            raise Exception("Permission Error")
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(title="User triggers")
            embed.description = f"If participant invited by **user**, then give the **role**\ntrigger index | trigger name\nTo add/delete user trigger:\n> {self.bot.static_data.emoji.invite_add} {self.bot.PREFIX}{self.bot.get_command('user_trigger add').usage}\n> {self.bot.static_data.emoji.invite_del} {self.bot.PREFIX}{self.bot.get_command('user_trigger remove').usage}"
            count = 1
            for trigger_name in await self.bot.db.get_user_trigger_list(ctx.guild.id):
                roles = await self.bot.db.get_user_trigger_roles(ctx.guild.id, trigger_name)
                embed.add_field(name=f"{count} | {trigger_name}", value=",".join([f"<@&{ctx.guild.get_role(role).id}>" for role in roles]))
                count += 1
            embed.set_footer(text=f"Total {count - 1} user triggers | {ctx.guild.name}", icon_url=ctx.guild.icon_url)
            await ctx.send(embed=embed)

    @user_trigger.command(name="add", usage="user_trigger add [@user] [@role]", description="Add new trigger. mention/ID/name are allowed to specify.")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def user_trigger_add(self, ctx, user, *, role):
        # 数を確認
        if len(await self.bot.db.get_user_trigger_count(ctx.guild.id)) == 5:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(":x: You already have 5 triggers! Please delete trigger before make new one.")
        # ユーザーを取得
        target_user = self.get_user_from_string(user, ctx.guild)
        if target_user is None:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(f"{self.bot.static_data.emoji.no_mag} User not found. Please make sure that user exists.")
        target_role = self.get_roles_from_string(role, ctx.guild)
        if not target_role:
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(f"{self.bot.static_data.emoji.no_mag} Role not found. Please make sure that role exists.")
        elif len(target_role) > 5:
            return await ctx.send(":x: Too many roles! You can satisfy roles up to 5.")
        if target_user in await self.bot.db.get_user_trigger_roles(ctx.guild.id):  # 既に設定されている場合は確認する
            await ctx.send(f":warning: User **{user}** is already configured.\n**DO YOU WANT TO OVERRIDE PREVIOUS SETTING?**\nType 'yes' to continue.")

            def check(m):
                return m.channel.id == ctx.channel.id and m.author.id == ctx.author.id

            try:
                msg = await self.bot.wait_for('message', check=check, timeout=30)
                if msg.content not in ["yes", "y", "'yes'"]:
                    return await ctx.send(":negative_squared_cross_mark: Command canceled!")
            except asyncio.TimeoutError:
                return await ctx.send(":negative_squared_cross_mark: Command canceled because no text provided for a long time.")
        await self.bot.db.add_user_trigger(ctx.guild.id, target_user, target_role)
        await ctx.send(f"{self.bot.static_data.emoji.invite_add} User trigger has created successfully!")

    @user_trigger.command(name="remove", usage="user_trigger remove [index]", description="Delete exist trigger.", alias=["delete", "del"])
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def user_trigger_remove(self, ctx, index):
        if index.isdigit() and 1 <= int(index) <= len(await self.bot.db.get_user_trigger_count(ctx.guild.id)):
            key_list = list(self.bot.db[str(ctx.guild.id)]["roles"]["user"].keys())
            await self.bot.db.remove_user_trigger(ctx.guild.id, key_list[int(index) - 1])
            await ctx.send(f"{self.bot.static_data.emoji.invite_del} User trigger **{index}** has deleted successfully!")
        else:
            await ctx.send(f":warning: Invalid index! Please specify with integer between 1 and {len(self.bot.db[str(ctx.guild.id)]['roles']['user'])}.")

    def get_user_from_string(self, user_string, guild):
        target_user: str
        user = None
        if (user_match := re.match(r"<@!?(\d+)>", user_string)) is not None:  # メンションの場合
            return user_match.group()
        elif user_string.isdigit() and ((user := discord.utils.get(guild.members, id=int(user_string))) is not None):
            return str(user.id)
        else:  # 名前で検索
            if (user := discord.utils.get(guild.members, name=user_string)) is not None:
                return str(user.id)
            else:  # 見つからなかった場合
                return None

    def get_roles_from_string(self, role_text, guild):
        role_texts = role_text.split()
        roles = []
        for role_string in role_texts:  # それぞれを確認していく
            target_user: str
            role = None
            if (user_match := re.match(r"<@&(\d+)>", role_string)) is not None:  # メンションの場合
                roles.append(int(user_match.group()))
            elif role_string.isdigit() and ((role := discord.utils.get(guild.roles, id=int(role_string))) is not None):
                roles.append(role.id)
            else:  # 名前で検索
                if (role := discord.utils.get(guild.roles, name=role_string)) is not None:
                    roles.append(role.id)
        return roles

    def parse_max_uses(self, max_uses: int) -> str:
        if max_uses == 0:
            return "∞"
        else:
            return str(max_uses)

    def parse_max_age(self, max_age: int) -> str:
        if max_age == 0:
            return "∞"
        elif max_age == 1800:
            return "30 minutes"
        elif max_age == 3600:
            return "1 hour"
        elif max_age == 21600:
            return "6 hours"
        elif max_age == 43200:
            return "12 hours"
        elif max_age == 86400:
            return "1 day"

    def get_delta_time(self, base_datetime, with_warn=False):
        now = datetime.datetime.now(datetime.timezone.utc)  # JST -> UTC
        delta = now - pytz.timezone('UTC').localize(base_datetime)  # native -> aware(UTC)
        if delta.days == 0:  # 一日以内場合
            delta = f"__**{delta.seconds // 3600}hours {(delta.seconds % 3600) // 60}minutes**__"
            if with_warn:
                delta = f"__**{delta}**__"
        elif delta.days <= 7:  # 一週間以内の場合
            delta = f"**{delta.days}days {delta.seconds // 3600}hours**"
            if with_warn:
                delta = f"**{delta}**"
        else:  # それ以上の場合
            delta = f"{delta.days // 30}months {delta.seconds % 30}days"
        return [now, delta]


def setup(bot):
    bot.add_cog(Invite(bot))
