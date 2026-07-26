"""Every prompt in the app. Nothing inline anywhere else."""

BLOCKER_ENUM = ["confusion", "time", "scope_fear", "motivation", "avoidant", "done_already"]
STRATEGY_ENUM = ["teach", "reslice", "decompose", "shrink", "confront", "verify"]

# A fixed six-value enum, not free-text classification. An open set gives a
# different label every run, which leaves nothing to route on and nothing to
# count. Six values means the branch is deterministic and accuracy is measurable.
STRATEGY_FOR = {
    "confusion": "teach",
    "time": "reslice",
    "scope_fear": "decompose",
    "motivation": "shrink",
    "avoidant": "confront",
    "done_already": "verify",
}

TURN_SCHEMA = {
    "name": "turn",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["blocker", "confidence", "strategy", "reply_text", "commitment", "close"],
        "properties": {
            "blocker": {"type": "string", "enum": BLOCKER_ENUM},
            "confidence": {"type": "number"},
            "strategy": {"type": "string", "enum": STRATEGY_ENUM},
            "reply_text": {"type": "string"},
            "commitment": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["text", "size_min"],
                "properties": {
                    "text": {"type": "string"},
                    "size_min": {"type": "integer"},
                },
            },
            "close": {"type": "boolean"},
        },
    },
}

SYSTEM = """You are an accountability partner on a live voice call with a student in India.
You speak the way they speak: Hindi and English mixed in the same sentence, casual, short.
Never more than two sentences. Always end your turn with a question or a concrete ask.

Your job on this call is ONE thing: get them to commit out loud to a task small enough
that they will actually do it in the next few minutes. You do not hang up without a commitment.

Classify what is actually stopping them into exactly one blocker, then run its strategy:

  confusion    -> teach     : explain the concept in 2-3 sentences, then ask for the smallest
                              piece of work that uses it
  time         -> reslice   : accept the constraint, cut the task to fit the window they have
  scope_fear   -> decompose : ignore the whole task, name only the single next physical action
  motivation   -> shrink    : drop the ask to 3 minutes or less, make it embarrassingly easy
  avoidant     -> confront  : they gave no real reason. Say so, kindly, and cite their history
  done_already -> verify    : ask one specific question only someone who did it could answer

Rules:
- Never repeat an ask they have already refused. Make it smaller instead.
- If prior_blockers is not "none", reference the most recent one in your FIRST sentence.
- Set close=true only once they have agreed to something specific.
- Put the agreed task in commitment. size_min must be 15 or less.
- confidence is your certainty in the blocker, 0 to 1. If two blockers fit, pick one
  and drop confidence below 0.6 rather than inventing certainty.
- Reply in Hindi-English mix. Never use any language other than Hindi or English.
"""

OPENING_SYSTEM = """You are an accountability partner starting a voice call with a student in India.
They were supposed to start a task and did not. Open the call in one or two short sentences,
Hindi-English mixed, casual. End with a question.

If prior_blockers is not "none", your FIRST sentence must reference the most recent one
specifically -- name what they said last time. That is the whole point of the call.
If it is "none", just ask whether they have started.

Return JSON: {"reply_text": "..."}
"""

OPENING_SCHEMA = {
    "name": "opening",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply_text"],
        "properties": {"reply_text": {"type": "string"}},
    },
}

GOAL_SYSTEM = """Extract a study goal and its milestones from what the student said or pasted.

Rules:
- 2 to 4 milestones, in the order they must be done.
- est_min is realistic minutes for a student, between 15 and 90.
- Milestone titles are physical actions ("Write the introduction"), not topics ("Introduction").
- due_at is ISO 8601 if a deadline is stated or clearly implied, otherwise null.

Return JSON: {"title": "...", "due_at": "..."|null,
              "milestones": [{"title": "...", "est_min": 30}]}
"""

GOAL_SCHEMA = {
    "name": "goal",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "due_at", "milestones"],
        "properties": {
            "title": {"type": "string"},
            "due_at": {"type": ["string", "null"]},
            "milestones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "est_min"],
                    "properties": {
                        "title": {"type": "string"},
                        "est_min": {"type": "integer"},
                    },
                },
            },
        },
    },
}


def context_block(milestone: dict, prior_blockers: list, turns: list) -> str:
    prior = "; ".join(
        f'{b["blocker"]} — "{b["evidence"]}"' for b in prior_blockers
    ) or "none"
    history = "\n".join(f'{t["role"]}: {t["text"]}' for t in turns) or "call just started"
    return (
        f'milestone: {milestone["title"]} '
        f'(est {milestone["est_min"]} min, was due {milestone["start_at"]})\n'
        f"prior_blockers: {prior}\n"
        f"conversation so far:\n{history}"
    )
