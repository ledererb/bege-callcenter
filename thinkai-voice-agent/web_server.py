"""
Bégé Design Voice Agent — Web Server
Serves the voice widget page, generates LiveKit room tokens,
and provides API endpoints for calendar events and email logs.
"""

import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from livekit.api import AccessToken, VideoGrants

THIS_DIR = Path(__file__).resolve().parent
load_dotenv(THIS_DIR / ".env")

CALENDAR_FILE = THIS_DIR / "calendar.json"
EMAILS_FILE = THIS_DIR / "emails.json"

app = FastAPI(title="Bégé Design Voice Agent")

# CORS — allow embedding on bege.hu and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bege.hu",
        "https://www.bege.hu",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index():
    return FileResponse(THIS_DIR / "voice-widget.html")


@app.get("/widget")
async def widget():
    return FileResponse(THIS_DIR / "voice-widget.html")


@app.get("/api/token")
async def get_token():
    """Generate a LiveKit room token for a new user."""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not api_key or not api_secret:
        return JSONResponse({"error": "LiveKit credentials not configured"}, status_code=500)

    room_name = f"bege-{uuid.uuid4().hex[:8]}"
    participant_name = f"user-{uuid.uuid4().hex[:6]}"

    token = (
        AccessToken(api_key, api_secret)
        .with_identity(participant_name)
        .with_name("Visitor")
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
        ))
    )

    return JSONResponse({
        "token": token.to_jwt(),
        "url": os.getenv("LIVEKIT_URL"),
        "room": room_name,
    })


@app.get("/api/calendar")
async def get_calendar():
    """Return all calendar events, sorted by start time."""
    if not CALENDAR_FILE.exists():
        return JSONResponse({"events": []})
    try:
        events = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        events.sort(key=lambda e: e.get("start", ""))
        return JSONResponse({"events": events})
    except Exception:
        return JSONResponse({"events": []})


@app.get("/api/emails")
async def get_emails():
    """Return all logged emails, newest first."""
    if not EMAILS_FILE.exists():
        return JSONResponse({"emails": []})
    try:
        emails = json.loads(EMAILS_FILE.read_text(encoding="utf-8"))
        emails.reverse()  # newest first
        return JSONResponse({"emails": emails})
    except Exception:
        return JSONResponse({"emails": []})


@app.get("/api/health")
async def health():
    return {"status": "ok", "agent": "bege-voice-agent"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
