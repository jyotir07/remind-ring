"""SQLite schema and queries. Schema is IDEA_SCOPE.md section 5, unchanged."""
import sqlite3
from pathlib import Path

import clock

DB_PATH = Path(__file__).parent / "reverse_reminder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT, lang_pref TEXT DEFAULT 'hi-IN'
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, source TEXT,
    raw_input TEXT, due_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY, goal_id INTEGER, title TEXT, order_idx REAL,
    est_min INTEGER, start_at TEXT, status TEXT DEFAULT 'pending',
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY, user_id INTEGER, milestone_id INTEGER,
    trigger_reason TEXT, opened_at TEXT, closed_at TEXT, outcome TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, checkin_id INTEGER, idx INTEGER, role TEXT,
    audio_path TEXT, text TEXT, blocker TEXT, confidence REAL, strategy TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY, checkin_id INTEGER, milestone_id INTEGER,
    text TEXT, size_min INTEGER, due_at TEXT, honored INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS blockers (
    id INTEGER PRIMARY KEY, user_id INTEGER, milestone_id INTEGER,
    blocker TEXT, evidence TEXT, created_at TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    c = conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def wipe() -> None:
    """Windows will not unlink a file with an open handle, so every helper below
    closes its connection explicitly. `with sqlite3.connect(...)` commits a
    transaction; it does not close the connection."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    init()


def _rows(sql, args=()) -> list[dict]:
    c = conn()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def _row(sql, args=()) -> dict | None:
    r = _rows(sql, args)
    return r[0] if r else None


def _exec(sql, args=()) -> int:
    c = conn()
    try:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ---------- users / goals / milestones ----------

def add_user(name: str) -> int:
    return _exec("INSERT INTO users (name) VALUES (?)", (name,))


def add_goal(user_id, title, source, raw_input, due_at) -> int:
    return _exec(
        "INSERT INTO goals (user_id, title, source, raw_input, due_at, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (user_id, title, source, raw_input, due_at, clock.now_iso()),
    )


def add_milestone(goal_id, title, order_idx, est_min, start_at, status="pending") -> int:
    return _exec(
        "INSERT INTO milestones (goal_id, title, order_idx, est_min, start_at, status)"
        " VALUES (?,?,?,?,?,?)",
        (goal_id, title, order_idx, est_min, start_at, status),
    )


def get_milestone(mid) -> dict | None:
    return _row("SELECT * FROM milestones WHERE id=?", (mid,))


def update_milestone(mid, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    _exec(f"UPDATE milestones SET {sets} WHERE id=?", (*fields.values(), mid))


def set_status(mid, status) -> None:
    update_milestone(mid, status=status)


def milestones_for_goal(goal_id) -> list[dict]:
    return _rows(
        "SELECT * FROM milestones WHERE goal_id=? ORDER BY order_idx", (goal_id,)
    )


def latest_goal(user_id) -> dict | None:
    return _row(
        "SELECT * FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
    )


def due_milestones(now_iso: str) -> list[dict]:
    """Past their start_at and not finished. The scheduler's only query."""
    return _rows(
        "SELECT m.*, g.user_id FROM milestones m JOIN goals g ON g.id = m.goal_id"
        " WHERE m.start_at <= ? AND m.status IN ('pending','active')"
        " ORDER BY m.order_idx",
        (now_iso,),
    )


# ---------- checkins / turns ----------

def open_checkin(user_id, milestone_id, reason) -> int:
    return _exec(
        "INSERT INTO checkins (user_id, milestone_id, trigger_reason, opened_at)"
        " VALUES (?,?,?,?)",
        (user_id, milestone_id, reason, clock.now_iso()),
    )


def get_checkin(cid) -> dict | None:
    return _row("SELECT * FROM checkins WHERE id=?", (cid,))


def close_checkin(cid, outcome) -> None:
    _exec(
        "UPDATE checkins SET closed_at=?, outcome=? WHERE id=?",
        (clock.now_iso(), outcome, cid),
    )


def any_open_checkin() -> dict | None:
    return _row("SELECT * FROM checkins WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1")


def open_checkin_for(milestone_id) -> dict | None:
    return _row(
        "SELECT * FROM checkins WHERE milestone_id=? AND closed_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (milestone_id,),
    )


def add_turn(checkin_id, role, text, audio_path=None, blocker=None,
             confidence=None, strategy=None) -> int:
    idx = len(turns(checkin_id))
    return _exec(
        "INSERT INTO turns (checkin_id, idx, role, audio_path, text, blocker,"
        " confidence, strategy, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (checkin_id, idx, role, audio_path, text, blocker, confidence, strategy,
         clock.now_iso()),
    )


def get_turn(tid) -> dict | None:
    return _row("SELECT * FROM turns WHERE id=?", (tid,))


def turns(checkin_id) -> list[dict]:
    return _rows("SELECT * FROM turns WHERE checkin_id=? ORDER BY idx", (checkin_id,))


# ---------- the ledger ----------

def add_blocker(user_id, milestone_id, blocker, evidence) -> int:
    return _exec(
        "INSERT INTO blockers (user_id, milestone_id, blocker, evidence, created_at)"
        " VALUES (?,?,?,?,?)",
        (user_id, milestone_id, blocker, evidence, clock.now_iso()),
    )


def prior_blockers(user_id, limit=3, before_checkin=None) -> list[dict]:
    """Ledger entries from BEFORE this call. Feeding a call its own blockers back
    makes the agent repeat itself, so the current call's rows are excluded."""
    if before_checkin is None:
        return _rows(
            "SELECT * FROM blockers WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
    ck = get_checkin(before_checkin)
    return _rows(
        "SELECT * FROM blockers WHERE user_id=? AND created_at < ?"
        " ORDER BY id DESC LIMIT ?",
        (user_id, ck["opened_at"], limit),
    )


def all_blockers(user_id) -> list[dict]:
    return _rows("SELECT * FROM blockers WHERE user_id=? ORDER BY id DESC", (user_id,))


# ---------- commitments ----------

def add_commitment(checkin_id, milestone_id, text, size_min, due_at) -> int:
    return _exec(
        "INSERT INTO commitments (checkin_id, milestone_id, text, size_min, due_at)"
        " VALUES (?,?,?,?,?)",
        (checkin_id, milestone_id, text, size_min, due_at),
    )


def commitments(user_id) -> list[dict]:
    return _rows(
        "SELECT c.* FROM commitments c JOIN checkins k ON k.id = c.checkin_id"
        " WHERE k.user_id=? ORDER BY c.id DESC",
        (user_id,),
    )
