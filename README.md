# Discord Control Panel (Bot + API + Website)

Produktionsnahes Grundgerüst für einen Discord‑Bot mit Web‑Dashboard (Rollen, Nachrichten, Moderation, Tickets) und OAuth‑Login.

## Features
- Discord OAuth2 Login (identify)
- Rollen vergeben, Nachrichten posten, Moderation (Ban)
- Tickets als eigene Channels
- FastAPI + Discord.py im gleichen Prozess

## Setup

1. Python‑Abhängigkeiten installieren:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

2. `.env` anlegen:

```bash
cp .env.example .env
```

3. Discord Application konfigurieren:
- **OAuth2 Redirect URI:** `http://localhost:8000/auth/callback`
- **Bot** erstellen und Token kopieren
- **Privileged Intents** aktivieren (Members, Message Content)

4. Starten:

```bash
export $(cat .env | xargs)
python -m backend.app.server
```

## Hinweise für Produktion
- Nutze HTTPS und sichere Session‑Secrets.
- Prüfe Rollen/Permissions vor kritischen Aktionen.
- Optional: Rate‑Limit, Audit‑Logs, Ticket‑Datenbank.
