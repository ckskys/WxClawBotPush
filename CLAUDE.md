# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Install deps
pip install -r requirements.txt

# Environment
cp .env.example .env   # set ADMIN_USERNAME, ADMIN_PASSWORD
source .env

# Local dev
bash start.sh
# Or directly:
ADMIN_USERNAME=admin ADMIN_PASSWORD=your_pass bash start.sh

# Docker build + run
docker compose up -d --build
```

There are no tests, linters, or type checkers configured.

## Architecture

A multi-user webhook-to-WeChat push platform. Each user logs into their own WeChat bot via QR code scan, then receives a unique webhook token to push messages to WeChat contacts via HTTP POST.

- **Stack**: Python 3.11+, FastAPI, uvicorn, SQLite (no ORM), vanilla HTML/CSS/JS frontend.
- **WeChat protocol**: Custom iLink protocol client in [wxclawbotpush/ilink/](wxclawbotpush/ilink/).

### Key data flow

1. User logs into admin panel → requests QR code from `/api/user/qrcode`
2. `ilink/client.py` opens iLink connection, renders QR as PNG (pyqrcode)
3. User scans QR in WeChat → background thread in [polling.py](wxclawbotpush/polling.py) polls scan status
4. Once scanned, a second thread polls `/ilink/bot/getupdates` to discover contacts
5. User gets a webhook token → external service POSTs to `/webhook?token=...`
6. `webhook.py` resolves the user, applies the message template, calls `ilink/client.py` to send

### Core modules

| Module | Role |
|---|---|
| [app.py](wxclawbotpush/app.py) | FastAPI app factory, startup/shutdown lifecycle |
| [database.py](wxclawbotpush/database.py) | SQLite connection management, DDL, `get_db()` helper |
| [auth.py](wxclawbotpush/auth.py) | bcrypt passwords, in-memory session tokens (7-day timeout), user CRUD |
| [config.py](wxclawbotpush/config.py) | Per-user config CRUD (`user_configs` table) and system config (`system_config` table) |
| [webhook.py](wxclawbotpush/webhook.py) | `GET/POST /webhook` — token auth, template rendering, push to WeChat |
| [admin.py](wxclawbotpush/admin.py) | Admin/user API routes + static HTML page serving (SPA admin panel) |
| [client.py](wxclawbotpush/client.py) | Per-user `ILinkClient` instance cache (`dict[int, ILinkClient]`) |
| [polling.py](wxclawbotpush/polling.py) | Background daemon threads for contact sync and QR status polling |
| [template.py](wxclawbotpush/template.py) | Custom `{{ field }}` template engine with dot-path JSON resolution |
| [log_utils.py](wxclawbotpush/log_utils.py) | Logger setup with in-memory deque buffer (max 1000) for admin log viewing |
| [ilink/](wxclawbotpush/ilink/) | iLink protocol: QR login, message send, contact polling, AES encryption |

### Database

Four SQLite tables in `{DATA_DIR}/wxclawbotpush.db`: `users`, `user_configs`, `system_config`, `push_logs`. Schema migrations happen on startup via `database.init_db()`. No migration framework — column existence checks guard against re-running DDL.

### Environment variables

`ADMIN_USERNAME`, `ADMIN_PASSWORD` (required), `ILINK_BASE_URL`, `WEBHOOK_PORT` (8000), `DATA_DIR` (./data), `TZ`.

### CLI

```bash
python -m wxclawbotpush.create_admin <username> <password>
```
