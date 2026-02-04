import asyncio
import logging

import uvicorn

from .api import create_app
from .bot import DiscordBot
from .config import Settings

logging.basicConfig(level=logging.INFO)


def run() -> None:
    settings = Settings.from_env()
    bot = DiscordBot(guild_id=settings.discord_guild_id, command_prefix="!")
    app = create_app(bot, settings)

    async def runner() -> None:
        await bot.login(settings.discord_bot_token)
        bot_task = asyncio.create_task(bot.connect())
        server = uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
        )
        api_task = asyncio.create_task(server.serve())
        await asyncio.wait([bot_task, api_task], return_when=asyncio.FIRST_COMPLETED)

    asyncio.run(runner())


if __name__ == "__main__":
    run()
