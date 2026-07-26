"""THE MECHANIC — classify the blocker, route to a strategy, mutate the plan."""
import logging
from pathlib import Path

import clock
import db
import plan
import prompts
import sarvam

log = logging.getLogger("brain")
AUDIO = Path(__file__).parent / "audio"


def _voice(text: str, name: str) -> str:
    try:
        sarvam.tts(text, AUDIO / f"{name}.wav")
        return f"/audio/{name}.wav"
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
    text = out["reply_text"]

    db.add_turn(checkin_id, "agent", text=text)
    return {
        "role": "agent",
        "text": text,
        "audio_url": _voice(text, f"ck{checkin_id}_open"),
        "recalled": [dict(b) for b in prior],
    }


def handle_turn(checkin_id: int, audio_path: str | Path = None,
                typed_text: str = None) -> dict:
    ck = db.get_checkin(checkin_id)
    ms = db.get_milestone(ck["milestone_id"])

    said = typed_text if typed_text else sarvam.stt(audio_path)
    prior = db.prior_blockers(ck["user_id"], limit=3, before_checkin=checkin_id)
    history = db.turns(checkin_id)

    out = sarvam.chat(
        [{"role": "system", "content": prompts.SYSTEM},
         {"role": "user", "content": prompts.context_block(ms, prior, history)},
         {"role": "user", "content": f'they said: "{said}"'}],
        schema=prompts.TURN_SCHEMA,
        kind="turn",
    )

    # An excuse the classifier cannot place IS an avoidant excuse. Clamping here
    # rather than raising means a bad turn costs a strategy, not the demo.
    blocker = out.get("blocker")
    if blocker not in prompts.BLOCKER_ENUM:
        log.warning("blocker %r outside enum, clamping to avoidant", blocker)
        blocker, out["confidence"] = "avoidant", 0.3

    # The model proposes both; the route is a fixed table so the branch is
    # deterministic and the accuracy count means something.
    strategy = prompts.STRATEGY_FOR[blocker]
    if out.get("strategy") != strategy:
        log.info("model said strategy=%s, table says %s", out.get("strategy"), strategy)

    confidence = float(out.get("confidence") or 0.5)
    reply = out.get("reply_text") or "Ek chhota sa step batao jo abhi kar sakte ho?"

    db.add_turn(checkin_id, "user", text=said,
                audio_path=str(audio_path) if audio_path else None)
    db.add_turn(checkin_id, "agent", text=reply, blocker=blocker,
                confidence=confidence, strategy=strategy)
    db.add_blocker(ck["user_id"], ms["id"], blocker, evidence=said)

    commitment = out.get("commitment")
    board_change = None
    if commitment:
        size = max(1, min(int(commitment.get("size_min") or 3), 15))
        db.add_commitment(checkin_id, ms["id"], commitment["text"], size,
                          clock.iso(plan.commitment_due(size)))
        board_change = plan.apply(ms, strategy, {**commitment, "size_min": size})

    close = bool(out.get("close")) and commitment is not None
    if close:
        db.close_checkin(checkin_id, "completed" if strategy == "verify" else "committed")

    idx = len(history)
    return {
        "user_text": said,
        "reply_text": reply,
        "blocker": blocker,
        "confidence": round(confidence, 2),
        "strategy": strategy,
        "commitment": {**commitment, "size_min": size} if commitment else None,
        "board_change": board_change,
        "close": close,
        "audio_url": _voice(reply, f"ck{checkin_id}_{idx}"),
        "recalled": [dict(b) for b in prior],
    }
