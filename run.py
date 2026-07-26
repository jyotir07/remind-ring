"""CLI: python run.py clips/01_confusion.wav [clips/03_motivation.wav ...]

The M1 acceptance test. Runs each clip as a full check-in against a fresh seed
and prints the classification, the strategy, the reply and the board change.
Use --text to pass an excuse as a string instead of audio.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import brain    # noqa: E402
import db       # noqa: E402
import sarvam   # noqa: E402
import seed     # noqa: E402

BAR = "─" * 68


def one(source: str, is_text: bool) -> dict:
    goal_id = seed.run()
    ms = db.milestones_for_goal(goal_id)[0]
    db.set_status(ms["id"], "stalled")
    cid = db.open_checkin(1, ms["id"], "cli")

    print(BAR)
    print(f"INPUT   {source}")

    op = brain.opening(cid)
    print(f"AGENT   {op['text']}")
    if op["recalled"]:
        print(f"        (recalled: {op['recalled'][0]['blocker']} — "
              f"\"{op['recalled'][0]['evidence'][:48]}...\")")

    out = brain.handle_turn(cid, None, source) if is_text else brain.handle_turn(cid, source)

    print(f"USER    {out['user_text']}")
    print(f"CHIP    {out['blocker']} · {out['confidence']}  →  {out['strategy']}")
    print(f"AGENT   {out['reply_text']}")
    if out["commitment"]:
        print(f"DEAL    {out['commitment']['text']}  ({out['commitment']['size_min']} min)")
    if out["board_change"]:
        print(f"BOARD   {out['board_change']}")
    print(f"STATE   {[(m['title'][:28], m['status']) for m in db.milestones_for_goal(goal_id)]}")
    return out


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    is_text = args[0] == "--text"
    if is_text:
        args = args[1:]

    print(f"mode = {sarvam.mode()}")
    results = []
    for a in args:
        results.append((a, one(a, is_text)))

    print(BAR)
    print("SUMMARY")
    for name, r in results:
        deal = f"{r['commitment']['size_min']}min" if r["commitment"] else "no deal"
        print(f"  {Path(name).name[:34]:<34} {r['blocker']:<13} "
              f"{r['confidence']:<5} {r['strategy']:<10} {deal}")


if __name__ == "__main__":
    main()
