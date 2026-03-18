"""
ThinkAI Voice Agent — Tool Implementations (LiveKit Agents v1.4)
Function tools using @function_tool decorator for the voice assistant.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
import re

import httpx
from livekit.agents import function_tool, RunContext
from loguru import logger

# ── Paths ────────────────────────────────────────────────────────────────────
THIS_DIR = Path(__file__).resolve().parent
TASKS_FILE = THIS_DIR / "tasks.json"
CALENDAR_FILE = THIS_DIR / "calendar.json"
EMAILS_FILE = THIS_DIR / "emails.json"


# ── Hungarian date/time parsing ─────────────────────────────────────────────
_HU_MONTHS = {
    "január": 1, "jan": 1,
    "február": 2, "feb": 2,
    "március": 3, "márc": 3, "mar": 3,
    "április": 4, "ápr": 4,
    "május": 5, "máj": 5,
    "június": 6, "jún": 6,
    "július": 7, "júl": 7,
    "augusztus": 8, "aug": 8,
    "szeptember": 9, "szept": 9, "szep": 9,
    "október": 10, "okt": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _parse_hungarian_date(raw: str) -> str:
    """Parse various date formats into YYYY-MM-DD.

    Accepts: '2026-03-11', 'március 11', 'márc 11', '03/11', '03.11',
             'március 11-én', '11. március', etc.
    """
    raw = raw.strip().rstrip(".")

    # Already ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    year = datetime.utcnow().year

    # "március 11" / "márc 11" / "március 11-én" / "március 11."
    for name, month_num in _HU_MONTHS.items():
        if name in raw.lower():
            day_match = re.search(r"(\d{1,2})", raw)
            if day_match:
                day = int(day_match.group(1))
                return f"{year}-{month_num:02d}-{day:02d}"

    # "03/11" or "03.11" or "3/11"
    m = re.match(r"^(\d{1,2})[/\.](\d{1,2})$", raw)
    if m:
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # "2026.03.11" or "2026/03/11"
    m = re.match(r"^(\d{4})[/\.](\d{1,2})[/\.](\d{1,2})$", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # Last resort: try fromisoformat
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
    except Exception:
        pass

    raise ValueError(f"Nem értelmezhető dátum: '{raw}'")


def _parse_hungarian_time(raw: str) -> str:
    """Parse various time formats into HH:MM.

    Accepts: '10:00', '10 óra', '10h', 'délelőtt 10', '14:30', '10'
    """
    raw = raw.strip().lower()

    # Already HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    # "10 óra" / "10h" / "délelőtt 10" / "délután 3"
    m = re.search(r"(\d{1,2})", raw)
    if m:
        hour = int(m.group(1))
        if "délután" in raw or "du" in raw:
            if hour < 12:
                hour += 12
        return f"{hour:02d}:00"

    raise ValueError(f"Nem értelmezhető időpont: '{raw}'")


# ── JSON helpers ─────────────────────────────────────────────────────────────
def _read_json(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _write_json(path: Path, data: list):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SEND FOLLOW-UP EMAIL (Brevo Transactional API) — also logs to emails.json
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Follow-up email küldése egy érdeklődőnek vagy ügyfélnek. Használd, ha a felhasználó emailt szeretne küldeni valakinek.")
async def send_followup_email(
    ctx: RunContext,
    recipient_name: Annotated[str, "A címzett neve"],
    recipient_email: Annotated[str, "A címzett email címe"],
    message: Annotated[str, "Az email szövegtörzse (rövid, barátságos, szakmai)"],
    subject: Annotated[str, "Az email tárgya"] = "Bégé Design — Köszönjük érdeklődését!",
) -> str:
    """Follow-up email küldése egy érdeklődőnek."""
    raw_key = os.getenv("BREVO_API_KEY", "")
    # Try raw key first. If it looks base64-encoded (no hyphens, starts with 'ey'), try decoding.
    api_key = raw_key
    if raw_key and not raw_key.startswith("xkeysib-"):
        try:
            import base64 as b64module
            decoded = b64module.b64decode(raw_key).decode()
            parsed = json.loads(decoded)
            api_key = parsed.get("api_key", raw_key)
            logger.info("Brevo key: decoded from base64/JSON")
        except Exception:
            api_key = raw_key
    logger.info(f"Brevo key starts with: {api_key[:12]}...")
    logger.info(f"Sending follow-up email to {recipient_name} <{recipient_email}>")

    sent_ok = False
    error_msg = ""

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": api_key, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "Bégé Design", "email": "info@bege.hu"},
                    "to": [{"email": recipient_email, "name": recipient_name}],
                    "subject": subject,
                    "htmlContent": f"""
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2 style="color: #1a1a2e;">Kedves {recipient_name}!</h2>
                        <p>{message}</p>
                        <hr style="border: 1px solid #eee; margin: 20px 0;">
                        <p style="color: #666; font-size: 14px;">
                            Üdvözlettel,<br>
                            <strong>Bégé Design csapat</strong><br>
                            <a href="https://bege.hu">bege.hu</a> | info@bege.hu | +36 70 258 0102
                        </p>
                    </div>
                    """,
                },
                timeout=20,
            )
            resp.raise_for_status()
            sent_ok = True
    except Exception as e:
        logger.error(f"Email error: {e}")
        error_msg = str(e)

    # Log the email regardless of send success
    emails = _read_json(EMAILS_FILE)
    emails.append({
        "id": len(emails) + 1,
        "to_name": recipient_name,
        "to_email": recipient_email,
        "subject": subject,
        "message": message,
        "sent_at": datetime.utcnow().isoformat(),
        "status": "sent" if sent_ok else "failed",
        "error": error_msg if not sent_ok else None,
    })
    _write_json(EMAILS_FILE, emails)

    if sent_ok:
        return f"Email sikeresen elküldve {recipient_name} ({recipient_email}) részére."
    else:
        return f"Hiba az email küldésekor: {error_msg}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CHECK CALENDAR (local JSON store)
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Naptár ellenőrzése: megnézi, milyen események vannak a következő napokban. Használd, ha a felhasználó időpontot keres vagy tudni akarja, mikor szabad a naptár.")
async def check_calendar(
    ctx: RunContext,
    days_ahead: Annotated[int, "Hány napra előre nézze a naptárat (alapértelmezett: 7)"] = 7,
) -> str:
    """Naptár ellenőrzése a következő napokra."""
    logger.info(f"Checking calendar for next {days_ahead} days")

    events = _read_json(CALENDAR_FILE)
    if not events:
        return f"A következő {days_ahead} napban nincsenek rögzített események — teljesen szabad a naptár!"

    now = datetime.utcnow()
    cutoff = now + timedelta(days=days_ahead)

    upcoming = []
    for ev in events:
        try:
            ev_dt = datetime.fromisoformat(ev["start"])
            if now <= ev_dt <= cutoff:
                upcoming.append(ev)
        except Exception:
            continue

    upcoming.sort(key=lambda e: e["start"])

    if not upcoming:
        return f"A következő {days_ahead} napban nincsenek rögzített események — teljesen szabad a naptár!"

    event_list = []
    for ev in upcoming[:10]:
        try:
            dt = datetime.fromisoformat(ev["start"])
            formatted = dt.strftime("%m/%d %H:%M")
        except Exception:
            formatted = ev["start"]
        title = ev.get("title", "Névtelen esemény")
        duration = ev.get("duration_minutes", 30)
        event_list.append(f"- {formatted}: {title} ({duration} perc)")

    return f"A következő {days_ahead} napban {len(upcoming)} esemény van:\n" + "\n".join(event_list)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BOOK A MEETING (local JSON store)
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Találkozó/meeting foglalása a naptárba. Használd, ha a felhasználó időpontot szeretne foglalni.")
async def book_meeting(
    ctx: RunContext,
    title: Annotated[str, "A meeting címe/témája"],
    date: Annotated[str, "A meeting dátuma (pl. 2026-03-11, március 11, márc 11)"],
    time: Annotated[str, "A meeting kezdési időpontja (pl. 10:00, 10 óra, 14:30)"],
    duration_minutes: Annotated[int, "A meeting hossza percben"] = 30,
    attendee_email: Annotated[str, "A meghívott email címe (opcionális)"] = "",
) -> str:
    """Találkozó foglalása a naptárba."""
    logger.info(f"Booking meeting: {title} on {date} at {time}")

    try:
        parsed_date = _parse_hungarian_date(date)
        parsed_time = _parse_hungarian_time(time)
        start_dt = datetime.fromisoformat(f"{parsed_date}T{parsed_time}:00")
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        events = _read_json(CALENDAR_FILE)

        # ── Conflict detection ────────────────────────────────────────
        for ev in events:
            try:
                ev_start = datetime.fromisoformat(ev["start"])
                ev_end = ev_start + timedelta(minutes=ev.get("duration_minutes", 30))
                # Overlap: new starts before existing ends AND new ends after existing starts
                if start_dt < ev_end and end_dt > ev_start:
                    ev_title = ev.get("title", "Névtelen esemény")
                    ev_time = ev_start.strftime("%H:%M")
                    # Find next available slot on the same day
                    suggestion = _find_next_slot(events, date, duration_minutes, start_dt)
                    msg = (
                        f"Ütközés! {ev_time}-kor már van egy foglalás: \"{ev_title}\" "
                        f"({ev.get('duration_minutes', 30)} perc)."
                    )
                    if suggestion:
                        msg += f" Javaslat: {suggestion} lenne szabad. Foglaljam erre?"
                    else:
                        msg += " Ezen a napon nincs több szabad hely. Válassz egy másik napot!"
                    return msg
            except Exception:
                continue

        # ── No conflict — book it ─────────────────────────────────────
        new_id = max((e.get("id", 0) for e in events), default=0) + 1
        events.append({
            "id": new_id,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "duration_minutes": duration_minutes,
            "attendee": attendee_email or None,
            "created_at": datetime.utcnow().isoformat(),
        })
        _write_json(CALENDAR_FILE, events)

        result = f"Találkozó sikeresen lefoglalva: {title}, {date} {time}-kor, {duration_minutes} perces."
        if attendee_email:
            result += f" Meghívott: {attendee_email}."
        return result
    except Exception as e:
        logger.error(f"Booking error: {e}")
        return f"Hiba a találkozó foglalásakor: {str(e)}"


def _find_next_slot(events: list, date: str, duration: int, after: datetime) -> str | None:
    """Find the next available slot on the given date after the specified time."""
    day_events = []
    for ev in events:
        try:
            ev_start = datetime.fromisoformat(ev["start"])
            if ev_start.strftime("%Y-%m-%d") == date:
                ev_end = ev_start + timedelta(minutes=ev.get("duration_minutes", 30))
                day_events.append((ev_start, ev_end))
        except Exception:
            continue

    day_events.sort(key=lambda x: x[0])

    # Try slots from after_time to 18:00 in 30-min increments
    candidate = after.replace(second=0)
    end_of_day = after.replace(hour=18, minute=0, second=0)

    while candidate + timedelta(minutes=duration) <= end_of_day:
        candidate_end = candidate + timedelta(minutes=duration)
        conflict = any(candidate < ev_end and candidate_end > ev_start for ev_start, ev_end in day_events)
        if not conflict:
            return candidate.strftime("%H:%M")
        candidate += timedelta(minutes=30)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. WEATHER CHECK (Open-Meteo API — no API key needed!)
# ═══════════════════════════════════════════════════════════════════════════════

CITY_COORDS = {
    "budapest": (47.4979, 19.0402),
    "debrecen": (47.5316, 21.6273),
    "szeged": (46.253, 20.1414),
    "miskolc": (48.1035, 20.7784),
    "pécs": (46.0727, 18.2323),
    "győr": (47.6875, 17.6504),
    "nyíregyháza": (47.9553, 21.7174),
    "kecskemét": (46.8964, 19.6897),
    "székesfehérvár": (47.1860, 18.4221),
    "vienna": (48.2082, 16.3738),
    "bécs": (48.2082, 16.3738),
    "london": (51.5074, -0.1278),
    "new york": (40.7128, -74.0060),
    "paris": (48.8566, 2.3522),
    "párizs": (48.8566, 2.3522),
    "berlin": (52.5200, 13.4050),
}


@function_tool(description="Aktuális időjárás lekérdezése egy városban. Használd, ha a felhasználó az időjárásról kérdez.")
async def get_weather(
    ctx: RunContext,
    city: Annotated[str, "A város neve (pl. Budapest, Debrecen, Bécs)"],
) -> str:
    """Időjárás lekérdezése."""
    city_lower = city.lower().strip()
    coords = CITY_COORDS.get(city_lower, CITY_COORDS["budapest"])
    if city_lower not in CITY_COORDS:
        city = "Budapest"
    lat, lon = coords

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "current_weather": "true", "timezone": "Europe/Budapest"},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()

        weather = data.get("current_weather", {})
        temp = weather.get("temperature", "?")
        wind = weather.get("windspeed", "?")
        code = weather.get("weathercode", 0)

        weather_desc = {
            0: "tiszta égbolt", 1: "enyhén felhős", 2: "részben felhős",
            3: "borult", 45: "ködös", 48: "zúzmarás köd",
            51: "enyhe szitálás", 53: "mérsékelt szitálás", 55: "sűrű szitálás",
            61: "enyhe eső", 63: "mérsékelt eső", 65: "erős eső",
            71: "enyhe havazás", 73: "mérsékelt havazás", 75: "erős havazás",
            80: "enyhe zápor", 81: "mérsékelt zápor", 82: "erős zápor",
            95: "zivatar", 96: "jégesős zivatar", 99: "erős jégesős zivatar",
        }.get(code, "ismeretlen")

        return f"{city.title()}: {temp}°C, {weather_desc}, szél {wind} km/h."
    except Exception as e:
        logger.error(f"Weather error: {e}")
        return f"Hiba az időjárás lekérdezésekor: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CREATE TASK/NOTE (local JSON store)
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Feladat/teendő/jegyzet rögzítése. Használd, ha a felhasználó jegyezni akar valamit, vagy feladatot szeretne rögzíteni.")
async def create_task(
    ctx: RunContext,
    task: Annotated[str, "A feladat szövege"],
    priority: Annotated[str, "Prioritás: low/normal/high"] = "normal",
    due_date: Annotated[str, "Határidő YYYY-MM-DD formátumban (opcionális)"] = "",
) -> str:
    """Feladat rögzítése."""
    logger.info(f"Creating task: {task}")

    try:
        tasks = _read_json(TASKS_FILE)
        new_task = {
            "id": len(tasks) + 1,
            "text": task,
            "priority": priority,
            "due_date": due_date,
            "created_at": datetime.utcnow().isoformat(),
            "completed": False,
        }
        tasks.append(new_task)
        _write_json(TASKS_FILE, tasks)

        result = f'Feladat rögzítve: "{task}"'
        if due_date:
            result += f" — határidő: {due_date}"
        return result + "."
    except Exception as e:
        logger.error(f"Task error: {e}")
        return f"Hiba a feladat mentésekor: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. KNOWLEDGE LOOKUP (structured ThinkAI info)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Knowledge base path ──────────────────────────────────────────────────────
KNOWLEDGE_FILE = THIS_DIR / "knowledge.json"


def _load_knowledge() -> dict:
    """Load knowledge base from JSON file."""
    if KNOWLEDGE_FILE.exists():
        try:
            return json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_TOPIC_ALIASES = {
    # Hungarian terms → knowledge.json key
    "árazás": "pricing", "árak": "pricing", "ár": "pricing", "mennyibe": "pricing",
    "audit": "audit", "átvilágítás": "audit",
    "technológia": "tech_stack", "tech": "tech_stack", "eszközök": "tech_stack",
    "csapat": "team", "csapattagok": "team", "munkatársak": "team", "kik vagytok": "team",
    "eaisy": "eaisy",
    "garancia": "guarantee", "pénzvisszafizetés": "guarantee",
    "pillér": "pillerek", "pillérek": "pillerek", "szolgáltatások": "pillerek",
    "hogyan dolgoztok": "hogyan_dolgozunk", "módszer": "modszerunk", "módszertan": "modszerunk", "folyamat": "hogyan_dolgozunk",
    "szektor": "szektorok", "szektorok": "szektorok", "iparág": "szektorok",
    "sikertörténet": "sikertortenetek", "referencia": "sikertortenetek", "projekt": "sikertortenetek",
    "kapcsolat": "kapcsolat", "elérhetőség": "kapcsolat", "email cím": "kapcsolat",
    "pénzügy": "penzugy", "számvitel": "penzugy",
    "webshop": "ecommerce", "e-kereskedelem": "ecommerce", "ecommerce": "ecommerce",
    "marketing": "marketing", "sales": "marketing", "értékesítés": "marketing",
    "listamester": "listamester",
    "hungarorisk": "hungarorisk", "biztosítás": "hungarorisk",
    "könyvelés": "konyvelesai", "könyvelés ai": "konyvelesai",
    "pályázat": "palyazat", "dimop": "palyazat", "támogatás": "palyazat",
    "pályázati feltételek": "palyazat_feltetelek",
    "ügyfélszolgálat": "ai_ugyfelszolgalat", "ai ügyfélszolgálat": "ai_ugyfelszolgalat",
    "rólunk": "rolunk", "cég": "rolunk", "bemutatkozás": "rolunk",
}


@function_tool(description="ThinkAI belső tudásbázis lekérdezése. Használd, ha a felhasználó bármilyen részletes információt kér a cégről, árazásról, csapatról, szolgáltatásokról, pályázatokról, sikertörténetekről vagy bármi másról. Bármilyen témát megadhatsz szabadon, a rendszer megtalálja a megfelelő információt.")
async def lookup_info(
    ctx: RunContext,
    topic: Annotated[str, "A keresett téma szabadon megadva, pl: 'csapat', 'árazás', 'pályázat', 'garancia', 'ügyfélszolgálat', 'sikertörténetek'"],
) -> str:
    """ThinkAI tudásbázis lekérdezése."""
    kb = _load_knowledge()
    topic_lower = topic.lower().strip()
    logger.info(f"Knowledge lookup: {topic_lower}")

    # 1. Exact match in knowledge base
    if topic_lower in kb:
        return kb[topic_lower]

    # 2. Match via Hungarian aliases
    for alias, key in _TOPIC_ALIASES.items():
        if alias in topic_lower or topic_lower in alias:
            if key in kb:
                return kb[key]

    # 3. Fuzzy match on knowledge base keys
    for key, value in kb.items():
        if key in topic_lower or topic_lower in key:
            return value

    # 4. Full-text search in values
    for key, value in kb.items():
        if topic_lower in value.lower():
            return value

    # 5. Multi-word: try each word separately
    words = topic_lower.split()
    for word in words:
        if len(word) < 3:
            continue
        for alias, key in _TOPIC_ALIASES.items():
            if word in alias or alias in word:
                if key in kb:
                    return kb[key]
        for key, value in kb.items():
            if word in key or word in value.lower():
                return value

    return (
        "Erről a témáról nincs részletes információm a tudásbázisban. "
        "Részletesebb információért keresd a csapatot a hello@thinkai.hu címen!"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MODIFY CALENDAR EVENT (voice command)
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Naptári esemény módosítása. Használd, ha a felhasználó meg akarja változtatni egy meglévő találkozó időpontját, címét vagy időtartamát.")
async def modify_meeting(
    ctx: RunContext,
    event_title: Annotated[str, "A módosítandó esemény címe (vagy egy része, ami azonosítja)"],
    new_title: Annotated[str, "Az új cím (ha változik, különben hagyd üresen)"] = "",
    new_date: Annotated[str, "Az új dátum (pl. 2026-03-11, március 12, márc 12)"] = "",
    new_time: Annotated[str, "Az új időpont (pl. 10:00, 10 óra, 14:30)"] = "",
    new_duration_minutes: Annotated[int, "Az új időtartam percben (ha változik)"] = 0,
) -> str:
    """Naptári esemény módosítása."""
    logger.info(f"Modifying meeting: {event_title}")

    events = _read_json(CALENDAR_FILE)
    if not events:
        return "Nincs egyetlen esemény sem a naptárban."

    # Find the event by title (fuzzy match)
    found = None
    for ev in events:
        if event_title.lower() in ev.get("title", "").lower():
            found = ev
            break

    if not found:
        titles = ", ".join(e.get("title", "?") for e in events)
        return f"Nem találtam ilyen eseményt. A naptárban ezek vannak: {titles}"

    try:
        if not any([new_title, new_date, new_time, new_duration_minutes]):
            logger.warning(f"modify_meeting called with no changes for: {event_title}")
            return (
                f"Nem kaptam módosítási adatot. Mit szeretnél változtatni? "
                f"(új dátum, új időpont, új cím, vagy új időtartam)"
            )

        logger.info(
            f"Modify args: title='{new_title}', date='{new_date}', "
            f"time='{new_time}', duration={new_duration_minutes}"
        )

        if new_title:
            found["title"] = new_title
        if new_date or new_time:
            old_dt = datetime.fromisoformat(found["start"])
            d = _parse_hungarian_date(new_date) if new_date else old_dt.strftime("%Y-%m-%d")
            t = _parse_hungarian_time(new_time) if new_time else old_dt.strftime("%H:%M")
            new_start = datetime.fromisoformat(f"{d}T{t}:00")
            dur = new_duration_minutes or found.get("duration_minutes", 30)
            found["start"] = new_start.isoformat()
            found["end"] = (new_start + timedelta(minutes=dur)).isoformat()
            found["duration_minutes"] = dur
        elif new_duration_minutes:
            start = datetime.fromisoformat(found["start"])
            found["duration_minutes"] = new_duration_minutes
            found["end"] = (start + timedelta(minutes=new_duration_minutes)).isoformat()

        _write_json(CALENDAR_FILE, events)
        logger.info(f"Calendar written successfully: {CALENDAR_FILE}")

        changes = []
        if new_title: changes.append(f"cím: {new_title}")
        if new_date: changes.append(f"dátum: {new_date}")
        if new_time: changes.append(f"idő: {new_time}")
        if new_duration_minutes: changes.append(f"időtartam: {new_duration_minutes} perc")
        return f"Esemény módosítva ({found['title']}): {', '.join(changes)}."
    except Exception as e:
        logger.error(f"Modify error: {e}")
        return f"Hiba a módosításkor: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DELETE CALENDAR EVENT (voice command)
# ═══════════════════════════════════════════════════════════════════════════════

@function_tool(description="Naptári esemény törlése. Használd, ha a felhasználó le akarja mondani vagy törölni akar egy találkozót.")
async def delete_meeting(
    ctx: RunContext,
    event_title: Annotated[str, "A törlendő esemény címe (vagy egy része, ami azonosítja)"],
) -> str:
    """Naptári esemény törlése."""
    logger.info(f"Deleting meeting: {event_title}")

    events = _read_json(CALENDAR_FILE)
    if not events:
        return "Nincs egyetlen esemény sem a naptárban."

    # Find and remove
    original_count = len(events)
    events = [e for e in events if event_title.lower() not in e.get("title", "").lower()]

    if len(events) == original_count:
        titles = ", ".join(e.get("title", "?") for e in events)
        return f"Nem találtam ilyen eseményt. A naptárban ezek vannak: {titles}"

    _write_json(CALENDAR_FILE, events)
    return f"Esemény törölve: {event_title}."


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SEND QUOTE REQUEST TO N8N WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

N8N_WEBHOOK_URL = "https://n8n.thinkaikontir.hu/webhook-test/voiceagent"
KNOWLEDGE_BASE_FILE = THIS_DIR / "bege_design_knowledge_base.md"


def _load_knowledge_base_text() -> str:
    """Load the full knowledge base markdown text for the webhook payload."""
    if KNOWLEDGE_BASE_FILE.exists():
        try:
            return KNOWLEDGE_BASE_FILE.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


@function_tool(description="Ajánlatkérés elküldése a CRM rendszerbe. Használd, ha az ügyfél megadta a nevét, email címét, és elmondta mire van szüksége (jármű típusa, kért szolgáltatás). Foglald össze a megbeszélt igényeket és küldd el ezt az eszközt.")
async def send_quote_request(
    ctx: RunContext,
    ugyfel_nev: Annotated[str, "Az ügyfél teljes neve"],
    ugyfel_email: Annotated[str, "Az ügyfél email címe"],
    bejovo_uzenet: Annotated[str, "Az ügyfél igényének összefoglalása: jármű típusa, kért szolgáltatás, egyéb megjegyzések"],
) -> str:
    """Ajánlatkérés elküldése az n8n CRM webhookra."""
    logger.info(f"Sending quote request to n8n: {ugyfel_nev} <{ugyfel_email}>")

    tudasbazis = _load_knowledge_base_text()

    payload = {
        "forras_csatorna": "Voice Agent",
        "ugyfel_email": ugyfel_email,
        "ugyfel_nev": ugyfel_nev,
        "bejovo_uzenet": bejovo_uzenet,
        "tudasbazis": tudasbazis,
        "ticket_id": None,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            logger.info(f"Quote request sent successfully: {resp.status_code}")
            return (
                f"Az ajánlatkérés sikeresen elküldve! "
                f"{ugyfel_nev} adatait és igényeit rögzítettük, "
                f"kollégáink hamarosan felveszik a kapcsolatot az {ugyfel_email} címen."
            )
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return (
            f"Sajnos technikai hiba miatt az automatikus rögzítés nem sikerült. "
            f"Kérlek, vedd fel a kapcsolatot közvetlenül: info@bege.hu vagy +36 70 258 0102."
        )


# All tools for easy import
ALL_TOOLS = [
    send_followup_email,
    send_quote_request,
    check_calendar,
    book_meeting,
    modify_meeting,
    delete_meeting,
    get_weather,
    lookup_info,
]
