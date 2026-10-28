# Claude WebAPI Python Service

A small FastAPI service that wraps `claude-webapi` so a Next.js (or any backend) app like **Noveral** can send text and receive normal chat-style responses or real-time streaming using Claude's web interface.

## What it does

* Accepts prompt text via HTTP (`POST /chat`)
* Automatically uses Claude's default web model (Sonnet)
* Accepts `session_key` dynamically in the request body from Noveral, or from local DB / `.env`
* Auto-discovers the Claude organization ID automatically
* Runs on port **`3002`** (so it runs cleanly alongside `gemini-service` on port 3000 and `noveral-web` on port 3001)

## Project structure

```text
claude-web-api/
├─ app.py
├─ requirements.txt
├─ Dockerfile
├─ docker-compose.yml
├─ .env.example
├─ .gitignore
└─ README.md
```

## Requirements

* Python 3.10+ (or Docker)
* An active [Claude.ai](https://claude.ai) account logged in on your browser
* The Claude web session cookie:
  * `sessionKey` (starts with `sk-ant-sid01-...`)

## How to get your Claude sessionKey

1. Open Chrome, Edge, or Brave and go to [https://claude.ai](https://claude.ai).
2. Sign in to your account.
3. Press `F12` (or right-click anywhere and click **Inspect**).
4. Navigate to the **Application** tab (in Firefox: **Storage**).
5. Expand **Cookies** in the left sidebar and select `https://claude.ai`.
6. Copy the value of the cookie named **`sessionKey`** (starts with `sk-ant-sid01-...`).

## Running with Docker

### 1. Build and run

```bash
docker build -t claude-service .
docker run -p 3002:3002 claude-service
```

### 2. Run with Docker Compose

```bash
docker compose up -d
```

Service is exposed on port `3002` (e.g. `http://jellyfon.ddns.net:3002`).

---

## API Reference

### `GET /health`

```json
{
  "ok": true
}
```

### `POST /chat`

Request body:

```json
{
  "text": "Hello Claude",
  "session_key": "sk-ant-sid01-..."
}
```

*(Note: `session_key` is optional if configured in `.env` or mounted SQLite DB).*

Response body:

```json
{
  "reply": "Hello! How can I help you today?"
}
```
