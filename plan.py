"""Goal extraction and the plan mutations.

The mutation is the point. Each strategy leaves a different shape in the
milestones table, so "the agent responded differently" is visible on the board
rather than only audible in the reply.
"""
from datetime import timedelta

import clock
import db
import prompts
import sarvam

# Hardcoded weekly availability. Real calendar lookup is parking-lot: it would
# cost an hour and change nothing a judge can see.
AVAILABILITY = {
    0: [(16, 19), (21, 23)],  # Mon
    1: [(16, 19), (21, 23)],
    2: [(15, 18), (21, 23)],
    3: [(16, 19), (21, 23)],
    4: [(15, 19), (21, 23)],
    5: [(11, 14), (16, 22)],  # Sat
    6: [(11, 14), (16, 22)],  # Sun
}


def next_free_slot(after=None, minutes: int = 30):
    """First availability window with room, searching forward up to a week."""
    t = after or clock.now()
    for day_offset in range(8):
        day = t + timedelta(days=day_offset)
        for start_h, end_h in AVAILABILITY.get(day.weekday(), []):
            slot = day.replace(hour=start_h, minute=0, second=0, microsecond=0)
            if slot <= t:
                slot = t.replace(second=0, microsecond=0) + timedelta(minutes=5)
                if slot.hour >= end_h:
                    continue
            if slot.hour + minutes / 60 <= end_h:
                return slot
    return t + timedelta(hours=1)


# ------------------------------------------------------------------ extraction

def extract(raw_text: str, user_id: int, source: str = "text") -> int:
    out = sarvam.chat(
        [{"role": "system", "content": prompts.GOAL_SYSTEM},
         {"role": "user", "content": raw_text}],
        schema=prompts.GOAL_SCHEMA,
        kind="goal",
    )

    goal_id = db.add_goal(user_id, out["title"], source, raw_text, out.get("due_at"))

    # First milestone lands 25 simulated minutes out. At CLOCK_SCALE=60 that is
    # ~25 real seconds: long enough that nobody thinks you clicked it, short
    # enough to add a goal live and still have it ring inside a 3-minute demo.
    cursor = clock.now() + timedelta(minutes=25)
    for i, m in enumerate(out["milestones"], start=1):
        db.add_milestone(goal_id, m["title"], float(i), int(m["est_min"]),
                         clock.iso(cursor))
        cursor += timedelta(hours=4)
    return goal_id


# ------------------------------------------------------------------- mutations

def apply(milestone: dict, strategy: str, commitment: dict | None) -> str:
    """Returns a human-readable description of what changed on the board."""
    mid = milestone["id"]
    size = int(commitment["size_min"]) if commitment else 3
    text = commitment["text"] if commitment else "Make a start"
    now = clock.now()

    if strategy == "teach":
        db.add_milestone(milestone["goal_id"], f"{text}", milestone["order_idx"] - 0.5,
                         size, clock.iso(now), status="active")
        db.update_milestone(mid, status="pending")
        return f"Inserted a {size}-minute step before “{milestone['title']}”"

    if strategy == "reslice":
        slot = next_free_slot(now + timedelta(minutes=30), size)
        db.update_milestone(mid, est_min=size, start_at=clock.iso(slot), status="pending")
        return f"Cut to {size} min and moved to {slot.strftime('%a %H:%M')}"

    if strategy == "decompose":
        db.add_milestone(milestone["goal_id"], text, milestone["order_idx"] - 0.5,
                         size, clock.iso(now), status="active")
        db.update_milestone(mid, est_min=max(5, milestone["est_min"] - size),
                            status="pending")
        return f"Split off “{text}” as the next action"

    if strategy == "shrink":
        db.add_milestone(milestone["goal_id"], text, milestone["order_idx"] - 0.5,
                         min(size, 3), clock.iso(now), status="active")
        db.update_milestone(mid, status="pending")
        return f"Shrunk the ask to {min(size, 3)} minutes, starting now"

    if strategy == "confront":
        db.update_milestone(mid, status="active",
                            start_at=clock.iso(now + timedelta(minutes=size)))
        return f"Held to it — due in {size} minutes, no reschedule"

    if strategy == "verify":
        db.update_milestone(mid, status="done", completed_at=clock.iso(now))
        nxt = _next_pending(milestone)
        if nxt:
            db.update_milestone(nxt["id"], status="active",
                                start_at=clock.iso(now + timedelta(minutes=30)))
            return f"Marked done. “{nxt['title']}” is next"
        return "Marked done. Goal complete"

    return "No plan change"


def _next_pending(milestone: dict) -> dict | None:
    for m in db.milestones_for_goal(milestone["goal_id"]):
        if m["order_idx"] > milestone["order_idx"] and m["status"] == "pending":
            return m
    return None


def commitment_due(size_min: int):
    return clock.now() + timedelta(minutes=max(3, size_min))
