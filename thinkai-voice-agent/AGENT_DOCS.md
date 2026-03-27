# Bégé Design Voice Agent — Technical Documentation

> **Last updated:** 2026-03-09  
> **Framework:** LiveKit Agents v1.4.4  
> **Status:** Working — local dev tested, Railway deployment configured

---

## Architecture

```
┌──────────────┐        ┌──────────────────────┐        ┌───────────────┐
│  Browser     │  WS/   │  LiveKit Cloud       │  WS    │  Agent Worker │
│  (widget)    │◄─────► │  (WebRTC relay)      │◄──────►│  (server.py)  │
│              │  WebRTC│  Germany 2 region     │        │               │
└──────┬───────┘        └──────────────────────┘        └───────┬───────┘
       │ HTTP                                                   │
       ▼                                                        │
┌──────────────┐                                                │
│  Web Server  │           ┌───────────────────┐                │
│  (web_server)│◄─────────►│  Local JSON Store  │◄──────────────┘
│  :8000       │  read     │  calendar.json     │  write (tools)
│              │           │  emails.json       │
└──────────────┘           │  tasks.json        │
                           └───────────────────┘
                                    │
                                    ▼
                  ┌─────────────────────────────┐
                  │  External Services          │
                  │  ├─ Google STT              │
                  │  ├─ Anthropic Claude (LLM)  │
                  │  ├─ Cartesia TTS            │
                  │  ├─ Brevo (email sending)   │
                  │  └─ Open-Meteo (weather)    │
                  └─────────────────────────────┘
```

**Two processes** run in parallel:
1. `server.py` — LiveKit agent worker (connects to LiveKit Cloud via WebSocket)
2. `web_server.py` — FastAPI (serves widget + REST API for calendar/emails/tokens)

`start.sh` launches both from a single command for Railway.

---

## File Structure

```
thinkai-voice-agent/
├── server.py              # LiveKit agent worker (STT → LLM → TTS pipeline)
├── web_server.py          # FastAPI web server (widget + REST API)
├── tools.py               # 6 function tools the agent can call
├── voice-widget.html      # 3-panel browser UI (calendar | voice | emails)
├── start.sh               # Single-command launcher for both processes
├── .env                   # Environment variables (secrets)
├── google-credentials.json # Google service account (not in git)
├── calendar.json          # Local calendar store (created at runtime)
├── emails.json            # Sent email log (created at runtime)
├── tasks.json             # Task/note store (created at runtime)
├── requirements.txt       # Python dependencies
└── AGENT_DOCS.md          # This file
```

---

## Components

### 1. Agent Worker (`server.py`)

| Component | Provider | Config |
|-----------|----------|--------|
| **STT** | Google Cloud | Languages: `hu-HU`, `en-US` |
| **LLM** | Anthropic | Model: `claude-haiku-4-5-20251001` |
| **TTS** | Cartesia | Model: `sonic-3`, speed: `1.0`, language: `hu` |
| **VAD** | Silero | threshold: `0.6`, min_speech: `0.1s`, min_silence: `0.6s` |

**Agent class:** `BegeDesignAgent(Agent)`
- `min_endpointing_delay`: 0.5s
- `max_endpointing_delay`: 5.0s
- Greeting on connect: "Szia! A Bégé Dizájn asszisztense vagyok."

**Cartesia TTS gotchas:**
- `word_timestamps=False` — not supported for Hungarian
- `speed` must be a **float**, not string
- Use `sonic-3` (sonic-2 is deprecated)

**Startup:**
```bash
python server.py dev    # dev mode (hot-reload)
python server.py start  # production
```

---

### 2. Web Server (`web_server.py`)

**Framework:** FastAPI + Uvicorn

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serves `voice-widget.html` |
| `/widget` | GET | Serves `voice-widget.html` (alias) |
| `/api/token` | GET | Generates LiveKit room token |
| `/api/calendar` | GET | Returns all calendar events (sorted by date) |
| `/api/emails` | GET | Returns all sent emails (newest first) |
| `/api/health` | GET | Health check |

**CORS origins:** `bege.hu`, `www.bege.hu`, `localhost:3000/8000`

**Token generation:** Creates unique room `bege-{hex}` + participant `user-{hex}` with `room_join` grant.

**Startup:**
```bash
python web_server.py  # listens on PORT env var (default 8000)
```

---

### 3. Tools (`tools.py`)

