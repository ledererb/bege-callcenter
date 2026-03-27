"""
Bégé Design Voice Agent — LiveKit Agents Server
Real-time voice assistant powered by LiveKit + ElevenLabs Scribe v2 STT + Gemini 2.5 Flash + Cartesia TTS
Hungarian-only with Bégé Design brand pronunciation handling
"""

import asyncio
import os
import re
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
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

from livekit.plugins import cartesia, elevenlabs, google, noise_cancellation, silero

# ── Import tools ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(THIS_DIR))
from tools import ALL_TOOLS

# ── Google credentials setup (still needed for Gemini LLM) ───────────────────
def _setup_google_credentials():
    """Write Google credentials from env var if present (for Railway/cloud)."""
    creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    creds_path = Path("/tmp/google-credentials.json")
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
    "Bégé Design": "Bégé Dizájn",
    "bege.hu": "bégé pont há ú",
    "info@bege.hu": "infó kukac bégé pont há ú",
    # Fólia / szakmai rövidítések
    "PPF": "pé-pé-ef",
    "3M": "három-em",
    "UV": "ú-vé",
    "PVC": "pé-vé-cé",
    # Email providers
    "Gmail": "dzsé-mél",
    "gmail": "dzsé-mél",
    "GMAIL": "dzsé-mél",
    "gmail.com": "dzsé-mél pont kom",
    # Egyéb tech rövidítések
    "XI.": "tizenegyedik",
}


def _apply_tts_replacements(text: str) -> str:
    """Replace brand/tech terms with phonetic Hungarian spellings for TTS."""
    for original, phonetic in _TTS_REPLACEMENTS.items():
        text = text.replace(original, phonetic)
    return text


# ── Phantom transcript filter ────────────────────────────────────────────────
# ElevenLabs Scribe v2 sometimes transcribes noise/breathing as gibberish.
# This regex catches consonant-only strings that are clearly not Hungarian words.
# NOTE: "Ja", "Na", "Hm" are valid Hungarian — we only filter consonant-only noise.
_NOISE_PATTERN = re.compile(r'^[bcdfghjklmnpqrstvwxyz]{2,}$', re.IGNORECASE)
_KNOWN_NOISE = {"ksznm", "kszn", "hm", "hmm", "mhm"}

def _is_phantom_transcript(text: str) -> bool:
    """Return True if the transcript looks like noise, not real speech."""
    cleaned = text.strip().lower()
    if not cleaned:
        return True
    if cleaned in _KNOWN_NOISE:
        return True
    if _NOISE_PATTERN.match(cleaned):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class BegeDesignAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=_get_system_prompt(),
            tools=ALL_TOOLS,
        )

    async def on_enter(self):
        """Greet the user when they connect."""
        self.session.say(
            "Szia! A Bégé Dizájn virtuális asszisztense vagyok. "
            "Kérdezhetsz fóliázási szolgáltatásainkról, árakról, "
            "vagy segítek időpontot egyeztetni. Miben segíthetek?"
        )

    async def stt_node(self, audio, model_settings):
        """Override STT node: filter phantom transcripts from noise."""
        async for event in Agent.default.stt_node(self, audio, model_settings):
            # Filter out noise transcripts before they reach the LLM
            if hasattr(event, 'alternatives') and event.alternatives:
                text = event.alternatives[0].text
                if text and _is_phantom_transcript(text):
                    logger.warning(f"Filtered phantom transcript: '{text}'")
                    continue
            yield event

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

    async def toolcall_node(self, tool_call, chat_ctx, model_settings):
        """Override toolcall node: catch unhandled exceptions so the agent never goes silent."""
        try:
            result = Agent.default.toolcall_node(self, tool_call, chat_ctx, model_settings)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            logger.error(f"Tool call error ({tool_call.function.name}): {e}")
            return f"Sajnos hiba történt a művelet során: {str(e)}. Kérlek, próbáld újra!"

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

    # ── Connection options for resilient API calls ────────────────────────
    conn_options = SessionConnectOptions(
        stt_conn_options=APIConnectOptions(max_retry=3, timeout=10),
        llm_conn_options=APIConnectOptions(max_retry=3, timeout=30),
        tts_conn_options=APIConnectOptions(max_retry=3, timeout=10),
        max_unrecoverable_errors=5,
    )

    session = AgentSession(
        stt=elevenlabs.STT(
            model_id="scribe_v2_realtime",
            language_code="hu",
            api_key=os.getenv("ELEVEN_API_KEY") or os.getenv("ELEVENLABS_API_KEY"),
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
            activation_threshold=0.85,
            min_speech_duration=0.4,
            min_silence_duration=0.65,
        ),
        # ── Production tuning ─────────────────────────────────────────────
        min_endpointing_delay=0.8,
        max_endpointing_delay=5.0,
        min_interruption_duration=0.7,
        min_interruption_words=1,
        max_tool_steps=5,
        user_away_timeout=20.0,
        preemptive_generation=True,
        conn_options=conn_options,
    )

    logger.info(
        f"Session configured: STT=ElevenLabs scribe_v2_realtime, "
        f"LLM=gemini-2.5-flash, TTS=cartesia sonic-3, "
        f"VAD threshold=0.85, preemptive={True}"
    )

    await session.start(
        agent=BegeDesignAgent(),
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
