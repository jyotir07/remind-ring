"""Thin Sarvam client: stt / chat / tts.

Contracts verified from docs.sarvam.ai on 26 July 2026 (see BUILD_GUIDE.md section 2).
Direct REST rather than the SDK because the wire format is what was verified.

Three run modes:
  live    SARVAM_API_KEY set            -> real calls, every response cached
  offline OFFLINE=1                     -> replay cache/ only, raise if missing
  mock    MOCK=1 or no key              -> deterministic canned responses, no network
"""
import base64
import hashlib
import json
import math
import os
import struct
import subprocess
import time
import wave
from pathlib import Path

import httpx

BASE = "https://api.sarvam.ai"
ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)

KEY = os.getenv("SARVAM_API_KEY", "").strip()
OFFLINE = os.getenv("OFFLINE") == "1"
MOCK = os.getenv("MOCK") == "1" or not KEY

# sarvam-30b reasons by default and reasoning tokens count against max_tokens.
# Measured 26 July on this app's real prompts: effort="low" still burned the whole
# budget thinking (4.4K chars), returned finish_reason="length" and content=None,
# and took 8-43s. Only an explicit JSON null switches thinking off. Omitting the
# key is NOT the same thing -- the server default is "low".
# With null: think=0, finish=stop, 0.7-1.8s. This is the single most important
# line in the file.
REASONING_EFFORT = None
MAX_TOKENS = 700

_client: httpx.Client | None = None


def mode() -> str:
    return "mock" if MOCK else ("offline" if OFFLINE else "live")


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=45,
            headers={
                "api-subscription-key": KEY,
                # The chat endpoint's docs example shows Bearer while every other
                # endpoint shows the subscription key. Sending both costs nothing.
                "Authorization": f"Bearer {KEY}",
            },
        )
    return _client


def _cache_path(kind: str, key: str) -> Path:
    return CACHE / f"{kind}_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def _post(url: str, *, retries: int = 2, **kw) -> dict:
    last = None
    for attempt in range(retries + 1):
        try:
            r = _http().post(url, **kw)
            if r.status_code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code < 500 and e.response.status_code != 429:
                raise
        except httpx.HTTPError as e:
            last = e
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last


# --------------------------------------------------------------- speech to text

def _transcode(src: Path) -> Path:
    """Chrome's WebM container is the one format on Sarvam's supported list that
    might still be rejected as Chrome emits it. Only called after a 400."""
    dst = src.with_suffix(".conv.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", str(dst)],
        check=True,
    )
    return dst


def stt(path: str | Path) -> str:
    path = Path(path)
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    cache = _cache_path("stt", digest)

    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["transcript"]
    if MOCK:
        data = {"transcript": _mock_transcript(path)}
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data["transcript"]
    if OFFLINE:
        raise RuntimeError(f"OFFLINE=1 and no cached transcript for {path.name}")

    # language_code='unknown' on purpose: Hinglish is neither hi-IN nor en-IN, and
    # forcing either biases the transcript toward one script.
    form = {"model": "saaras:v3", "mode": "transcribe", "language_code": "unknown"}
    try:
        with open(path, "rb") as f:
            data = _post(f"{BASE}/speech-to-text",
                         files={"file": (path.name, f, "application/octet-stream")},
                         data=form)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 400:
            raise
        conv = _transcode(path)
        with open(conv, "rb") as f:
            data = _post(f"{BASE}/speech-to-text",
                         files={"file": (conv.name, f, "audio/wav")}, data=form)

    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data["transcript"]


# ---------------------------------------------------------------------- chat