| # | Tool | Backend | What it does |
|---|------|---------|-------------|
| 1 | `send_followup_email` | Brevo SMTP API | Sends HTML email, logs to `emails.json` |
| 2 | `check_calendar` | `calendar.json` | Lists events for next N days |
| 3 | `book_meeting` | `calendar.json` | Creates calendar event |
| 4 | `get_weather` | Open-Meteo (free) | Current weather (15 cities hardcoded) |
| 5 | `create_task` | `tasks.json` | Saves task/note |
| 6 | `lookup_info` | In-memory dict | ThinkAI knowledge base (pricing, audit, etc.) |

**Data storage:** All tools use local JSON files. The web server reads them via `/api/calendar` and `/api/emails`. The agent writes to them via tools. This creates a live-updating loop.

**Brevo API key:** The `.env` key is base64-encoded (MCP format). `tools.py` auto-decodes:
```python
decoded = base64.b64decode(raw_key).decode()
api_key = json.loads(decoded).get("api_key", raw_key)
```

---

### 4. Voice Widget (`voice-widget.html`)

**SDK:** `livekit-client@2` (ESM from CDN)

**Layout:** 3-column grid:
- **Left panel** — Calendar (📅) with live-updating event cards
- **Center** — Voice control (mic button, visualizer, transcript)
- **Right panel** — Sent emails (📧) with live-updating email cards

**Voice states:**

| State | Button Color | Text |
|-------|-------------|------|
| 🔵 Listening | Blue gradient | "Hallgatlak..." |
| 🟠 Thinking | Orange gradient | "Gondolkodom..." |
| 🔴 Speaking | Pink gradient | "Beszélek..." |

**Live polling:** Both side panels poll `/api/calendar` and `/api/emails` every 2 seconds. New items animate in with slide-in effect.

**Auto-reconnect:** 5 retries, max 5s delay. Shows "Újracsatlakozás..." during reconnect.

**Audio settings:** `autoGainControl`, `echoCancellation`, `noiseSuppression` all enabled.

**IMPORTANT:** Mic access requires HTTPS or `localhost`. If accessing via IP, use SSH tunnel:
```bash
ssh -L 8000:localhost:8000 root@165.227.139.84
```

---

## Environment Variables (`.env`)

| Variable | Required | Value |
|----------|----------|-------|
| `LIVEKIT_URL` | ✅ | `wss://thinkai-ugyfelszolgalat-f05w09v7.livekit.cloud` |
| `LIVEKIT_API_KEY` | ✅ | LiveKit Cloud API key |
| `LIVEKIT_API_SECRET` | ✅ | LiveKit Cloud API secret |
| `ANTHROPIC_API_KEY` | ✅ | `sk-ant-...` |
| `CARTESIA_API_KEY` | ✅ | `sk_car_...` |
| `BREVO_API_KEY` | ✅ | Base64-encoded MCP format |
| `CARTESIA_VOICE_ID` | ❌ | Default: `36e0c00b-...` |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | ❌ | Google SA creds (for cloud) |
| `PORT` | ❌ | Web server port (default: `8000`) |

---

## Running Locally

```bash
# Activate venv
source .venv-linux/bin/activate

# Terminal 1: web server
cd thinkai-voice-agent && python web_server.py

# Terminal 2: agent worker
cd thinkai-voice-agent && python server.py dev

# Or both at once:
cd thinkai-voice-agent && ./start.sh dev
```

Access at `http://localhost:8000`

---

## Railway Deployment

**Config files:**
- `railway.toml` — start command + healthcheck
- `nixpacks.toml` — nixpacks start command
- `Procfile` — process definition

**Start command:** `cd thinkai-voice-agent && bash start.sh start`
**Health check:** `GET /api/health` (60s timeout)

**Required env vars:** All from table above must be set in Railway dashboard.

**Google credentials:** Set `GOOGLE_APPLICATION_CREDENTIALS_JSON` with the full JSON content. Code writes it to disk at startup.

---

## System Prompt Summary

~4000 tokens, Hungarian. Key sections:
1. **Personality** — Confident, friendly, max 2 sentences per reply
2. **Speaking style** — Natural fillers ("Hát...", "Szóval...")
3. **Language** — Hungarian default, English if user speaks English
4. **Company info** — Mission, 3 pillars, work process, sectors, success stories
5. **Tool usage** — Lists 6 capabilities, instructs proactive tool use
6. **Email/phone protocol** — Spell-back confirmation for STT accuracy
7. **Conversation memory** — Remember user name/company throughout session

---

## Data Flow

```
User speaks → Google STT → text
                              ↓
                         Claude Haiku → response text
                              ↓                    ↓ (if tool call)
                         Cartesia TTS         tools.py writes to
                              ↓               calendar.json / emails.json
                         Audio → User              ↓
                                              widget polls /api/calendar
                                              widget polls /api/emails
                                                    ↓
                                              Side panels update live
```
