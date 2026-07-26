# Reverse Reminder

**The accountability agent that doesn't remind you — it helps you finish.**

You didn't forget the assignment. You've known for four days. The reminder app told you
six times and you swiped every one away, because a notification asks nothing of you and
knows nothing about *why* you aren't moving.

Reverse Reminder calls you when you stall, gets the real reason out of you in Hinglish,
and doesn't hang up until you've committed to something small enough that you'll actually
do it. Then it rewrites the plan around what it learned.

Sarvam Epoch Buildathon · scored capability: **Voice Experience**

---

## The mechanic

A stall has a cause, the cause is one of six things, and each one needs a different
response. The classifier picks the cause; a fixed table picks the route. Same product,
six visibly different conversations — each leaving a different shape in the database.

| Blocker | Sounds like | Strategy | What changes on the board |
|---|---|---|---|
| `confusion` | "samajh nahi aa raha" | teach | Inserts a 3-min step before the current one |
| `time` | "lab hai 9 baje tak" | reslice | Cuts the task to fit, moves it to a free slot |
| `scope_fear` | "bahut bada hai" | decompose | Splits off the single next physical action |
| `motivation` | "mann nahi kar raha" | shrink | New 3-min task, starting now |
| `avoidant` | "kal dekh lunga" | confront | No reschedule — held to it, cites the ledger |
| `done_already` | "kar liya" | verify | Milestone → done, next one promoted |

Every classified blocker is written to a **ledger**. The next call opens by naming what
you said last time.

## Architecture

```
scheduler (asyncio, 2s tick, accelerated clock)
   └─ milestone past start_at ──► SSE "ring" ──► browser phone rings, unprompted

user holds to talk ──► Saaras v3 STT ──┐
                                       ├─► classify   (excuse alone, no context)
                                       └─► respond    (blocker + ledger + history)
                                                │
                                    plan mutation + commitment + ledger row
                                                │
                                          Bulbul v3 TTS
```

FastAPI · SQLite · one vanilla-JS page · no build step.

## Run it

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # put your SARVAM_API_KEY in it
python seed.py
uvicorn main:app --port 8000
```

Open <http://127.0.0.1:8000>, click **Arm the demo** (unlocks browser audio), then don't
touch anything. The phone rings on its own within a couple of seconds.

CLI, for testing the routing without the UI:

```bash
python run.py --text "yaar samajh nahi aa raha deadlocks" "aaj lab hai 9 baje tak"
python run.py clips/01_confusion.wav
```

### Modes

| Mode | Trigger | Behaviour |
|---|---|---|
| live | `SARVAM_API_KEY` set | Real calls. Every response cached to `cache/`. |
| offline | `OFFLINE=1` | Replays `cache/` only. Fails loudly if a response is missing. |
| mock | no key, or `MOCK=1` | Canned responses, no network. Replies prefixed `[mock]`. |

## Measured, 26 July 2026

Everything below was run against the live API, not estimated.

**Classification — 8/8 on the phrasings tested.** Six blocker types plus two hard cases
(two blockers in one breath → picked the stronger; content-free mumbling → `avoidant`).
Six of the eight share wording with the few-shot examples in the prompt, so **this is not
yet a clean measurement** — the honest number comes from the recorded clips, which are
different wordings. Do not quote 8/8 as accuracy.

**Latency, warm client:**

| Leg | Time |
|---|---|
| Saaras v3 STT | 0.8–1.1 s |
| classify (sarvam-30b) | ~0.4 s |
| respond (sarvam-30b) | 1–3 s |
| Bulbul v3 TTS | 3.2–3.5 s |
| **text + chip on screen** | **~1.5–3 s** |
| voice audible | +3.4 s after that |

TTS is fetched on a separate request (`GET /voice/{turn_id}`) so the reply text and the
blocker chip render before the audio exists. Perceived latency roughly halves.

### Three findings that changed the build

1. **`sarvam-30b` reasons by default and reasoning tokens count against `max_tokens`.**
   On this app's prompts, `reasoning_effort="low"` still burned the entire budget
   thinking, returned `finish_reason="length"` and `content: None`, and took 8–43 s.
   Only an explicit JSON `null` disables it — *omitting the key is not the same thing*,
   the server default is `low`. With `null`: 0.7–1.8 s, `finish_reason="stop"`.

2. **Classification must not see the ledger.** With prior blockers in context, the model
   returned the ledger's own blocker for every excuse — 3/3 collapsed to `motivation`,
   which kills the entire idea. Splitting into two calls (classify on the excuse alone,
   then respond with full context) fixed it. Two sub-second calls beat one biased call.

3. **The model sometimes emits complete JSON then pads newlines until `max_tokens`**,
   never closing the outer brace. The payload is intact. The parser balances unclosed
   braces and a `stop` sequence cuts the padding short.

## Layout

```
main.py      FastAPI routes, SSE, scheduler
brain.py     THE MECHANIC — classify, route, mutate
plan.py      goal extraction + the six plan mutations
prompts.py   every prompt, nothing inline elsewhere
sarvam.py    stt / chat / tts + cache + mock layer
clock.py     accelerated simulated clock
db.py        SQLite schema and queries
seed.py      wipe + reseed to demo state
run.py       CLI acceptance test
```

## Not built (deliberately)

Telephony, streaming STT, barge-in, PDF/email ingest, real calendar lookup, auth,
deployment, languages beyond Hindi-English. See `../IDEA_SCOPE.md` §2 for why each is out.

## Origin note

The blocker-classification routing pattern is conceptually similar to my open-source
Loom router. **No Loom code is imported** — the routing logic was written from scratch
today. Standard scaffolding (FastAPI, SQLite) is used as permitted by the handbook.
