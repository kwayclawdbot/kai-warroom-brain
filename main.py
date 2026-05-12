"""Kai War Room brain server.

Minimum-viable Phase-1: receive a user message, ask Claude to roleplay as
Kai, generate a short spoken-style reply with inline <region> tags, run it
through OpenAI TTS, and return audio + region timeline to the frontend.

Real tool calls into Supabase / Polygon land in the next iteration.
"""

import base64
import os
import re
from typing import Optional

import anthropic
import openai
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- env -------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BRAIN_TOKEN = os.environ.get("BRAIN_TOKEN")  # shared secret with frontend
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,https://kai-warroom.vercel.app",
    ).split(",")
    if o.strip()
]
KAI_MODEL = os.environ.get("KAI_MODEL", "claude-opus-4-7")
TTS_MODEL = os.environ.get("TTS_MODEL", "tts-1")
TTS_VOICE = os.environ.get("TTS_VOICE", "onyx")

# --- clients ---------------------------------------------------------------
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
oa = openai.OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# --- app -------------------------------------------------------------------
app = FastAPI(title="kai-warroom-brain")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- system prompt ---------------------------------------------------------
KAI_SYSTEM_PROMPT = """You are Kai, a sharp AI trading coach for Cheat Code AI.

Voice: confident, direct, no fluff. Like a trader who's been doing this for
fifteen years and has seen every setup.

Format:
- Reply in 1–3 short spoken-style sentences. NO markdown, NO bullets, NO headers.
- Use plain numbers spelled the way you'd say them ("eight fifty" not "$850.00").
- Mention specific tickers, levels, or setups when relevant.

Cognitive regions:
Your brain has 8 regions that light up as you think. Tag the regions you're
using with inline XML tags like <region>technicals</region>. Use 2–4 regions
per reply, placed inline near the relevant words. Available regions:
- memory     — pulling up prior context / vault notes
- market     — overall regime, macro, sectors
- technicals — chart patterns, scoring, levels
- alerts     — sent alerts, performance, winners
- watchlist  — user watchlists, saved tickers
- users      — community sentiment, what people are saying
- news       — headlines, sentiment, themes
- options    — chains, flow, unusual activity

Example reply:
"<region>market</region>Tape is risk-on right now. <region>technicals</region>
NVDA broke eight fifty with volume — <region>alerts</region>we already fired
that one this morning."
"""

# --- schemas ---------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str


class RegionPulse(BaseModel):
    id: str
    peak: float = 0.9
    decay_ms: int = 2200
    at_second: float = 0.0


class ChatResponse(BaseModel):
    text: str
    audio_base64: str
    audio_mime: str = "audio/mpeg"
    regions: list[RegionPulse]
    duration_estimate_sec: float


# --- helpers ---------------------------------------------------------------

REGION_TAG_RE = re.compile(r"<region>\s*([a-z_]+)\s*</region>", re.IGNORECASE)
VALID_REGIONS = {
    "memory",
    "market",
    "technicals",
    "alerts",
    "watchlist",
    "users",
    "news",
    "options",
}


def check_auth(authorization: Optional[str]) -> None:
    if not BRAIN_TOKEN:
        return  # auth disabled in dev when no token configured
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != BRAIN_TOKEN:
        raise HTTPException(401, "invalid token")


def parse_regions(raw_text: str) -> tuple[str, list[RegionPulse]]:
    """Strip <region> tags from text, return clean text + scheduled pulses.

    The pulse timing is estimated from the character offset of each tag in the
    clean text, assuming ~14 chars/sec speaking rate for OpenAI tts-1.
    """
    regions: list[RegionPulse] = []
    pieces: list[str] = []
    cursor = 0
    chars_so_far = 0
    chars_per_sec = 14.0

    for m in REGION_TAG_RE.finditer(raw_text):
        # Append text before the tag.
        pieces.append(raw_text[cursor : m.start()])
        chars_so_far += m.start() - cursor
        region_id = m.group(1).strip().lower()
        if region_id in VALID_REGIONS:
            regions.append(
                RegionPulse(
                    id=region_id,
                    peak=0.9,
                    decay_ms=2200,
                    at_second=chars_so_far / chars_per_sec,
                )
            )
        cursor = m.end()
    pieces.append(raw_text[cursor:])
    clean = "".join(pieces).strip()
    return clean, regions


def estimate_duration(text: str) -> float:
    return max(0.5, len(text) / 14.0)


# --- routes ----------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "claude_ready": claude is not None,
        "tts_ready": oa is not None,
        "model": KAI_MODEL,
        "tts_model": TTS_MODEL,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if not claude:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")
    if not oa:
        raise HTTPException(503, "OPENAI_API_KEY not configured")
    if not req.message.strip():
        raise HTTPException(400, "empty message")

    # 1) Generate Kai's reply via Claude.
    msg = claude.messages.create(
        model=KAI_MODEL,
        max_tokens=300,
        system=KAI_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": req.message}],
    )
    # Concatenate all text blocks.
    raw_text = "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    )

    # 2) Strip region tags + schedule pulses.
    clean_text, regions = parse_regions(raw_text)
    if not clean_text:
        raise HTTPException(502, "empty model reply")

    # 3) Generate TTS audio.
    tts = oa.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=clean_text,
        response_format="mp3",
    )
    audio_bytes = tts.content
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

    return ChatResponse(
        text=clean_text,
        audio_base64=audio_b64,
        audio_mime="audio/mpeg",
        regions=regions,
        duration_estimate_sec=estimate_duration(clean_text),
    )
