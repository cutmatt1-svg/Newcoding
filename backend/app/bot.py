import asyncio
import json
import logging
import random
from typing import Optional, Dict, List
from pathlib import Path
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

# In-memory storage (for production, use a real database)
USER_THREAD_MAP = {}  # user_id -> thread_id (ban appeals)
USER_WARNINGS = {}    # user_id -> list of warnings
USER_MUTES = {}       # user_id -> unmute_time (timestamp)
MODERATION_LOG = []   # List of all moderation actions
AUTO_ROLES = []       # List of role IDs to assign on join
IGNORED_USERS = set() # Users to ignore for moderation
USER_APPEALS = {}     # user_id -> list of appeals {reason, evidence, status, response, created_at}


class DiscordBot(commands.Bot):
    def __init__(self, guild_id: int, *args, **kwargs) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents, *args, **kwargs)
        self.guild_id = guild_id
        self._ready = asyncio.Event()
        self.current_activity = None
        self.activity_type = "playing"  # "playing", "listening", "watching", "streaming"
        self.random_voice_enabled = False
        self.random_voice_interval = 600  # seconds
        self.sound_file = None

    async def setup_hook(self) -> None:
        logger.info("Discord bot setup complete")
        # Load YUI commands cog
        await self.add_cog(YUICog(self))
        # Start random voice loop if enabled
        if not self.random_voice_task.is_running():
            self.random_voice_task.start()

    async def on_ready(self) -> None:
        logger.info("Bot logged in as %s", self.user)
        self._ready.set()

    async def on_message(self, message: discord.Message) -> None:
        """Listen for DMs and thread messages for moderation appeals."""
        # Ignore bot messages
        if message.author.bot:
            return

        # Case 1: User sends DM -> post to their appeal thread
        if isinstance(message.channel, discord.DMChannel):
            user_id = message.author.id
            if user_id in USER_THREAD_MAP:
                thread_id = USER_THREAD_MAP[user_id]
                try:
                    guild = self.guild()
                    if guild is None:
                        return
                    
                    # Find the thread
                    thread = None
                    for ch in guild.text_channels:
                        for t in ch.threads:
                            if t.id == thread_id:
                                thread = t
                                break
                    
                    if thread:
                        # Post user's message to thread
                        embed = discord.Embed(
                            title=f"💬 Nachricht von {message.author.name}",
                            description=message.content,
                            color=discord.Color.blue()
                        )
                        embed.set_footer(text=f"User: {message.author} (ID: {user_id})")
                        if message.attachments:
                            embed.add_field(name="Anhänge", value=f"{len(message.attachments)} Datei(en)")
                        await thread.send(embed=embed)
                        logger.info("Posted DM from user %s to appeal thread %s", user_id, thread_id)
                except Exception as e:
                    logger.error("Error posting DM to thread: %s", e)
            return

        # Case 2: Admin posts in appeal thread -> send as DM to user
        if isinstance(message.channel, discord.Thread):
            try:
                # Check if this thread is an appeal thread
                # Find which user this thread belongs to
                for user_id, stored_thread_id in USER_THREAD_MAP.items():
                    if stored_thread_id == message.channel.id:
                        # This is an appeal thread, send as DM to user
                        try:
                            user = await self.fetch_user(user_id)
                            admin_name = message.author.name if hasattr(message.author, 'name') else str(message.author)
                            embed = discord.Embed(
                                title=f"📨 Antwort von {admin_name}",
                                description=message.content,
                                color=discord.Color.gold()
                            )
                            embed.set_footer(text=f"Admin: {message.author} (ID: {message.author.id})")
                            if message.attachments:
                                embed.add_field(name="Anhänge", value=f"{len(message.attachments)} Datei(en)")
                            await user.send(embed=embed)
                            logger.info("Posted thread message to DM to user %s from admin %s", user_id, message.author.id)
                        except discord.Forbidden:
                            logger.warning("Could not send DM to user %s", user_id)
                        return
            except Exception as e:
                logger.error("Error processing thread message: %s", e)
                return

        # Continue with other event handlers
        await self.process_commands(message)

    async def wait_until_ready_safe(self) -> None:
        await self._ready.wait()

    def guild(self) -> Optional[discord.Guild]:
        return self.get_guild(self.guild_id)

    # ==================== MODERATION LOGGING ====================
    def log_action(self, action: str, user_id: int, moderator_id: int, reason: str = "", target_id: int = 0) -> None:
        """Log a moderation action."""
        MODERATION_LOG.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user_id": user_id,
            "moderator_id": moderator_id,
            "reason": reason,
            "target_id": target_id
        })
    
    def get_moderation_log(self, limit: int = 50) -> List[Dict]:
        """Get recent moderation actions."""
        return sorted(MODERATION_LOG, key=lambda x: x["timestamp"], reverse=True)[:limit]

    # ==================== WARN SYSTEM ====================
    def warn_user(self, user_id: int, reason: str = "No reason") -> int:
        """Warn a user. Returns warning count."""
        if user_id not in USER_WARNINGS:
            USER_WARNINGS[user_id] = []
        USER_WARNINGS[user_id].append({
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        })
        return len(USER_WARNINGS[user_id])
    
    def get_warnings(self, user_id: int) -> List[Dict]:
        """Get all warnings for a user."""
        return USER_WARNINGS.get(user_id, [])
    
    def clear_warnings(self, user_id: int) -> None:
        """Clear all warnings for a user."""
        if user_id in USER_WARNINGS:
            USER_WARNINGS[user_id] = []

    # ==================== MUTE SYSTEM ====================
    async def mute_user(self, member_id: int, duration_seconds: int, reason: str = "No reason") -> None:
        """Mute a user for specified duration."""
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        
        # Calculate unmute time
        unmute_time = datetime.now() + timedelta(seconds=duration_seconds)
        USER_MUTES[member_id] = unmute_time.isoformat()
        
        # Find or create muted role
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if muted_role is None:
            muted_role = await guild.create_role(name="Muted", reason="Mute system role")
        
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        await member.add_roles(muted_role, reason=reason)
        
        self.log_action("mute", member_id, guild.owner_id, reason)
        logger.info("Muted user %s for %d seconds", member_id, duration_seconds)

    async def unmute_user(self, member_id: int) -> None:
        """Unmute a user."""
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if muted_role is None:
            return
        
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        await member.remove_roles(muted_role, reason="Auto-unmute or manual unmute")
        
        if member_id in USER_MUTES:
            del USER_MUTES[member_id]
        
        logger.info("Unmuted user %s", member_id)

    def is_muted(self, user_id: int) -> bool:
        """Check if user is currently muted."""
        if user_id not in USER_MUTES:
            return False
        unmute_time = datetime.fromisoformat(USER_MUTES[user_id])
        if datetime.now() > unmute_time:
            del USER_MUTES[user_id]
            return False
        return True

    # ==================== AUTO-ROLES ====================
    async def set_auto_roles(self, role_ids: List[int]) -> None:
        """Set roles to auto-assign on join."""
        AUTO_ROLES.clear()
        AUTO_ROLES.extend(role_ids)
        logger.info("Auto-roles set: %s", role_ids)

    async def on_member_join(self, member: discord.Member) -> None:
        """Assign auto-roles when member joins."""
        if member.bot:
            return
        
        guild = self.guild()
        if guild is None or member.guild.id != guild.id:
            return
        
        try:
            for role_id in AUTO_ROLES:
                role = guild.get_role(role_id)
                if role:
                    await member.add_roles(role, reason="Auto-role assignment")
                    logger.info("Auto-assigned role %s to member %s", role_id, member.id)
        except Exception as e:
            logger.error("Error assigning auto-roles: %s", e)

    # ==================== MEMBER INFO ====================
    def get_member_info(self, member_id: int) -> Dict:
        """Get comprehensive info about a member."""
        return {
            "user_id": member_id,
            "warnings": self.get_warnings(member_id),
            "is_muted": self.is_muted(member_id),
            "mute_until": USER_MUTES.get(member_id),
            "in_ignore_list": member_id in IGNORED_USERS
        }

    def set_member_ignore(self, member_id: int, ignore: bool) -> None:
        """Add/remove user from ignore list."""
        if ignore:
            IGNORED_USERS.add(member_id)
        else:
            IGNORED_USERS.discard(member_id)

    async def add_role(self, member_id: int, role_id: int) -> None:
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        role = guild.get_role(role_id)
        if role is None:
            raise RuntimeError("Role not found")
        await member.add_roles(role, reason="Web dashboard role assignment")

    async def send_message(self, channel_id: int, content: str) -> None:
        await self.wait_until_ready_safe()
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("Channel is not messageable")
        await channel.send(content)

    async def post_appeal_to_thread(self, user_id: int, appeal: Dict, ping_role_name: str = "staff") -> None:
        """Post an appeal into the existing appeal thread (or create one) and ping staff role."""
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            logger.warning("Guild not available when posting appeal for %s", user_id)
            return

        thread = None
        thread_id = USER_THREAD_MAP.get(user_id)
        try:
            if thread_id:
                thread = guild.get_channel(thread_id) or await self.fetch_channel(thread_id)
        except Exception:
            thread = None

        # If no thread exists, create one in a mod-log channel
        if thread is None:
            mod_log = None
            for ch in guild.text_channels:
                if any(k in ch.name.lower() for k in ("log", "mod", "appeal")):
                    mod_log = ch
                    break
            if mod_log is None:
                mod_log = guild.text_channels[0] if guild.text_channels else None

            if mod_log:
                info_msg = await mod_log.send(f"🔔 Neuer Ban-Appeal von <@{user_id}>")
                try:
                    thread = await info_msg.create_thread(name=f"APPEAL: {user_id}", auto_archive_duration=4320)
                    USER_THREAD_MAP[user_id] = thread.id
                except Exception:
                    thread = None

        if thread is None:
            logger.warning("Could not post appeal to thread for user %s: no thread available", user_id)
            return

        # Find staff role mention
        role_mention = None
        for r in guild.roles:
            if r.name.lower() == ping_role_name.lower():
                role_mention = r.mention
                break

        embed = discord.Embed(title="🆕 Neuer Ban-Appeal", color=discord.Color.blue())
        embed.add_field(name="Benutzer", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Status", value="⏳ Ausstehend", inline=True)
        embed.add_field(name="Grund", value=appeal.get("reason", "—")[:1000])
        if appeal.get("evidence"):
            embed.add_field(name="Beweise", value=appeal.get("evidence")[:1000])
        embed.set_footer(text=f"Appeal eingereicht: {appeal.get('created_at')}")

        content = f"{role_mention + ' ' if role_mention else ''}Neuer Ban-Appeal von <@{user_id}>"
        await thread.send(content=content, embed=embed)

    async def post_moderator_response(self, user_id: int, response: str, moderator: str | None = None) -> None:
        """Post a moderator response into the appeal thread."""
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            logger.warning("Guild not available when posting moderator response for %s", user_id)
            return

        thread = None
        thread_id = USER_THREAD_MAP.get(user_id)
        try:
            if thread_id:
                thread = guild.get_channel(thread_id) or await self.fetch_channel(thread_id)
        except Exception:
            thread = None

        if thread is None:
            logger.warning("No thread found for moderator response for user %s", user_id)
            return

        embed = discord.Embed(title="💬 Moderator-Antwort", color=discord.Color.green())
        if moderator:
            embed.add_field(name="Moderator", value=moderator, inline=True)
        embed.add_field(name="Antwort", value=response[:1900])
        embed.set_footer(text=f"Antwort gesendet: {discord.utils.utcnow().isoformat()}")

        await thread.send(embed=embed)

    async def ban_user(self, member_id: int, reason: str, mod_log_channel_id: int | None = None) -> None:
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        
        # Send DM to user before banning
        try:
            dm_embed = discord.Embed(
                title="🔨 Du wurdest aus dem Server gebannt",
                color=discord.Color.red(),
                description=f"**Grund:** {reason}\n\nBitte antworte auf diese Nachricht, wenn du etwas dazu sagen möchtest."
            )
            dm_msg = await member.send(embed=dm_embed)
            # Store mapping
            USER_THREAD_MAP[member_id] = dm_msg.id
        except discord.Forbidden:
            logger.warning("Could not send DM to user %s", member_id)
        
        # Create thread in mod log if provided
        if mod_log_channel_id:
            try:
                mod_log = guild.get_channel(mod_log_channel_id) or await guild.fetch_channel(mod_log_channel_id)
                if isinstance(mod_log, discord.TextChannel):
                    # Initial info embed
                    info_embed = discord.Embed(
                        title=f"🔨 Ban: {member}",
                        color=discord.Color.red(),
                        description=f"**Gebannter Nutzer:** {member.mention} (ID: {member_id})\n**Discord-Tag:** {member}\n**Grund:** {reason}"
                    )
                    info_msg = await mod_log.send(embed=info_embed)
                    # Create thread with user's name
                    thread = await info_msg.create_thread(name=f"{member.name[:90]}", auto_archive_duration=4320)
                    USER_THREAD_MAP[member_id] = thread.id
                    # Send initial context to thread
                    context_embed = discord.Embed(
                        title="📋 Appeal-Thread Information",
                        color=discord.Color.greyple(),
                        description=f"**Nutzer:** {member.mention}\n**ID:** {member_id}\n**Grund:** {reason}\n\nBenutzern können hier antworten, um Einspruch einzulegen."
                    )
                    await thread.send(embed=context_embed)
            except Exception as e:
                logger.warning("Could not create appeal thread: %s", e)
        
        await guild.ban(member, reason=reason)

    async def create_ticket_channel(self, requester_id: int, title: str) -> int:
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        category = discord.utils.get(guild.categories, name="Tickets")
        if category is None:
            category = await guild.create_category("Tickets")
        channel_name = f"ticket-{requester_id}-{title}".lower().replace(" ", "-")[:90]
        channel = await guild.create_text_channel(channel_name, category=category)
        await channel.send(f"Ticket opened by <@{requester_id}>: **{title}**")
        return channel.id

    async def set_raid_protection(self, enabled: bool, owner_role_id: int | None = None, close_voice: bool = True, restrict_text: bool = True, alert_channel_id: int | None = None) -> None:
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")

        owner_role = None
        if owner_role_id:
            owner_role = guild.get_role(owner_role_id)

        # target the @everyone role
        everyone = guild.default_role

        # Send alert message
        if enabled and alert_channel_id:
            try:
                alert_channel = guild.get_channel(alert_channel_id) or await guild.fetch_channel(alert_channel_id)
                if isinstance(alert_channel, discord.TextChannel):
                    alert_embed = discord.Embed(
                        title="🚨 Raid-Schutz aktiviert",
                        color=discord.Color.orange(),
                        description="Raid-Schutz ist jetzt **aktiv**.\n\n"
                                    "- Nur die Owner-Rolle darf in Text-Kanälen schreiben\n"
                                    "- Alle Voice-Kanäle sind geschlossen"
                    )
                    await alert_channel.send(embed=alert_embed)
            except Exception as e:
                logger.warning("Could not send raid alert: %s", e)

        # Apply or clear permission overwrites
        if enabled:
            if restrict_text:
                overwrite_everyone = discord.PermissionOverwrite(send_messages=False)
                # deny sending for everyone
                for channel in guild.text_channels:
                    await channel.set_permissions(everyone, overwrite=overwrite_everyone)
                    if owner_role is not None:
                        await channel.set_permissions(owner_role, overwrite=discord.PermissionOverwrite(send_messages=True))

            if close_voice:
                overwrite_voice = discord.PermissionOverwrite(connect=False)
                for v in guild.voice_channels:
                    await v.set_permissions(everyone, overwrite=overwrite_voice)
        else:
            # clear overwrites we set (best-effort)
            if restrict_text:
                for channel in guild.text_channels:
                    await channel.set_permissions(everyone, overwrite=None)
                    if owner_role is not None:
                        await channel.set_permissions(owner_role, overwrite=None)
            if close_voice:
                for v in guild.voice_channels:
                    await v.set_permissions(everyone, overwrite=None)
            
            # Send deactivation alert
            if alert_channel_id:
                try:
                    alert_channel = guild.get_channel(alert_channel_id) or await guild.fetch_channel(alert_channel_id)
                    if isinstance(alert_channel, discord.TextChannel):
                        alert_embed = discord.Embed(
                            title="✅ Raid-Schutz deaktiviert",
                            color=discord.Color.green(),
                            description="Raid-Schutz ist jetzt **deaktiviert**."
                        )
                        await alert_channel.send(embed=alert_embed)
                except Exception as e:
                    logger.warning("Could not send raid alert: %s", e)
    async def set_activity(self, text: str, activity_type: str = "playing") -> None:
        """Set bot activity (playing, listening, watching, streaming)."""
        await self.wait_until_ready_safe()
        self.current_activity = text
        self.activity_type = activity_type.lower()
        
        activity_map = {
            "playing": discord.ActivityType.playing,
            "listening": discord.ActivityType.listening,
            "watching": discord.ActivityType.watching,
            "streaming": discord.ActivityType.streaming,
        }
        
        activity_obj = discord.Activity(
            type=activity_map.get(self.activity_type, discord.ActivityType.playing),
            name=text
        )
        await self.change_presence(activity=activity_obj)
        logger.info("Bot activity changed to: %s %s", activity_type, text)

    async def enable_random_voice(self, enabled: bool, interval: int = 600, sound_file: Optional[str] = None) -> None:
        """Enable/disable random voice channel joins with sound playback."""
        await self.wait_until_ready_safe()
        self.random_voice_enabled = enabled
        self.random_voice_interval = max(60, interval)  # Minimum 60 seconds
        self.sound_file = sound_file
        logger.info("Random voice enabled=%s, interval=%d, sound=%s", enabled, self.random_voice_interval, sound_file)

    @tasks.loop(seconds=120)  # Check every 2 minutes
    async def random_voice_task(self) -> None:
        """Randomly join voice channel and play sound."""
        if not self.random_voice_enabled:
            return
        
        try:
            guild = self.guild()
            if guild is None:
                return
            
            # Random chance to trigger (based on interval)
            chance = 120 / self.random_voice_interval  # interval in seconds
            if random.random() > chance:
                return
            
            # Get list of voice channels
            voice_channels = [ch for ch in guild.voice_channels if len(ch.members) > 0]
            if not voice_channels:
                logger.debug("No voice channels with members found")
                return
            
            # Pick random channel
            target_channel = random.choice(voice_channels)
            logger.info("Random voice: joining channel %s (%d members)", target_channel.name, len(target_channel.members))
            
            try:
                # Connect to voice
                voice_client = await target_channel.connect()
                
                # Play sound if available
                if self.sound_file and Path(self.sound_file).exists():
                    try:
                        audio_source = discord.FFmpegAudio(self.sound_file)
                        voice_client.play(audio_source, after=lambda e: logger.debug("Finished playing sound"))
                        # Wait for sound to finish (max 30 seconds)
                        for _ in range(30):
                            if not voice_client.is_playing():
                                break
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning("Could not play sound: %s", e)
                else:
                    await asyncio.sleep(2)  # Stay 2 seconds if no sound
                
                # Disconnect
                await voice_client.disconnect()
                logger.info("Random voice: disconnected from %s", target_channel.name)
                
            except Exception as e:
                logger.warning("Error during random voice: %s", e)
                
        except Exception as e:
            logger.error("Error in random voice task: %s", e)

    @random_voice_task.before_loop
    async def before_random_voice(self) -> None:
        await self.wait_until_ready_safe()


# ==================== YUI COMMANDS COG ====================
class YUICog(commands.Cog):
    """YUI command group for moderation and bot control."""
    
    def __init__(self, bot: DiscordBot):
        self.bot = bot
    
    @commands.group(name='yui', invoke_without_command=True, help='!yui {command}')
    async def yui_group(self, ctx):
        """Main command group."""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🎟️ YUI - Bot Control",
                description="Tippe `!yui help` für alle Commands",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

    @yui_group.command(name='warn', help='Benutzer verwarnen: !yui warn @user grund')
    @commands.has_permissions(moderate_members=True)
    async def yui_warn(self, ctx, member: discord.Member = None, *, reason: str = "No reason"):
        """Warn a user."""
        if member is None:
            await ctx.send("❌ Bitte einen Benutzer mentioned!")
            return
        
        warn_count = self.bot.warn_user(member.id, reason)
        self.bot.log_action("WARN", member.id, ctx.author.id, reason)
        
        try:
            embed = discord.Embed(
                title="⚠️ Verwarnung",
                description=f"Du wurdest verwarnt! Grund: {reason}",
                color=discord.Color.orange()
            )
            embed.add_field(name="Verwarnungen", value=f"Insgesamt: {warn_count}")
            await member.send(embed=embed)
        except discord.Forbidden:
            pass
        
        await ctx.send(f"✅ {member.mention} verwarnt! (Verwarnungen: {warn_count})")

    @yui_group.command(name='mute', help='Benutzer stummschalten: !yui mute @user 3600 grund')
    @commands.has_permissions(moderate_members=True)
    async def yui_mute(self, ctx, member: discord.Member = None, duration: int = 3600, *, reason: str = "No reason"):
        """Mute a user."""
        if member is None:
            await ctx.send("❌ Bitte einen Benutzer mentioned!")
            return
        
        self.bot.mute_user(member.id, duration, reason)
        self.bot.log_action("MUTE", member.id, ctx.author.id, reason)
        
        try:
            embed = discord.Embed(
                title="🔇 Stummgeschaltet",
                description=f"Du wurdest stummgeschaltet für {duration}s. Grund: {reason}",
                color=discord.Color.red()
            )
            await member.send(embed=embed)
        except discord.Forbidden:
            pass
        
        await ctx.send(f"✅ {member.mention} stummgeschaltet für {duration}s")

    @yui_group.command(name='unmute', help='Stummschaltung aufheben: !yui unmute @user')
    @commands.has_permissions(moderate_members=True)
    async def yui_unmute(self, ctx, member: discord.Member = None):
        """Unmute a user."""
        if member is None:
            await ctx.send("❌ Bitte einen Benutzer mentioned!")
            return
        
        self.bot.unmute_user(member.id)
        self.bot.log_action("UNMUTE", member.id, ctx.author.id)
        
        try:
            await member.send("✅ Deine Stummschaltung wurde aufgehoben!")
        except discord.Forbidden:
            pass
        
        await ctx.send(f"✅ {member.mention} stummschaltung aufgehoben")

    @yui_group.command(name='ban', help='Benutzer bannen mit Appeal-System: !yui ban @user grund')
    @commands.has_permissions(ban_members=True)
    async def yui_ban(self, ctx, member: discord.Member = None, *, reason: str = "No reason"):
        """Ban a user with Appeal System - DM sent BEFORE ban."""
        if member is None:
            await ctx.send("❌ Bitte einen Benutzer mentioned!")
            return
        
        await ctx.send(f"⏳ Bannen von {member.mention} mit Appeal-System...")
        
        guild = self.bot.guild()
        if guild is None:
            await ctx.send("❌ Guild nicht gefunden")
            return
        
        # ==================== STEP 1: Send DM to user BEFORE banning ====================
        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title="🔨 Du wurdest aus dem Server gebannt",
                color=discord.Color.red(),
                description=f"**Grund:** {reason}\n\n"
                           f"**Appeals-System - Web Dashboard:**\n"
                           f"Du kannst im Web-Dashboard Einspruch gegen deinen Ban einlegen.\n\n"
                           f"**So funktioniert es:**\n"
                           f"1️⃣ Besuche das Control Panel\n"
                           f"2️⃣ Melde dich mit deinem Discord-Account an\n"
                           f"3️⃣ Gehe zum \"Ban Appeal\" Bereich\n"
                           f"4️⃣ Reiche deinen Appeal mit Begründung ein\n"
                           f"5️⃣ Ein Moderator wird deinen Appeal überprüfen\n\n"
                           f"**ℹ️ Hinweis:** Du kannst das Dashboard öffnen, auch wenn du gebannt bist.",
                timestamp=discord.utils.utcnow()
            )
            dm_msg = await member.send(embed=dm_embed)
            dm_sent = True
            await ctx.send(f"✅ Ban-DM mit Web-Dashboard Appeal-Info gesendet")
            
            # Store mapping for appeal system
            from .bot import USER_THREAD_MAP
            USER_THREAD_MAP[member.id] = dm_msg.id
            
        except discord.Forbidden:
            await ctx.send(f"⚠️ Konnte DM an {member.mention} nicht senden (DMs deaktiviert)")
            await ctx.send(f"   → Benutzer kann trotzdem im Web-Dashboard Appeal einreichen")
            dm_sent = True  # Continue anyway
        
        # ==================== STEP 2: Create appeal thread ====================
        appeal_thread = None
        try:
            # Find mod-log channel or use first text channel
            mod_log = None
            for ch in guild.text_channels:
                if 'log' in ch.name.lower() or 'mod' in ch.name.lower() or 'appeal' in ch.name.lower():
                    mod_log = ch
                    break
            
            if mod_log is None:
                mod_log = guild.text_channels[0] if guild.text_channels else None
            
            if mod_log:
                # Create info message with full details
                info_embed = discord.Embed(
                    title=f"🔨 BAN-APPEAL: {member}",
                    color=discord.Color.red(),
                    description=f"**Gebannter Nutzer:** {member.mention} (ID: {member.id})\n"
                               f"**Discord-Tag:** {member}\n"
                               f"**Grund:** {reason}\n"
                               f"**Moderator:** {ctx.author.mention}\n"
                               f"**Gebannt am:** {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}"
                )
                info_msg = await mod_log.send(embed=info_embed)
                
                # Create appeal thread
                appeal_thread = await info_msg.create_thread(
                    name=f"APPEAL: {member.name[:80]}",
                    auto_archive_duration=4320
                )
                
                # Store thread mapping for bidirectional sync
                USER_THREAD_MAP[member.id] = appeal_thread.id
                
                # Send detailed instructions to thread
                context_embed = discord.Embed(
                    title="📋 Appeal-Thread Anleitung",
                    color=discord.Color.blue(),
                    description=f"**Gebannter Nutzer:** {member.mention}\n"
                               f"**ID:** {member.id}\n"
                               f"**Grund:** {reason}\n\n"
                               f"**Appeal-Kommunikation:**\n"
                               f"⚠️ **Hinweis:** Gebannte User können nicht direkt antworten.\n"
                               f"**Solution:** User kann im Web-Dashboard einen Appeal einreichen oder Moderatoren können hier direkt antworten\n\n"
                               f"**Admin antwortet hier** → wird später als Notification zum User gesendet\n\n"
                               f"**Moderator-Info:**\n"
                               f"Banned von: {ctx.author.mention}\n"
                               f"Banzeit: {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}"
                )
                await appeal_thread.send(embed=context_embed)
                
                # Send pinned message with instructions
                instructions_embed = discord.Embed(
                    title="📌 Anleitung für den Gebannten Nutzer",
                    color=discord.Color.green(),
                    description=f"Der gebannte Nutzer hat eine **Ban-DM mit Appeal-Informationen** erhalten.\n\n"
                               f"**Er kann jetzt:**\n"
                               f"1. Im Web-Dashboard einloggen\n"
                               f"2. Ein Appeal einreichen mit Begründung\n"
                               f"3. Auf Moderator-Antwort warten\n\n"
                               f"**Thread wird automatisch archiviert in:** 3 Tagen"
                )
                instructions_msg = await appeal_thread.send(embed=instructions_embed)
                try:
                    await instructions_msg.pin()
                except:
                    pass
                
                await ctx.send(f"🎟️ Appeal-Thread erstellt: {appeal_thread.mention}")
                
        except Exception as e:
            await ctx.send(f"⚠️ Fehler beim Erstellen des Appeal-Threads: {str(e)}")
            logger.error("Could not create appeal thread: %s", e)
        
        # ==================== STEP 3: Actually ban the user ====================
        try:
            await member.ban(reason=reason)
            
            # Log the ban action
            self.bot.log_action("BAN", member.id, ctx.author.id, reason)
            
            # Final confirmation
            await ctx.send(f"\n✅ **Ban durchgeführt!**")
            await ctx.send(f"   • Benutzer: {member.mention}")
            await ctx.send(f"   • Grund: {reason}")
            if dm_sent:
                await ctx.send(f"   • 📨 Ban-DM gesendet")
            if appeal_thread:
                await ctx.send(f"   • 🎟️ Appeal-Thread: {appeal_thread.mention}")
            
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Bannen: {str(e)}")
            logger.error("Error banning user: %s", e)




    @yui_group.command(name='role', help='Rolle vergeben: !yui role @user @rolle')
    @commands.has_permissions(manage_roles=True)
    async def yui_role(self, ctx, member: discord.Member = None, *, role_name: str = None):
        """Add role to user."""
        if member is None or role_name is None:
            await ctx.send("❌ Syntax: !yui role @user @rolle")
            return
        
        # Find role by name or mention
        role = discord.utils.find(lambda r: r.name == role_name or str(r.id) == role_name, ctx.guild.roles)
        if not role:
            await ctx.send(f"❌ Rolle '{role_name}' nicht gefunden!")
            return
        
        try:
            await member.add_roles(role)
            self.bot.log_action("ROLE_ADD", member.id, ctx.author.id, f"Role: {role.name}")
            await ctx.send(f"✅ {member.mention} hat jetzt die Rolle {role.mention}!")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {str(e)}")

    @yui_group.command(name='message', help='Nachricht senden: !yui message #kanal text')
    @commands.has_permissions(send_messages=True)
    async def yui_message(self, ctx, channel: discord.TextChannel = None, *, content: str = None):
        """Send a message to a channel."""
        if channel is None or content is None:
            await ctx.send("❌ Syntax: !yui message #kanal Nachricht")
            return
        
        try:
            await channel.send(content)
            self.bot.log_action("MESSAGE_SENT", ctx.author.id, ctx.author.id, f"Channel: {channel.name}")
            await ctx.send(f"✅ Nachricht in {channel.mention} gesendet!")
        except Exception as e:
            await ctx.send(f"❌ Fehler: {str(e)}")

    @yui_group.command(name='activity', help='Activity setzen: !yui activity playing text')
    @commands.has_permissions(administrator=True)
    async def yui_activity(self, ctx, activity_type: str = "playing", *, text: str = None):
        """Set bot activity."""
        if text is None:
            await ctx.send("❌ Syntax: !yui activity [playing|listening|watching] Text")
            return
        
        valid_types = ["playing", "listening", "watching", "streaming"]
        if activity_type.lower() not in valid_types:
            await ctx.send(f"❌ Typ muss einer dieser sein: {', '.join(valid_types)}")
            return
        
        await self.bot.set_activity(text, activity_type.lower())
        await ctx.send(f"✅ Activity gesetzt: {activity_type.capitalize()} {text}")

    @yui_group.command(name='randomvoice', help='Random Voice: !yui randomvoice on 600')
    @commands.has_permissions(administrator=True)
    async def yui_randomvoice(self, ctx, enabled: str = None, interval: int = 600):
        """Enable/disable random voice."""
        if enabled is None:
            await ctx.send("❌ Syntax: !yui randomvoice [on|off] [interval_seconds]")
            return
        
        is_enabled = enabled.lower() in ["on", "yes", "true", "1"]
        await self.bot.enable_random_voice(is_enabled, interval)
        status = "🟢 Aktiviert" if is_enabled else "🔴 Deaktiviert"
        await ctx.send(f"✅ Random Voice {status} (Intervall: {interval}s)")

    @yui_group.command(name='raid', help='Raid-Schutz: !yui raid on')
    @commands.has_permissions(administrator=True)
    async def yui_raid(self, ctx, enabled: str = None):
        """Enable/disable raid protection."""
        if enabled is None:
            await ctx.send("❌ Syntax: !yui raid [on|off]")
            return
        
        is_enabled = enabled.lower() in ["on", "yes", "true", "1"]
        owner_role = discord.utils.find(lambda r: r.name == "Owner", ctx.guild.roles)
        
        await self.bot.set_raid_protection(
            is_enabled,
            owner_role.id if owner_role else None,
            None,
            close_voice_channels=True,
            restrict_text_channels=True
        )
        
        status = "🟢 Aktiviert" if is_enabled else "🔴 Deaktiviert"
        await ctx.send(f"✅ Raid-Schutz {status}")

    @yui_group.command(name='autorole', help='Autorollen: !yui autorole @rolle')
    @commands.has_permissions(manage_roles=True)
    async def yui_autorole(self, ctx, role: discord.Role = None):
        """Add auto-role."""
        if role is None:
            await ctx.send("❌ Syntax: !yui autorole @rolle")
            return
        
        if role.id not in AUTO_ROLES:
            AUTO_ROLES.append(role.id)
            self.bot.log_action("AUTO_ROLE_ADD", role.id, ctx.author.id)
            await ctx.send(f"✅ {role.mention} wird jetzt automatisch zugewiesen!")
        else:
            await ctx.send(f"⚠️ {role.mention} ist bereits in der Autorollen-Liste!")

    @yui_group.command(name='ticket', help='Ticket: !yui ticket Titel')
    @commands.has_permissions(send_messages=True)
    async def yui_ticket(self, ctx, *, title: str = None):
        """Create a ticket."""
        if title is None:
            await ctx.send("❌ Syntax: !yui ticket Titel")
            return
        
        try:
            category = discord.utils.find(lambda c: c.name.lower() == "tickets", ctx.guild.categories)
            if not category:
                category = await ctx.guild.create_category("Tickets")
            
            # Create channel
            channel = await category.create_text_channel(f"ticket-{ctx.author.name}")
            await channel.set_permissions(ctx.author, send_messages=True, read_messages=True)
            await channel.set_permissions(ctx.guild.default_role, send_messages=False, read_messages=False)
            
            # Send info
            embed = discord.Embed(
                title=f"🎟️ {title}",
                description=f"Erstellt von: {ctx.author.mention}",
                color=discord.Color.blue()
            )
            await channel.send(embed=embed)
            await channel.send(f"{ctx.author.mention} Dein Ticket wurde erstellt!")
            
            self.bot.log_action("TICKET_CREATED", ctx.author.id, ctx.author.id, title)
            await ctx.send(f"✅ Ticket erstellt: {channel.mention}")
            
        except Exception as e:
            await ctx.send(f"❌ Fehler: {str(e)}")

    @yui_group.command(name='help', help='Commands anzeigen')
    async def yui_help(self, ctx):
        """Show all available commands."""
        embed = discord.Embed(
            title="🎟️ YUI Command List",
            description="Alle Discord-Commands für Moderation und Bot-Einstellungen",
            color=discord.Color.blue()
        )
        
        commands_info = [
            ("Moderation", [
                ("!yui warn @user grund", "⚠️ Benutzer verwarnen"),
                ("!yui mute @user 3600 grund", "🔇 Benutzer stummschalten"),
                ("!yui unmute @user", "🔊 Stummschaltung aufheben"),
                ("!yui ban @user grund", "🔨 Benutzer bannen"),
                ("!yui role @user @rolle", "🏷️ Rolle vergeben"),
            ]),
            ("Nachrichten & Tickets", [
                ("!yui message #kanal text", "💬 Nachricht senden"),
                ("!yui ticket titel", "🎟️ Ticket erstellen"),
            ]),
            ("Bot Einstellungen", [
                ("!yui activity [type] text", "🎵 Activity setzen"),
                ("!yui randomvoice [on|off] intervall", "🎲 Random Voice"),
                ("!yui raid [on|off]", "🚨 Raid-Schutz"),
                ("!yui autorole @rolle", "✨ Autorollen"),
            ]),
        ]
        
        for category, cmds in commands_info:
            cmd_list = "\n".join([f"`{cmd}` - {desc}" for cmd, desc in cmds])
            embed.add_field(name=category, value=cmd_list, inline=False)
        
        embed.set_footer(text="Weitere Infos: !help")
        await ctx.send(embed=embed)

    # ==================== TEST COMMANDS ====================
    
    @yui_group.group(name='test', invoke_without_command=True, help='Test-Commands für Moderation')
    async def yui_test(self, ctx):
        """Test commands for moderation features."""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🧪 YUI - Test Commands",
                description="Verfügbare Test-Commands:",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Commands",
                value=(
                    "`!yui test warn @user` - Test Warn-System\n"
                    "`!yui test mute @user` - Test Mute-System\n"
                    "`!yui test unmute @user` - Test Unmute-System\n"
                    "`!yui test ban @user` - Test Ban-System\n"
                    "`!yui test role @user @rolle` - Test Role-System"
                ),
                inline=False
            )
            await ctx.send(embed=embed)

    @yui_test.command(name='warn', help='Test: Benutzer verwarnen')
    async def test_warn(self, ctx, member: discord.Member = None):
        """Test warn command."""
        if member is None:
            await ctx.send("❌ Syntax: !yui test warn @user")
            return
        
        await ctx.send(f"🧪 **Test: WARN ausgelöst**")
        await ctx.send(f"📋 Details:")
        await ctx.send(f"  • Benutzer: {member.mention} (ID: {member.id})")
        await ctx.send(f"  • Grund: Test-Verwarnung")
        
        warn_count = self.bot.warn_user(member.id, "Test-Verwarnung")
        self.bot.log_action("WARN", member.id, ctx.author.id, "Test-Verwarnung")
        
        await ctx.send(f"✅ Test erfolgreich! Verwarnungen gesamt: **{warn_count}**")
        
        try:
            embed = discord.Embed(
                title="⚠️ Test - Verwarnung",
                description="Dies ist eine Test-Verwarnung vom Bot-Tester!",
                color=discord.Color.orange()
            )
            embed.add_field(name="Grund", value="Test-Verwarnung")
            embed.add_field(name="Verwarnungen", value=f"Insgesamt: {warn_count}")
            embed.set_footer(text="Dies ist eine Test-Nachricht")
            await member.send(embed=embed)
            await ctx.send(f"📨 DM an {member.mention} gesendet!")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Konnte DM an {member.mention} nicht senden (DMs deaktiviert)")

    @yui_test.command(name='mute', help='Test: Benutzer stummschalten')
    async def test_mute(self, ctx, member: discord.Member = None, duration: int = 60):
        """Test mute command."""
        if member is None:
            await ctx.send("❌ Syntax: !yui test mute @user [dauer_sekunden]")
            return
        
        await ctx.send(f"🧪 **Test: MUTE ausgelöst**")
        await ctx.send(f"📋 Details:")
        await ctx.send(f"  • Benutzer: {member.mention} (ID: {member.id})")
        await ctx.send(f"  • Dauer: {duration} Sekunden")
        await ctx.send(f"  • Grund: Test-Stummschaltung")
        
        self.bot.mute_user(member.id, duration, "Test-Stummschaltung")
        self.bot.log_action("MUTE", member.id, ctx.author.id, "Test-Stummschaltung")
        
        await ctx.send(f"✅ Test erfolgreich! Benutzer stummgeschaltet für {duration}s")
        
        try:
            embed = discord.Embed(
                title="🔇 Test - Stummgeschaltet",
                description=f"Du wurdest stummgeschaltet für {duration} Sekunden!",
                color=discord.Color.red()
            )
            embed.add_field(name="Grund", value="Test-Stummschaltung")
            embed.set_footer(text="Dies ist eine Test-Nachricht")
            await member.send(embed=embed)
            await ctx.send(f"📨 DM an {member.mention} gesendet!")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Konnte DM an {member.mention} nicht senden (DMs deaktiviert)")

    @yui_test.command(name='unmute', help='Test: Stummschaltung aufheben')
    async def test_unmute(self, ctx, member: discord.Member = None):
        """Test unmute command."""
        if member is None:
            await ctx.send("❌ Syntax: !yui test unmute @user")
            return
        
        await ctx.send(f"🧪 **Test: UNMUTE ausgelöst**")
        await ctx.send(f"📋 Details:")
        await ctx.send(f"  • Benutzer: {member.mention} (ID: {member.id})")
        
        self.bot.unmute_user(member.id)
        self.bot.log_action("UNMUTE", member.id, ctx.author.id)
        
        await ctx.send(f"✅ Test erfolgreich! Stummschaltung aufgehoben")
        
        try:
            embed = discord.Embed(
                title="🔊 Test - Stummschaltung aufgehoben",
                description="Deine Stummschaltung wurde aufgehoben!",
                color=discord.Color.green()
            )
            embed.set_footer(text="Dies ist eine Test-Nachricht")
            await member.send(embed=embed)
            await ctx.send(f"📨 DM an {member.mention} gesendet!")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Konnte DM an {member.mention} nicht senden (DMs deaktiviert)")

    @yui_test.command(name='ban', help='Test: Ban-Appeal-System (NICHT ECHT)')
    async def test_ban(self, ctx, member: discord.Member = None):
        """Test complete ban system with appeal thread and DM sync."""
        if member is None:
            await ctx.send("❌ Syntax: !yui test ban @user")
            return
        
        await ctx.send(f"🧪 **Test: BAN-SYSTEM ausgelöst (SIMULIERT)**")
        await ctx.send(f"📋 Details:")
        await ctx.send(f"  • Benutzer: {member.mention} (ID: {member.id})")
        await ctx.send(f"  • Grund: Test-Ban mit Appeal-System")
        await ctx.send(f"⚠️ **SIMULATION**: Der Benutzer wird NICHT wirklich gebannt!")
        
        self.bot.log_action("BAN", member.id, ctx.author.id, "Test-Ban")
        
        # 1. Send DM to user
        try:
            dm_embed = discord.Embed(
                title="🔨 Du wurdest aus dem Server gebannt",
                color=discord.Color.red(),
                description=f"**Grund:** Test-Ban mit Appeal-System\n\n**Dies ist eine Simulation!** Du bist NICHT wirklich gebannt.\n\nBitte schreib eine Antwort auf diese Nachricht, um einen Appeal einzureichen. Deine Nachricht wird im Appeal-Thread gepostet.",
                timestamp=discord.utils.utcnow()
            )
            dm_msg = await member.send(embed=dm_embed)
            await ctx.send(f"📨 DM an {member.mention} gesendet!")
            await ctx.send(f"   → Benutzer kann jetzt DM-Nachrichten senden")
            
            # Store mapping for appeal
            from .bot import USER_THREAD_MAP
            USER_THREAD_MAP[member.id] = dm_msg.id
            
        except discord.Forbidden:
            await ctx.send(f"⚠️ Konnte DM an {member.mention} nicht senden (DMs deaktiviert)")
            return
        
        # 2. Create appeal thread in mod-log or announcement channel
        try:
            guild = self.bot.guild()
            if guild is None:
                await ctx.send("❌ Guild nicht gefunden")
                return
            
            # Find mod-log or use first text channel
            mod_log = None
            for ch in guild.text_channels:
                if 'log' in ch.name.lower() or 'mod' in ch.name.lower():
                    mod_log = ch
                    break
            
            if mod_log is None:
                mod_log = guild.text_channels[0] if guild.text_channels else None
            
            if mod_log is None:
                await ctx.send("❌ Kein passender Channel für Appeal-Thread gefunden")
                return
            
            # Create initial message with info
            info_embed = discord.Embed(
                title=f"🔨 TEST-BAN: {member}",
                color=discord.Color.red(),
                description=f"**TEST - Benutzer wurde NICHT gebannt!**\n\n**Gebannter Nutzer:** {member.mention} (ID: {member.id})\n**Discord-Tag:** {member}\n**Grund:** Test-Ban mit Appeal-System"
            )
            info_msg = await mod_log.send(embed=info_embed)
            
            # Create thread
            thread = await info_msg.create_thread(
                name=f"TEST-APPEAL: {member.name[:80]}",
                auto_archive_duration=4320
            )
            
            # Store thread mapping
            USER_THREAD_MAP[member.id] = thread.id
            
            # Send context to thread
            context_embed = discord.Embed(
                title="📋 Appeal-Thread Anleitung",
                color=discord.Color.greyple(),
                description=f"**Gebannter Nutzer:** {member.mention}\n**ID:** {member.id}\n**Grund:** Test-Ban mit Appeal-System\n\n"
                           f"**Wie das Appeal-System funktioniert:**\n"
                           f"1️⃣ Benutzer sendet DM an den Bot\n"
                           f"2️⃣ Die DM wird hier im Thread gepostet\n"
                           f"3️⃣ Admin antwortet hier im Thread\n"
                           f"4️⃣ Die Admin-Antwort wird als DM an den Benutzer gesendet\n\n"
                           f"**Dies ist ein TEST** - kein echter Ban!"
            )
            await thread.send(embed=context_embed)
            
            await ctx.send(f"🎟️ Appeal-Thread erstellt: {thread.mention}")
            await ctx.send(f"   → Benutzer kann jetzt über DM kommunizieren")
            await ctx.send(f"   → Admins können hier im Thread antworten")
            await ctx.send(f"   → Antworten werden bidirektional synchronisiert")
            
        except Exception as e:
            await ctx.send(f"⚠️ Fehler beim Erstellen des Appeal-Threads: {str(e)}")
            return
        
        await ctx.send(f"\n✅ **Test erfolgreich abgeschlossen!**")
        await ctx.send(f"**Nächste Schritte zum Testen:**")
        await ctx.send(f"1. {member.mention} sendet eine DM an den Bot")
        await ctx.send(f"2. Die DM erscheint im Appeal-Thread: {thread.mention}")
        await ctx.send(f"3. Admin antwortet im Thread")
        await ctx.send(f"4. Antwort wird als DM zu {member.mention} zurück gesendet")


    @yui_test.command(name='role', help='Test: Rolle vergeben')
    async def test_role(self, ctx, member: discord.Member = None, *, role_name: str = None):
        """Test role command."""
        if member is None or role_name is None:
            await ctx.send("❌ Syntax: !yui test role @user @rolle")
            return
        
        # Find role by name or mention
        role = discord.utils.find(lambda r: r.name == role_name or str(r.id) == role_name, ctx.guild.roles)
        if not role:
            await ctx.send(f"❌ Rolle '{role_name}' nicht gefunden!")
            return
        
        await ctx.send(f"🧪 **Test: ROLE vergeben ausgelöst**")
        await ctx.send(f"📋 Details:")
        await ctx.send(f"  • Benutzer: {member.mention} (ID: {member.id})")
        await ctx.send(f"  • Rolle: {role.mention} (ID: {role.id})")
        
        try:
            await member.add_roles(role)
            self.bot.log_action("ROLE_ADD", member.id, ctx.author.id, f"Role: {role.name}")
            await ctx.send(f"✅ Test erfolgreich! {role.mention} zu {member.mention} hinzugefügt")
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Hinzufügen der Rolle: {str(e)}")
