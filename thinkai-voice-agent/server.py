"""
ThinkAI Voice Agent — LiveKit Agents Server
Real-time voice assistant powered by LiveKit + ElevenLabs Scribe v2 STT + Gemini 2.5 Flash + Cartesia TTS
Hungarian-only with ThinkAI brand pronunciation handling
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# ── Load env ──────────────────────────────────────────────────────────────────
THIS_DIR = Path(__file__).resolve().parent
load_dotenv(THIS_DIR / ".env")

# ── LiveKit Agents ────────────────────────────────────────────────────────────
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
)

from livekit.plugins import cartesia, elevenlabs, google, noise_cancellation, silero

# ── Import tools ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(THIS_DIR))
from tools import ALL_TOOLS

# ── Google credentials setup (still needed for Gemini LLM) ───────────────────
def _setup_google_credentials():
    """Write Google credentials from env var if present (for Railway/cloud)."""
    creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    creds_path = THIS_DIR / "google-credentials.json"
    if creds_json and not creds_path.exists():
        creds_path.write_text(creds_json)
    if creds_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)

_setup_google_credentials()


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — loaded from system_prompt.md for easy editing
# ═══════════════════════════════════════════════════════════════════════════════

PROMPT_FILE = THIS_DIR / "system_prompt.md"

def _get_system_prompt() -> str:
    """Load system prompt from system_prompt.md and inject current date."""
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return template.format(today=today)


# ── TTS pronunciation replacements (applied before Cartesia gets the text) ────
# Keys are case-sensitive. The LLM writes natural text; this map ensures
# Cartesia pronounces foreign/brand words correctly in Hungarian.
_TTS_REPLACEMENTS = {
    # Brand names
    "ThinkAI": "Tink-éj-áj",
    "thinkAI": "tink-éj-áj",
    "Thinkai": "Tink-éj-áj",
    "thinkai": "tink-éj-áj",
    "EAISY": "Ízí",
    "Eaisy": "Ízí",
    "eaisy": "ízí",
    # Domains & emails
    "thinkai.hu": "tink-éj-áj pont há ú",
    "hello@thinkai.hu": "helló kukac tink-éj-áj pont há ú",
    # Tech terms the Hungarian TTS mangles
    "AI": "éj-áj",
    "CRM": "szé-er-em",
    "ERP": "é-er-pé",
}


def _apply_tts_replacements(text: str) -> str:
    """Replace brand/tech terms with phonetic Hungarian spellings for TTS."""
    for original, phonetic in _TTS_REPLACEMENTS.items():
        text = text.replace(original, phonetic)
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class ThinkAIAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=_get_system_prompt(),
            tools=ALL_TOOLS,
            min_endpointing_delay=0.8,
            max_endpointing_delay=4.0,
        )

    async def on_enter(self):
        """Greet the user when they connect."""
        self.session.say(
            "Szia! A Tink-éj-áj virtuális asszisztense vagyok. "
            "Kérdezz a szolgáltatásainkról, foglalj időpontot, "
            "vagy akár emailt is küldhetek helyetted. Miben segíthetek?"
        )

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Override LLM node: context window + error fallback."""
        chat_ctx.truncate(max_items=20)

        try:
            stream = Agent.default.llm_node(self, chat_ctx, tools, model_settings)
            if asyncio.iscoroutine(stream):
                stream = await stream
            return stream
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return "Hoppá, most egy pillanatra elakadtam. Kérlek, próbáld újra!"

    async def tts_node(self, text, model_settings):
        """Override TTS node: apply brand pronunciation replacements."""
        async def _cleaned_text():
            async for chunk in text:
                if chunk:
                    chunk = _apply_tts_replacements(chunk)
                    yield chunk

        async for frame in Agent.default.tts_node(self, _cleaned_text(), model_settings):
            yield frame


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════════

async def entrypoint(ctx: JobContext):
    """LiveKit agent entrypoint — called when a user joins a room."""
    logger.info(f"Agent connecting to room: {ctx.room.name}")

    await ctx.connect()

    # NOTE: ElevenLabs keyterms only work in batch mode (not realtime streaming).
    # The scribe_v2_realtime model ignores keyterms in the WebSocket streaming path.
    # Hungarian name/brand recognition relies on Scribe v2's native 3.1% WER accuracy.
    # If you need keyterms, switch model_id to "scribe_v2" (batch, higher latency).

    session = AgentSession(
        stt=elevenlabs.STT(
            model_id="scribe_v2_realtime",
            language_code="hu",
            api_key=os.getenv("ELEVEN_API_KEY"),
        ),
        llm=google.LLM(
            model="gemini-2.5-flash",
        ),
        tts=cartesia.TTS(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice=os.getenv("CARTESIA_VOICE_ID", "93896c4f-aa00-4c17-a360-fec55579d7fa"),
            model="sonic-3",
            speed=1.0,
            language="hu",
            word_timestamps=False,
            emotion=["positivity:high", "curiosity"],
        ),
        vad=silero.VAD.load(
            activation_threshold=0.75,
            min_speech_duration=0.2,
            min_silence_duration=0.65,
        ),
    )

    await session.start(
        agent=ThinkAIAgent(),
        room=ctx.room,
        # Server-side noise cancellation — filters breathing, background noise,
        # keyboard sounds before they reach VAD (requires LiveKit Cloud)
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# WORKER
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        ),
    )

