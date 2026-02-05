import logging
from pathlib import Path
from typing import Any, Dict, List

import httpx
import discord
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

    @app.get("/auth/login")
    async def login() -> RedirectResponse:
        params = {
            "client_id": settings.discord_client_id,
            "redirect_uri": settings.discord_redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
        }
        # httpx.URL has no `copy_add_params`; add params one-by-one
        url = httpx.URL("https://discord.com/api/oauth2/authorize")
        for k, v in params.items():
            url = url.copy_add_param(k, v)
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

    @app.get("/auth/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.pop("user", None)
        return RedirectResponse("/")

    @app.get("/api/me")
    async def me(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Return Discord user info and guild membership info (roles + is_admin flag if available)."""
        result = dict(user)
        user_id = user.get("id", "unknown")
        logger.info("GET /api/me for user %s", user_id)
        try:
            guild = bot.guild()
            logger.info("  Guild available: %s", guild is not None)
            if guild:
                member = None
                try:
                    # find member in guild
                    mid = int(user_id)
                    logger.info("  Trying to fetch member %d from guild", mid)
                    member = guild.get_member(mid)
                    if not member:
                        logger.info("  Member not in cache, fetching from Discord API...")
                        member = await guild.fetch_member(mid)
                    logger.info("  Member found: %s", member is not None)
                except Exception as fetch_err:
                    logger.warning("  Could not fetch member %s from guild: %s", user_id, fetch_err, exc_info=True)
                    member = None
                
                if member:
                    logger.info("  Processing member %s", member.display_name)
                    roles = [ {"id": r.id, "name": r.name} for r in member.roles if r.name != "@everyone" ]
                    # determine admin/moderator: owner OR manage_guild OR role name contains admin/mod/staff
                    is_admin = False
                    # Check if guild owner
                    if guild.owner_id == member.id:
                        is_admin = True
                        logger.info("  User %s is admin via guild owner", user_id)
                    # Check manage_guild permission
                    elif member.guild_permissions and getattr(member.guild_permissions, "manage_guild", False):
                        is_admin = True
                        logger.info("  User %s is admin via manage_guild permission", user_id)
                    # Check admin/mod/staff roles
                    else:
                        for r in member.roles:
                            if any(k in r.name.lower() for k in ("admin","mod","staff")):
                                is_admin = True
                                logger.info("  User %s is admin via role: %s", user_id, r.name)
                                break
                    result["member"] = {
                        "id": member.id,
                        "display_name": member.display_name,
                        "roles": roles,
                        "is_admin": is_admin
                    }
                    logger.info("  /api/me complete: display_name=%s roles=%d is_admin=%s", member.display_name, len(roles), is_admin)
                else:
                    logger.warning("  Member %s not found in guild", user_id)
            else:
                logger.warning("  Guild not available for /api/me")
        except Exception as e:
            logger.warning("  Exception in /api/me: %s", e, exc_info=True)
        return result

    @app.post("/api/roles/add")
    async def add_role(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        try:
            member_id = int(payload.get("member_id", 0))
            role_id = int(payload.get("role_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member or role ID (must be valid numbers)")
        if member_id <= 0 or role_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member or role ID (must be > 0)")
        try:
            await bot.add_role(member_id, role_id)
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="Mitglied oder Rolle nicht gefunden")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="Keine Berechtigung - Bot hat keine Rechte")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")
        logger.info("User %s assigned role %s to member %s", user.get("id"), role_id, member_id)
        return {"status": "ok"}

    @app.post("/api/message/send")
    async def send_message(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        try:
            channel_id = int(payload.get("channel_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid channel ID (must be a valid number)")
        content = str(payload.get("content", "")).strip()
        if channel_id <= 0 or not content:
            raise HTTPException(status_code=400, detail="Invalid channel or content")
        try:
            await bot.send_message(channel_id, content)
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="Kanal nicht gefunden")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="Keine Berechtigung - Bot kann nicht schreiben")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")
        logger.info("User %s sent message to channel %s", user.get("id"), channel_id)
        return {"status": "ok"}

    @app.post("/api/moderation/ban")
    async def ban_user(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        try:
            member_id = int(payload.get("member_id", 0))
            mod_log_channel_id = payload.get("mod_log_channel_id")
            if mod_log_channel_id is not None:
                mod_log_channel_id = int(mod_log_channel_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member or channel ID (must be valid numbers)")
        reason = str(payload.get("reason", "Moderation action"))
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        try:
            await bot.ban_user(member_id, reason, mod_log_channel_id=mod_log_channel_id)
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="Mitglied oder Mod-Log-Kanal nicht gefunden")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="Keine Berechtigung - Bot kann nicht bannen")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")
        logger.info("User %s banned member %s", user.get("id"), member_id)
        return {"status": "ok"}

    @app.post("/api/tickets")
    async def create_ticket(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        title = str(payload.get("title", "Support"))
        channel_id = await bot.create_ticket_channel(int(user["id"]), title)
        logger.info("User %s created ticket %s", user.get("id"), channel_id)
        return {"status": "ok", "channel_id": channel_id}

    @app.post("/api/raid")
    async def set_raid(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        enable = bool(payload.get("enable", False))
        close_voice = bool(payload.get("close_voice", True))
        restrict_text = bool(payload.get("restrict_text", True))
        try:
            owner_role_id = payload.get("owner_role_id")
            if owner_role_id is not None and owner_role_id != "":
                owner_role_id = int(owner_role_id)
            else:
                owner_role_id = None
            alert_channel_id = payload.get("alert_channel_id")
            if alert_channel_id is not None and alert_channel_id != "":
                alert_channel_id = int(alert_channel_id)
            else:
                alert_channel_id = None
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid role or channel ID (must be valid numbers)")
        try:
            await bot.set_raid_protection(enable, owner_role_id=owner_role_id, close_voice=close_voice, restrict_text=restrict_text, alert_channel_id=alert_channel_id)
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="Rolle oder Kanal nicht gefunden")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="Keine Berechtigung - Bot kann Kanäle nicht verwalten")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")
        logger.info("User %s set raid mode=%s owner_role=%s close_voice=%s restrict_text=%s alert_ch=%s", user.get("id"), enable, owner_role_id, close_voice, restrict_text, alert_channel_id)
        return {"status": "ok"}

    @app.get("/api/guild/info")
    async def get_guild_info(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Get guild channels, roles, and members for dropdown selection."""
        guild = bot.guild()
        if guild is None:
            raise HTTPException(status_code=404, detail="Guild not found")
        
        # Force fetch members if not in cache
        if not guild.members:
            try:
                await guild.fetch_members(limit=None)
            except Exception as e:
                logger.warning("Could not fetch members: %s", e)
        
        # Channels (text and voice)
        channels = [
            {"id": ch.id, "name": ch.name, "type": "text"}
            for ch in guild.text_channels
        ] + [
            {"id": ch.id, "name": ch.name, "type": "voice"}
            for ch in guild.voice_channels
        ]
        
        # Roles (excluding @everyone)
        roles = [
            {"id": r.id, "name": r.name}
            for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if r.name != "@everyone"
        ]
        
        # Members (up to 200 for performance, but fetch all if available)
        members = []
        all_members = list(guild.members)[:200]
        logger.info("Guild has %d members in cache, returning up to %d", len(guild.members), len(all_members))
        for member in all_members:
            if not member.bot:
                avatar_url = None
                if member.avatar:
                    avatar_url = member.avatar.url
                members.append({
                    "id": member.id,
                    "name": member.name,
                    "display_name": member.display_name or member.name,
                    "avatar": avatar_url
                })
        members.sort(key=lambda m: m["display_name"])
        
        logger.info("User %s fetched guild info (channels=%d, roles=%d, members=%d)", user.get("id"), len(channels), len(roles), len(members))
        return {
            "channels": channels,
            "roles": roles,
            "members": members
        }

    @app.post("/api/bot/activity")
    async def set_bot_activity(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        """Set bot activity (playing, listening, watching, streaming)."""
        text = str(payload.get("text", "")).strip()
        activity_type = str(payload.get("type", "playing")).lower()
        
        if not text:
            raise HTTPException(status_code=400, detail="Activity text required")
        if activity_type not in ["playing", "listening", "watching", "streaming"]:
            raise HTTPException(status_code=400, detail="Invalid activity type")
        
        try:
            await bot.set_activity(text, activity_type)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
        
        logger.info("User %s set bot activity to: %s %s", user.get("id"), activity_type, text)
        return {"status": "ok", "activity": text, "type": activity_type}

    @app.post("/api/bot/random-voice")
    async def set_random_voice(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Enable/disable random voice channel joins with optional sound."""
        enabled = bool(payload.get("enabled", False))
        interval = int(payload.get("interval", 600))
        sound_file = str(payload.get("sound_file", "")).strip() or None
        
        if interval < 60:
            raise HTTPException(status_code=400, detail="Interval must be at least 60 seconds")
        
        try:
            await bot.enable_random_voice(enabled, interval, sound_file)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
        
        logger.info("User %s set random voice: enabled=%s, interval=%d, sound=%s", user.get("id"), enabled, interval, sound_file)
        return {"status": "ok", "enabled": enabled, "interval": interval, "sound_file": sound_file}

    @app.post("/api/test/ban-dm")
    async def test_ban_dm(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Test function: Send ban DM and create appeal thread without actually banning."""
        member_id = int(payload.get("member_id", 0))
        reason = str(payload.get("reason", "Test ban"))
        mod_log_channel_id = payload.get("mod_log_channel_id")
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        try:
            if mod_log_channel_id is not None:
                mod_log_channel_id = int(mod_log_channel_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid mod_log_channel_id")
        
        # Send DM without banning
        try:
            member = await bot.fetch_user(member_id)
            dm_embed = discord.Embed(
                title="🔨 Du wurdest aus dem Server gebannt",
                color=discord.Color.red(),
                description=f"**Grund:** {reason}\n\nBitte antworte auf diese Nachricht, wenn du etwas dazu sagen möchtest."
            )
            dm_msg = await member.send(embed=dm_embed)
            
            # Store mapping for appeal
            from .bot import USER_THREAD_MAP
            USER_THREAD_MAP[member_id] = dm_msg.id
            
            # Create appeal thread if mod_log provided
            if mod_log_channel_id:
                guild = bot.guild()
                if guild:
                    try:
                        mod_log = guild.get_channel(mod_log_channel_id) or await guild.fetch_channel(mod_log_channel_id)
                        if isinstance(mod_log, discord.TextChannel):
                            log_embed = discord.Embed(
                                title=f"🧪 TEST BAN: {member}",
                                color=discord.Color.orange(),
                                description=f"**Gebannter Nutzer:** {member.mention} (ID: {member_id})\n**Discord-Tag:** {member}\n**Grund:** {reason}\n\n*(Dies ist ein TEST - der User wurde NICHT gebannt)*"
                            )
                            log_msg = await mod_log.send(embed=log_embed)
                            thread = await log_msg.create_thread(name=f"{member.name[:90]} (TEST)", auto_archive_duration=4320)
                            USER_THREAD_MAP[member_id] = thread.id
                            # Send initial context to thread
                            context_embed = discord.Embed(
                                title="📋 Appeal-Thread Information (TEST)",
                                color=discord.Color.orange(),
                                description=f"**Nutzer:** {member.mention}\n**ID:** {member_id}\n**Grund:** {reason}\n\n🧪 **Dies ist ein TEST-Thread** - der Nutzer wurde NICHT gebannt."
                            )
                            await thread.send(embed=context_embed)
                    except Exception as e:
                        logger.warning("Could not create test appeal thread: %s", e)
            
            logger.info("User %s sent TEST ban DM to member %s with reason: %s", user.get("id"), member_id, reason)
            return {"status": "ok", "message": f"Test DM gesendet an {member} (ID: {member_id})"}
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="User nicht gefunden")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="Konnte DM nicht senden (User hat DMs deaktiviert?)")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fehler: {str(e)}")

    # ==================== NEW MODERATION FEATURES ====================
    
    @app.post("/api/moderation/warn")
    async def warn_user(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Warn a user (no punishment, just logging)."""
        try:
            member_id = int(payload.get("member_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member ID")
        reason = str(payload.get("reason", "No reason")).strip()
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        try:
            warn_count = bot.warn_user(member_id, reason)
            bot.log_action("warn", member_id, user.get("id"), reason)
            
            # Try to DM user about warning
            try:
                member = await bot.fetch_user(member_id)
                embed = discord.Embed(
                    title="⚠️ Du hast eine Warnung erhalten",
                    description=f"**Grund:** {reason}\n\n**Anzahl Verwarnungen:** {warn_count}",
                    color=discord.Color.orange()
                )
                await member.send(embed=embed)
            except discord.Forbidden:
                logger.warning("Could not DM warning to user %s", member_id)
            
            logger.info("User %s warned member %s (count=%d)", user.get("id"), member_id, warn_count)
            return {"status": "ok", "warn_count": warn_count}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    @app.post("/api/moderation/mute")
    async def mute_user_route(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        """Mute a user for specified duration."""
        try:
            member_id = int(payload.get("member_id", 0))
            duration = int(payload.get("duration_seconds", 3600))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member ID or duration")
        reason = str(payload.get("reason", "No reason")).strip()
        
        if member_id <= 0 or duration <= 0:
            raise HTTPException(status_code=400, detail="Invalid values")
        
        try:
            await bot.mute_user(member_id, duration, reason)
            bot.log_action("mute", member_id, user.get("id"), reason)
            logger.info("User %s muted member %s for %d seconds", user.get("id"), member_id, duration)
            return {"status": "ok", "duration_seconds": duration}
        except discord.NotFound:
            raise HTTPException(status_code=404, detail="Member not found")
        except discord.Forbidden:
            raise HTTPException(status_code=403, detail="No permission to mute")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    @app.post("/api/moderation/unmute")
    async def unmute_user_route(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        """Unmute a user immediately."""
        try:
            member_id = int(payload.get("member_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        try:
            await bot.unmute_user(member_id)
            bot.log_action("unmute", member_id, user.get("id"), "Manual unmute")
            logger.info("User %s unmuted member %s", user.get("id"), member_id)
            return {"status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    @app.get("/api/moderation/member/{member_id}")
    async def get_member_info(member_id: int, user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Get moderation info about a member."""
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        try:
            info = bot.get_member_info(member_id)
            
            # Get Discord user info
            try:
                discord_user = await bot.fetch_user(member_id)
                info.update({
                    "username": discord_user.name,
                    "display_name": discord_user.display_name or discord_user.name,
                    "avatar": str(discord_user.avatar.url) if discord_user.avatar else None,
                    "created_at": discord_user.created_at.isoformat()
                })
            except discord.NotFound:
                info["username"] = "Unknown User"
            
            logger.info("User %s fetched member info for %s", user.get("id"), member_id)
            return info
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    @app.get("/api/moderation/log")
    async def get_audit_log(limit: int = 50, user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Get recent moderation actions."""
        limit = min(max(1, limit), 100)  # Clamp between 1-100
        log = bot.get_moderation_log(limit)
        logger.info("User %s fetched moderation log (limit=%d)", user.get("id"), limit)
        return {"log": log, "count": len(log)}

    @app.post("/api/moderation/clear-warnings")
    async def clear_warnings_route(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, str]:
        """Clear all warnings for a user."""
        try:
            member_id = int(payload.get("member_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        if member_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid member ID")
        
        bot.clear_warnings(member_id)
        bot.log_action("clear_warnings", member_id, user.get("id"), "Warnings cleared")
        logger.info("User %s cleared warnings for member %s", user.get("id"), member_id)
        return {"status": "ok"}

    @app.post("/api/bot/auto-roles")
    async def set_auto_roles_route(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Set roles to auto-assign on member join."""
        role_ids = payload.get("role_ids", [])
        if not isinstance(role_ids, list):
            raise HTTPException(status_code=400, detail="role_ids must be a list")
        
        try:
            role_ids = [int(rid) for rid in role_ids]
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid role IDs")
        
        try:
            await bot.set_auto_roles(role_ids)
            logger.info("User %s set auto-roles: %s", user.get("id"), role_ids)
            return {"status": "ok", "role_ids": role_ids}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    @app.get("/api/bot/stats")
    async def get_bot_stats(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Get bot and guild statistics."""
        guild = bot.guild()
        if guild is None:
            raise HTTPException(status_code=404, detail="Guild not found")
        
        from .bot import USER_WARNINGS
        total_warnings = sum(len(warns) for warns in USER_WARNINGS.values())
        
        return {
            "guild_name": guild.name,
            "member_count": guild.member_count,
            "text_channels": len(guild.text_channels),
            "voice_channels": len(guild.voice_channels),
            "roles_count": len(guild.roles),
            "bot_name": bot.user.name if bot.user else "Unknown",
            "online_members": sum(1 for m in guild.members if m.status != discord.Status.offline),
            "total_warnings": total_warnings
        }

    # ==================== BAN APPEALS ====================
    @app.post("/api/ban-appeal")
    async def submit_ban_appeal(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Submit a ban appeal."""
        from .bot import USER_APPEALS
        from datetime import datetime
        
        user_id = int(user.get("id", 0))
        reason = payload.get("reason", "").strip()
        evidence = payload.get("evidence", "").strip()
        
        if not reason:
            raise HTTPException(status_code=400, detail="Grund ist erforderlich")
        
        if user_id not in USER_APPEALS:
            USER_APPEALS[user_id] = []
        
        appeal = {
            "reason": reason,
            "evidence": evidence,
            "status": "pending",  # pending, approved, rejected
            "response": None,
            "created_at": datetime.utcnow().isoformat()
        }
        
        USER_APPEALS[user_id].append(appeal)
        bot.log_action("ban_appeal", user_id, user_id, f"Ban appeal submitted: {reason[:50]}")
        logger.info("User %s submitted ban appeal", user_id)

        # Notify moderators in the appeal thread (if bot is running)
        try:
            await bot.post_appeal_to_thread(user_id, appeal, ping_role_name="staff")
        except Exception as e:
            logger.warning("Could not notify moderators for appeal from %s: %s", user_id, e)

        return {"status": "ok", "message": "Appeal eingereicht"}

    @app.post("/api/ban-appeal/respond")
    async def respond_ban_appeal(payload: Dict[str, Any], user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Moderator responds to a user's appeal. payload: target_user_id, appeal_index (optional), status, response"""
        from .bot import USER_APPEALS

        # admin check
        member_info = user.get("member") or {}
        if not member_info.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin privileges required")

        try:
            target_user_id = int(payload.get("target_user_id", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid target_user_id")

        status = payload.get("status", "pending")
        response_text = str(payload.get("response", "")).strip()
        appeal_index = payload.get("appeal_index")

        if target_user_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid target_user_id")

        if target_user_id not in USER_APPEALS or len(USER_APPEALS[target_user_id]) == 0:
            raise HTTPException(status_code=404, detail="No appeals for that user")

        # Choose appeal: by index if provided, otherwise last
        if appeal_index is None:
            idx = len(USER_APPEALS[target_user_id]) - 1
        else:
            try:
                idx = int(appeal_index)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid appeal_index")
        if idx < 0 or idx >= len(USER_APPEALS[target_user_id]):
            raise HTTPException(status_code=400, detail="appeal_index out of range")

        USER_APPEALS[target_user_id][idx]["status"] = status
        USER_APPEALS[target_user_id][idx]["response"] = response_text

        # Log action
        bot.log_action("ban_appeal_response", target_user_id, int(user.get("id", 0)), f"Status={status}")

        # Post moderator response to thread
        try:
            moderator_name = f"{user.get('username')}#{user.get('discriminator', '')}"
            await bot.post_moderator_response(target_user_id, response_text, moderator=moderator_name)
        except Exception as e:
            logger.warning("Could not post moderator response for %s: %s", target_user_id, e)

        return {"status": "ok", "message": "Antwort gesendet"}

    @app.get("/api/ban-appeal")
    async def get_ban_appeals(user: Dict[str, Any] = Depends(require_user)) -> List[Dict[str, Any]]:
        """Get user's ban appeals."""
        from .bot import USER_APPEALS
        
        user_id = int(user.get("id", 0))
        
        if user_id not in USER_APPEALS:
            return []
        
        return USER_APPEALS[user_id]

    @app.get("/api/ban-appeal/all")
    async def get_all_appeals(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
        """Get all appeals (admin only)."""
        from .bot import USER_APPEALS
        # determine if request user is admin based on /api/me enrichment
        member_info = user.get("member") or {}
        is_admin = member_info.get("is_admin", False)
        logger.info("GET /api/ban-appeal/all from user %s: member_info=%s is_admin=%s", user.get("id"), member_info, is_admin)
        if not is_admin:
            logger.warning("User %s tried to access /api/ban-appeal/all without admin privileges", user.get("id"))
            raise HTTPException(status_code=403, detail="Admin privileges required")
        # return mapping user_id -> appeals
        return USER_APPEALS

    # Mount frontend after API routes so API endpoints are matched first
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
