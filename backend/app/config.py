import os
from pathlib import Path
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
        # Load .env file if it exists
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
        
        return Settings(
            discord_client_id=os.environ["DISCORD_CLIENT_ID"],
            discord_client_secret=os.environ["DISCORD_CLIENT_SECRET"],
            discord_bot_token=os.environ["DISCORD_BOT_TOKEN"],
            discord_redirect_uri=os.environ["DISCORD_REDIRECT_URI"],
            discord_guild_id=int(os.environ["DISCORD_GUILD_ID"]),
            session_secret=os.environ["SESSION_SECRET"],
        )

