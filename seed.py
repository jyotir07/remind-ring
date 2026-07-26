"""Wipe and reseed to the demo state. Run between every rehearsal."""
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import clock  # noqa: E402
import db     # noqa: E402

GOAL = "DBMS assignment — deadlocks unit"
MILESTONES = [
    ("Read the deadlocks section", 25),
    ("Write the introduction", 30),
    ("Solve the numerical problems", 45),
]

# The excuse from "yesterday". Without it the first call has no history and the
# memory behaviour has nothing to demonstrate.
PRIOR_BLOCKER = ("motivation", "kal bhi mann nahi kar raha tha, subah karunga bola tha")


def run() -> int:
    clock.reset()
    db.wipe()

    user_id = db.add_user("Jyotir")
    now = clock.now()
    goal_id = db.add_goal(user_id, GOAL, "seed", GOAL,
                          clock.iso(now + timedelta(days=1)))

    # First milestone starts in the PAST, so the ring is guaranteed on the next
    # tick. Never rely on live clock drift during a demo.
    cursor = now - timedelta(minutes=40)
    for i, (title, est) in enumerate(MILESTONES, start=1):
        db.add_milestone(goal_id, title, float(i), est, clock.iso(cursor))
        cursor += timedelta(minutes=est + 90)

    db.add_blocker(user_id, None, PRIOR_BLOCKER[0], PRIOR_BLOCKER[1])
    return goal_id


if __name__ == "__main__":
    run()
    print(f"seeded · sim now {clock.now_iso()} · first milestone already overdue")
