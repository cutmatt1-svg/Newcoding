
**# Yui Bot** 
made with ❤️ by Matti (:

# 1. Bitte erstelle zuerst eine .env mit cp .env.example .env
Schreibe dort das rein:
DISCORD_CLIENT_ID= # Client ID vom Bot
DISCORD_CLIENT_SECRET= # Client Secret vom Bot
DISCORD_BOT_TOKEN= # Dein Discord Bot Token
DISCORD_REDIRECT_URI= # Die URL für die Discord Oauth von der Website
DISCORD_GUILD_ID= # Die ID des Discord Servers
SESSION_SECRET= # Such dir hier ein Passwort aus

# 2. requirements.txt installieren
# Instaliere die Requirements mit
pip install -r requirements.txt

# 3 Starte den Bot
python -m backend.app.server 2>&1 | tee server.log

# (4. Neustart
ss -ltnp | grep ':8000' || lsof -i :8000
und danach
# dann PID killen 
kill -9 <PID>)

