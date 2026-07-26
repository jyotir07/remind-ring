"""FastAPI app: routes, SSE ring channel, and the scheduler that fires the call."""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, File, Form, UploadFile          # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles                  # noqa: E402

import brain      # noqa: E402
import clock      # noqa: E402
import db         # noqa: E402
import plan       # noqa: E402
import sarvam     # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

ROOT = Path(__file__).parent
AUDIO = ROOT / "audio"
CLIPS = ROOT / "clips"
UPLOADS = ROOT / "uploads"
for d in (AUDIO, CLIPS, UPLOADS):
    d.mkdir(exist_ok=True)

USER_ID = 1
TICK_SECONDS = 2

_subscribers: set[asyncio.Queue] = set()


async def publish(event: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def scheduler() -> None:
    """The call fires because time passed, not because anyone clicked. Two real
    seconds is fast enough to feel live and slow enough not to be a busy loop."""
    while True:
        try:
            for ms in db.due_milestones(clock.now_iso()):
                if db.open_checkin_for(ms["id"]):
                    continue
                db.set_status(ms["id"], "stalled")
                cid = db.open_checkin(ms["user_id"], ms["id"], "missed start")
                log.info("RING milestone=%s checkin=%s sim=%s",
                         ms["title"], cid, clock.now_iso())
                await publish({"type": "ring", "checkin_id": cid,
                               "title": ms["title"], "est_min": ms["est_min"]})
                break                     # one call at a time
        except Exception as e:
            log.exception("scheduler tick failed: %s", e)
        await asyncio.sleep(TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    clock.reset()
    log.info("mode=%s  clock_scale=%s  sim_now=%s",
             sarvam.mode(), clock.SCALE, clock.now_iso())
    if sarvam.mode() == "mock":
        log.warning("NO SARVAM_API_KEY — running on canned responses. "
                    "Replies are prefixed [mock]. Not demo-ready.")
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/audio/{name}")
async def audio(name: str):
    return FileResponse(AUDIO / Path(name).name, media_type="audio/wav")


@app.get("/events")
async def events():
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    _subscribers.add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'mode': sarvam.mode()})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/board")
async def board():
    goal = db.latest_goal(USER_ID)
    milestones = db.milestones_for_goal(goal["id"]) if goal else []
    return {
        "mode": sarvam.mode(),
        "sim_now": clock.now_iso(),
        "goal": goal,
        "milestones": milestones,
        "ledger": db.all_blockers(USER_ID)[:8],
        "commitments": db.commitments(USER_ID)[:8],
        "clips": sorted(p.name for p in CLIPS.iterdir()
                        if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".webm", ".ogg")),
    }


@app.post("/goal")
async def add_goal(text: str = Form(None), audio: UploadFile = File(None)):
    if audio is not None:
        dest = UPLOADS / f"goal_{audio.filename}"
        dest.write_bytes(await audio.read())
        text = await asyncio.to_thread(sarvam.stt, dest)
        source = "voice"
    else:
        source = "text"
    if not text or not text.strip():
        return {"error": "empty goal"}

    goal_id = await asyncio.to_thread(plan.extract, text.strip(), USER_ID, source)
    await publish({"type": "board"})
    return {"goal_id": goal_id, "transcript": text}


@app.post("/answer/{checkin_id}")
async def answer(checkin_id: int):
    return await asyncio.to_thread(brain.opening, checkin_id)


@app.post("/turn/{checkin_id}")
async def turn(checkin_id: int, audio: UploadFile = File(None),
               clip: str = Form(None), text: str = Form(None)):
    if audio is not None:
        path = UPLOADS / f"ck{checkin_id}_{audio.filename or 'turn.webm'}"
        path.write_bytes(await audio.read())
    elif clip:
        path = CLIPS / Path(clip).name
    else:
        path = None

    out = await asyncio.to_thread(
        brain.handle_turn, checkin_id, path, text if not path else None
    )
    await publish({"type": "board"})
    return out


@app.post("/hangup/{checkin_id}")
async def hangup(checkin_id: int):
    ck = db.get_checkin(checkin_id)
    if ck and not ck["closed_at"]:
        db.close_checkin(checkin_id, "abandoned")
        db.set_status(ck["milestone_id"], "stalled")
    await publish({"type": "board"})
    return {"ok": True}


@app.post("/reset")
async def reset():
    """Reseed to the demo state between runs. seed.py, over HTTP."""
    import seed
    await asyncio.to_thread(seed.run)     # resets the clock itself
    await publish({"type": "board"})
    return {"ok": True, "sim_now": clock.now_iso()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")),
                reload=False)
