"""THE MECHANIC — classify the blocker, route to a strategy, mutate the plan."""
import hashlib
import logging
import re
from pathlib import Path

import clock
import db
import plan
import prompts
import sarvam

log = logging.getLogger("brain")
AUDIO = Path(__file__).parent / "audio"

# Markers that mean the model echoed its own prompt back instead of answering.
# Observed live: an entire context block was returned as reply_text and read aloud,
# timestamps and all. reply_text is speech — anything scaffolding-shaped is a bug.
_ECHO = ("prior_blockers", "conversation so far", "your strategy:", "they just said",
         "milestone:", "est_min", "reply_text")
_STRIP = [
    re.compile(r"\((?:est|was due|due)\b[^)]*\)", re.I),   # "(est 3 min, was due …)"
    re.compile(r"\b\d{4}-\d{2}-\d{2}T[\d:]+"),             # bare ISO timestamps
    re.compile(r"\s{2,}"),
]


def sanitize(text: str | None) -> str | None:
    """Returns speech-safe text, or None if the model echoed the prompt.

    The same string is displayed and spoken, so cleaning happens once here rather
    than separately for the bubble and the TTS — otherwise the two drift apart.
    """
    if not text:
        return None
    if any(m in text.lower() for m in _ECHO):
        return None
    out = text.strip()
    for rx in _STRIP:
        out = rx.sub(" ", out)
    out = out.strip(" -—:;")
    # Three sentences is already long for a phone call; anything past that is the
    # model padding, and padding is what makes a demo sound unpolished.
    parts = re.split(r"(?<=[.!?])\s+", out)
    out = " ".join(parts[:3]).strip()
    return out[:320] or None


def voice(turn_id: int) -> Path | None:
    """TTS is the slowest leg (~3.4s measured) and it is serial after the model.
    Generating it on a separate request lets the reply text and the blocker chip
    land ~3.4s earlier; the audio catches up while the judge is already reading.

    Named by a hash of the text, never by turn id: turn ids restart at 1 on every
    reseed while the wav files persist, so id-named files served the *previous*
    run's audio against the current run's text. A file can only ever contain what
    its name says."""
    turn = db.get_turn(turn_id)
    if not turn or not turn["text"]:
        return None
    digest = hashlib.sha1(turn["text"].encode("utf-8")).hexdigest()[:16]
    dest = AUDIO / f"{digest}.wav"
    if dest.exists():
        return dest
    try:
        return sarvam.tts(turn["text"], dest)
    except Exception as e:
        # A dead TTS leg must not kill the call. The reply is on screen either way.
        log.warning("tts failed, continuing text-only: %s", e)
        return None


def opening(checkin_id: int) -> dict:
    ck = db.get_checkin(checkin_id)
    ms = db.get_milestone(ck["milestone_id"])
    prior = db.prior_blockers(ck["user_id"], limit=3, before_checkin=checkin_id)

    out = sarvam.chat(
        [{"role": "system", "content": prompts.OPENING_SYSTEM},
         {"role": "user", "content": prompts.context_block(ms, prior, [])}],
        schema=prompts.OPENING_SCHEMA,
        kind="opening",
    )
    text = sanitize(out.get("reply_text"))
    if not text:
        log.warning("opening echoed the prompt, using a written line")
        last = prior[0]["blocker"] if prior else None
        text = (f"Pichhli baar tumne {last} wali baat kahi thi — "
                f"aaj {ms['title']} start kiya ya nahi?" if last
                else f"Tumne bola tha aaj {ms['title']} karoge. Start kiya?")

    turn_id = db.add_turn(checkin_id, "agent", text=text)
    return {
        "role": "agent",
        "text": text,
        "turn_id": turn_id,
        "recalled": [dict(b) for b in prior],
    }


def handle_turn(checkin_id: int, audio_path: str | Path = None,
                typed_text: str = None) -> dict:
    ck = db.get_checkin(checkin_id)
    ms = db.get_milestone(ck["milestone_id"])

    said = typed_text if typed_text else sarvam.stt(audio_path)
    prior = db.prior_blockers(ck["user_id"], limit=3, before_checkin=checkin_id)
    history = db.turns(checkin_id)

    # Call 1 — classify. The excuse and nothing else. Giving this call the ledger
    # makes it echo the ledger's blocker back for every input.
    cls = sarvam.chat(
        [{"role": "system", "content": prompts.CLASSIFY_SYSTEM},
         {"role": "user", "content": said}],
        schema=prompts.CLASSIFY_SCHEMA,
        kind="classify",
        temperature=0,
    )

    # An excuse the classifier cannot place IS an avoidant excuse. Clamping here
    # rather than raising means a bad turn costs a strategy, not the demo.
    blocker = cls.get("blocker")
    if blocker not in prompts.BLOCKER_ENUM:
        log.warning("blocker %r outside enum, clamping to avoidant", blocker)
        blocker, cls["confidence"] = "avoidant", 0.3

    strategy = prompts.STRATEGY_FOR[blocker]
    confidence = float(cls.get("confidence") or 0.5)

    # Call 2 — respond. Full context, but the branch is already decided.
    try:
        out = sarvam.chat(
            [{"role": "system", "content": prompts.RESPOND_SYSTEM},
             {"role": "user", "content": prompts.respond_block(
                 ms, prior, history, blocker, strategy, said)}],
            schema=prompts.RESPOND_SCHEMA,
            kind="respond",
        )
    except Exception as e:
        # The classification already happened, so the route is known. Falling back
        # to a canned line for THIS strategy keeps the branch visible and the call
        # alive; only the wording is lost.
        log.warning("respond call failed (%s), using %s fallback", e, strategy)
        out = dict(prompts.FALLBACK_REPLY[strategy])

    reply = sanitize(out.get("reply_text"))
    if not reply:
        log.warning("reply echoed the prompt, using %s fallback", strategy)
        out = dict(prompts.FALLBACK_REPLY[strategy])
        reply = out["reply_text"]

    db.add_turn(checkin_id, "user", text=said,
                audio_path=str(audio_path) if audio_path else None)
    turn_id = db.add_turn(checkin_id, "agent", text=reply, blocker=blocker,
                          confidence=confidence, strategy=strategy)
    db.add_blocker(ck["user_id"], ms["id"], blocker, evidence=said)

    commitment = out.get("commitment")
    board_change = None
    if commitment:
        size = max(1, min(int(commitment.get("size_min") or 3), 15))
        commitment = {**commitment, "size_min": size,
                      "label": commitment.get("label") or commitment.get("text", "")}
        db.add_commitment(checkin_id, ms["id"], commitment["text"], size,
                          clock.iso(plan.commitment_due(size)))
        board_change = plan.apply(ms, strategy, commitment)

    close = bool(out.get("close")) and commitment is not None
    if close:
        db.close_checkin(checkin_id, "completed" if strategy == "verify" else "committed")

    return {
        "user_text": said,
        "reply_text": reply,
        "turn_id": turn_id,
        "blocker": blocker,
        "confidence": round(confidence, 2),
        "strategy": strategy,
        "commitment": commitment,
        "board_change": board_change,
        "close": close,
        "recalled": [dict(b) for b in prior],
    }