def chat(messages: list, schema: dict | None = None, kind: str = "turn",
         temperature: float = 0.3) -> dict:
    key = json.dumps([messages, kind], sort_keys=True, ensure_ascii=False)
    cache = _cache_path("chat", key)

    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if MOCK:
        out = _mock_chat(messages, kind)
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    if OFFLINE:
        raise RuntimeError("OFFLINE=1 and no cached completion for this turn")

    body = {"model": "sarvam-30b", "messages": messages, "temperature": temperature,
            "max_tokens": MAX_TOKENS, "reasoning_effort": REASONING_EFFORT}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": schema}

    data = _post(f"{BASE}/v1/chat/completions", json=body)
    choice = data["choices"][0]
    raw = choice["message"]["content"]
    if raw is None:
        raise RuntimeError(
            f"empty content (finish_reason={choice['finish_reason']}) — the model "
            f"spent the token budget on reasoning. Check REASONING_EFFORT is None."
        )
    out = _parse_json(raw, messages, schema)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _parse_json(raw: str, messages: list, schema: dict | None) -> dict:
    """json_schema mode should make this unnecessary. It is here because a
    malformed turn mid-call is a dead demo, and one repair retry is cheap."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    repair = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": "Return only the JSON object. No prose, no code fences."},
    ]
    body = {"model": "sarvam-30b", "messages": repair, "temperature": 0,
            "max_tokens": MAX_TOKENS, "reasoning_effort": REASONING_EFFORT}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": schema}
    data = _post(f"{BASE}/v1/chat/completions", json=body)
    return json.loads(data["choices"][0]["message"]["content"].strip().strip("`"))


# ------------------------------------------------------------- text to speech

def tts(text: str, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = _cache_path("tts", text)

    if cache.exists():
        b64 = json.loads(cache.read_text(encoding="utf-8"))["audios"][0]
    elif MOCK:
        _write_placeholder_wav(out_path, text)
        return out_path
    elif OFFLINE:
        raise RuntimeError("OFFLINE=1 and no cached audio for this reply")
    else:
        data = _post(f"{BASE}/text-to-speech", json={
            "text": text[:2400],          # bulbul:v3 caps at 2500
            "target_language_code": "hi-IN",
            "model": "bulbul:v3",
            "speaker": "shubh",
            "pace": 1.05,
        })
        cache.write_text(json.dumps(data), encoding="utf-8")
        b64 = data["audios"][0]

    out_path.write_bytes(base64.b64decode(b64))
    return out_path


# ------------------------------------------------------------------ mock layer
# Deterministic stand-ins so the whole app is runnable before the key arrives.
# Keyword matching, not intelligence — every mock reply is prefixed [mock] in the
# UI so a mock run can never be mistaken for a live one.

_MOCK_TRANSCRIPTS = {
    "confusion": "yaar samajh hi nahi aa raha, deadlocks wala portion bilkul clear nahi hai",
    "time": "aaj lab hai nau baje tak, time hi nahi milega",
    "motivation": "pata nahi yaar, bas mann nahi kar raha aaj",
    "scope_fear": "bahut bada hai assignment, kahan se start karun samajh nahi aata",
    "avoidant": "haan haan ho jayega, kal dekhta hoon",
    "done_already": "arre wo to kar liya maine kal raat ko",
}

_KEYWORDS = [
    ("done_already", ["kar liya", "ho gaya", "done", "finished", "complete"]),
    ("confusion",    ["samajh", "understand", "clear nahi", "confus", "pata nahi kaise", "deadlock"]),
    ("time",         ["time nahi", "lab", "class", "baje", "busy", "no time"]),
    ("scope_fear",   ["bahut bada", "kahan se", "too big", "where to start", "huge"]),
    ("motivation",   ["mann nahi", "mood nahi", "lazy", "feel like", "energy"]),
    ("avoidant",     ["kal dekh", "ho jayega", "haan haan", "later", "tomorrow"]),
]

STRATEGY_FOR = {
    "confusion": "teach", "time": "reslice", "scope_fear": "decompose",
    "motivation": "shrink", "avoidant": "confront", "done_already": "verify",
}

_MOCK_REPLIES = {
    "teach": "Deadlock tab hota hai jab do transactions ek dusre ka lock wait kar rahe hote hain — circular wait. Bas itna: teen minute mein intro paragraph likh sakte ho?",
    "reslice": "Theek hai, lab ke baad. Poora assignment nahi — sirf intro, dus minute ka kaam. Nau baje ke baad kar loge?",
    "decompose": "Poora assignment mat socho. Sirf ek cheez: question paper kholo aur pehla part likh lo. Kar loge?",
    "shrink": "Mann nahi kar raha, theek hai. Poora nahi — sirf teen minute, ek paragraph. Sirf teen minute doge?",
    "confront": "Yeh kal bhi bola tha tumne. Main tumhe pakad raha hoon — abhi paanch minute do, ek line likho. Haan ya na?",
    "verify": "Achha! Ek cheez batao — deadlock detection ke liye kaunsa algorithm likha tumne? Phir main ise done mark kar deta hoon.",
}


def _mock_transcript(path: Path) -> str:
    name = path.stem.lower()
    for blocker, text in _MOCK_TRANSCRIPTS.items():
        if blocker in name:
            return text
    return _MOCK_TRANSCRIPTS["motivation"]


def _mock_classify(text: str) -> tuple[str, float]:
    low = text.lower()
    hits = [(b, sum(k in low for k in kws)) for b, kws in _KEYWORDS]
    hits = [h for h in hits if h[1] > 0]
    if not hits:
        return "avoidant", 0.3
    hits.sort(key=lambda h: -h[1])
    conf = 0.72 + min(hits[0][1], 3) * 0.07
    if len(hits) > 1 and hits[0][1] == hits[1][1]:
        conf = 0.55                      # genuinely ambiguous, say so
    return hits[0][0], round(conf, 2)


def _mock_chat(messages: list, kind: str) -> dict:
    joined = "\n".join(m["content"] for m in messages if m["role"] == "user")

    if kind == "goal":
        return {
            "title": "DBMS assignment",
            "due_at": None,
            "milestones": [
                {"title": "Read the deadlocks section", "est_min": 25},
                {"title": "Write the introduction", "est_min": 30},
                {"title": "Solve the numerical problems", "est_min": 45},
            ],
        }

    if kind == "opening":
        if "prior_blockers: none" not in joined:
            return {"reply_text": "[mock] Kal bhi tumne yahi bola tha — aaj start kiya ya nahi?"}
        return {"reply_text": "[mock] Tumne bola tha aaj yeh khatam karoge. Start kiya?"}

    if kind == "classify":
        blocker, conf = _mock_classify(joined)
        return {"blocker": blocker, "confidence": conf}

    strategy = joined.rsplit("your strategy:", 1)[-1].split("—")[0].strip()
    if strategy not in _MOCK_REPLIES:
        strategy = "shrink"

    commitment = None
    close = False
    if strategy == "verify":
        commitment = {"text": "Confirm the finished section and start the next one", "size_min": 5}
        close = True
    elif joined.count("user:") >= 1:
        commitment = {"text": "Write the introduction paragraph", "size_min": 3}
        close = True

    return {
        "reply_text": "[mock] " + _MOCK_REPLIES[strategy],
        "commitment": commitment,
        "close": close,
    }


def _write_placeholder_wav(path: Path, text: str) -> None:
    """Valid WAV so the browser <audio> element behaves exactly as it will live:
    a short soft tone, then silence scaled to how long the line takes to say."""
    rate = 24000
    seconds = max(1.2, min(len(text) / 14, 8.0))
    frames = bytearray()
    for i in range(int(rate * seconds)):
        t = i / rate
        amp = 0.18 * math.exp(-t * 9) if t < 0.35 else 0.0
        frames += struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 220 * t)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
