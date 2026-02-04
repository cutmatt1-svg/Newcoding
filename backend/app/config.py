import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_client_id: str
    discord_client_secret: str
    discord_bot_token: str
    discord_redirect_uri: str
    discord_guild_id: int
    session_secret: str


    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            discord_client_id=os.environ["DISCORD_CLIENT_ID"],
            discord_client_secret=os.environ["DISCORD_CLIENT_SECRET"],
            discord_bot_token=os.environ["DISCORD_BOT_TOKEN"],
            discord_redirect_uri=os.environ["DISCORD_REDIRECT_URI"],
            discord_guild_id=int(os.environ["DISCORD_GUILD_ID"]),
            session_secret=os.environ["SESSION_SECRET"],
        )
