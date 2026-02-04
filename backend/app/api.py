import logging
from pathlib import Path
from typing import Any, Dict

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .bot import DiscordBot
from .config import Settings

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api"


def get_settings() -> Settings:
    return Settings.from_env()


def require_user(request: Request) -> Dict[str, Any]:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def create_app(bot: DiscordBot, settings: Settings) -> FastAPI:
    app = FastAPI(title="Discord Control Panel")
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    @app.get("/auth/login")
    async def login() -> RedirectResponse:
        params = {
            "client_id": settings.discord_client_id,
            "redirect_uri": settings.discord_redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
        }
        url = httpx.URL("https://discord.com/api/oauth2/authorize").copy_add_params(params)
        return RedirectResponse(str(url))

    @app.get("/auth/callback")
    async def callback(request: Request, code: str) -> RedirectResponse:
        data = {
            "client_id": settings.discord_client_id,
            "client_secret": settings.discord_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.discord_redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(f"{DISCORD_API_BASE}/oauth2/token", data=data, headers=headers)
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data["access_token"]
            user_resp = await client.get(
                f"{DISCORD_API_BASE}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
        request.session["user"] = user_resp.json()
        return RedirectResponse("/")

    @app.get("/api/me")
    async def me(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        return user

    @app.post("/api/roles/add")
    async def add_role(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        member_id = int(payload.get("member_id", 0))
        role_id = int(payload.get("role_id", 0))
        if member_id <= 0 or role_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member or role ID")
        await bot.add_role(member_id, role_id)
        logger.info("User %s assigned role %s to member %s", user.get("id"), role_id, member_id)
        return {"status": "ok"}

    @app.post("/api/message/send")
    async def send_message(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        channel_id = int(payload.get("channel_id", 0))
        content = str(payload.get("content", "")).strip()
        if channel_id <= 0 or not content:
            raise HTTPException(status_code=400, detail="Invalid channel or content")
        await bot.send_message(channel_id, content)
        logger.info("User %s sent message to channel %s", user.get("id"), channel_id)
        return {"status": "ok"}

    @app.post("/api/moderation/ban")
    async def ban_user(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        member_id = int(payload.get("member_id", 0))
        reason = str(payload.get("reason", "Moderation action"))
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        await bot.ban_user(member_id, reason)
        logger.info("User %s banned member %s", user.get("id"), member_id)
        return {"status": "ok"}

    @app.post("/api/tickets")
    async def create_ticket(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        title = str(payload.get("title", "Support"))
        channel_id = await bot.create_ticket_channel(int(user["id"]), title)
        logger.info("User %s created ticket %s", user.get("id"), channel_id)
        return {"status": "ok", "channel_id": channel_id}

    return app
