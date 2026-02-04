import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class DiscordBot(commands.Bot):
    def __init__(self, guild_id: int, *args, **kwargs) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents, *args, **kwargs)
        self.guild_id = guild_id
        self._ready = asyncio.Event()

    async def setup_hook(self) -> None:
        logger.info("Discord bot setup complete")

    async def on_ready(self) -> None:
        logger.info("Bot logged in as %s", self.user)
        self._ready.set()

    async def wait_until_ready_safe(self) -> None:
        await self._ready.wait()

    def guild(self) -> Optional[discord.Guild]:
        return self.get_guild(self.guild_id)

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

    async def ban_user(self, member_id: int, reason: str) -> None:
        await self.wait_until_ready_safe()
        guild = self.guild()
        if guild is None:
            raise RuntimeError("Guild not available")
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
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
