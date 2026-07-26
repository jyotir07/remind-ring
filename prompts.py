"""Every prompt in the app. Nothing inline anywhere else.

Two calls per turn, not one. Classification runs on the excuse ALONE — no ledger,
no milestone, no history. Measured 26 July: with the ledger in context the model
returned the ledger's own blocker for every excuse (3/3 collapsed to `motivation`).
Isolating the classifier fixed it: 8/8. Two sub-second calls beat one biased one.
"""

BLOCKER_ENUM = ["confusion", "time", "scope_fear", "motivation", "avoidant", "done_already"]
STRATEGY_ENUM = ["teach", "reslice", "decompose", "shrink", "confront", "verify"]

# The route is a fixed table, not a model decision. The classifier picks the
# cause; the strategy follows deterministically. That makes the branch repeatable
# and makes accuracy a number you can actually count.
STRATEGY_FOR = {
    "confusion": "teach",
    "time": "reslice",
    "scope_fear": "decompose",
    "motivation": "shrink",
    "avoidant": "confront",
    "done_already": "verify",
}

# ─────────────────────────────── call 1: classify ───────────────────────────────

CLASSIFY_SYSTEM = """You label ONE excuse from a student who has not started their work.
Choose exactly one label. Judge only the words in the excuse. Ignore any history.

confusion    - they do not understand the material. "samajh nahi aa raha", "I don't get X"
time         - a concrete external commitment blocks them. "lab hai 9 baje tak", "class hai"
scope_fear   - the task feels too large to enter. "bahut bada hai", "kahan se start karun"
motivation   - no reason given except not feeling like it. "mann nahi kar raha", "mood nahi hai"
avoidant     - a vague promise that defers without any reason. "ho jayega", "kal dekh lunga"
done_already - they claim it is already finished. "kar liya", "ho gaya"

Examples:
"yaar samajh hi nahi aa raha deadlocks wala portion" -> confusion 0.93
"aaj lab hai nau baje tak, time nahi milega" -> time 0.92
"bahut bada hai yaar, kahan se shuru karun" -> scope_fear 0.9
"pata nahi bas mann nahi kar raha aaj" -> motivation 0.9
"haan haan ho jayega, kal dekh lunga" -> avoidant 0.88
"arre wo to kar liya maine kal raat" -> done_already 0.94

If two labels genuinely fit, pick the stronger one and set confidence below 0.6.
Return only JSON."""

CLASSIFY_SCHEMA = {
    "name": "classification",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["blocker", "confidence"],
        "properties": {
            "blocker": {"type": "string", "enum": BLOCKER_ENUM},
            "confidence": {"type": "number"},
        },
    },
}

# ─────────────────────────────── call 2: respond ────────────────────────────────

STRATEGY_BRIEF = {
    "teach": "They do not understand the material. Explain the concept in two short "
             "sentences, concretely, then ask for the smallest piece of work that uses it.",
    "reslice": "They have a real external commitment. Accept it without arguing, cut the "
               "task down to fit the window they actually have, and name the new size.",
    "decompose": "The task feels too big. Do not mention the whole task. Name only the "
                 "single next physical action, something they could start in ten seconds.",
    "shrink": "They have no reason beyond not feeling like it. Drop the ask to three "
              "minutes or less. Make it embarrassingly small. Do not lecture.",
    "confront": "They gave no real reason, just a vague deferral. Say so, kindly and "
                "directly. If prior_blockers shows they did this before, name it. Then "
                "ask for something tiny right now, not tomorrow.",
    "verify": "They claim it is done. Ask ONE specific question only someone who actually "
              "did it could answer. Do not congratulate them yet.",
}

RESPOND_SYSTEM = """You are an accountability partner on a live voice call with a student in India.
You speak the way they speak: Hindi and English mixed in the same sentence, casual, short.
Maximum two sentences. Always end with a question or a concrete ask.

You do not hang up without a commitment. Your one job on this call is to get them to
agree out loud to something small enough that they will actually do it in the next
few minutes.

You have already been told why they are stuck and which strategy to run. Run it.

Rules:
- Never repeat an ask they have already refused. Make it smaller instead.
- If prior_blockers is not "none", reference the most recent one in your FIRST sentence.
- close=true only once they have agreed to something specific. Then commitment must be set.
- commitment.text is what THEY will do, phrased as an action, one sentence.
- commitment.label is the same thing in AT MOST 5 words, English, title case off.
  Example: text "Bas ek paragraph likh do intro ka" -> label "Write intro paragraph".
- size_min must be the SAME number of minutes you said out loud in reply_text.
  If you said "teen minute", size_min is 3. Never larger than 10. Default to 3.

Output ONLY the JSON object. Never repeat, quote, or summarise the information you
were given above — no milestone lines, no prior_blockers, no timestamps, no
"conversation so far". reply_text is speech: it is read aloud to the student exactly
as written, so it must contain nothing but what a person would say on a phone call.
- Reply in Hindi-English mix — Hindi words in Roman script, English words as they are.
  Not pure English. Not pure Hindi. This is how they actually speak.
- Never output more than two sentences. This is a phone call, not an essay.
"""

# Used only when the respond call fails outright. The classification has already
# happened by then, so the route stays visible and the call survives.
FALLBACK_REPLY = {
    "teach": {"reply_text": "Chalo teen minute isko saath dekhte hain — pehle ek line "
                            "batao, tumhe kaunsa part atak raha hai?",
              "commitment": {"label": "Name the stuck part",
                             "text": "Name the exact part that is confusing", "size_min": 3},
              "close": False},
    "reslice": {"reply_text": "Theek hai, aaj time nahi hai. Poora nahi — sirf paanch "
                              "minute ka pehla hissa, baad mein kar loge?",
                "commitment": {"label": "First five-minute slice",
                               "text": "Do the first five-minute slice later today", "size_min": 5},
                "close": True},
    "decompose": {"reply_text": "Poora mat socho. Sirf file kholo aur heading likh do — "
                                "bas itna, abhi kar sakte ho?",
                  "commitment": {"label": "Open file, write heading",
                                 "text": "Open the file and write the heading", "size_min": 3},
                  "close": True},
    "shrink": {"reply_text": "Mann nahi kar raha, theek hai. Sirf teen minute — ek "
                             "paragraph. Itna de sakte ho?",
               "commitment": {"label": "Write one paragraph",
                              "text": "Write one paragraph, three minutes", "size_min": 3},
               "close": True},
    "confront": {"reply_text": "Yeh tum pehle bhi bol chuke ho. Kal nahi — abhi paanch "
                               "minute, ek line likho. Haan ya na?",
                 "commitment": {"label": "Write one line now",
                                "text": "Write one line right now", "size_min": 5},
                 "close": True},
    "verify": {"reply_text": "Achha! Ek cheez batao — usme aakhri point kya likha tha "
                             "tumne? Phir done mark kar deta hoon.",
               "commitment": {"label": "Confirm last point",
                              "text": "Confirm the last point written", "size_min": 3},
               "close": True},
}

RESPOND_SCHEMA = {
    "name": "reply",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply_text", "commitment", "close"],
        "properties": {
            "reply_text": {"type": "string"},
            "commitment": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["label", "text", "size_min"],
                "properties": {
                    # label becomes the card title on the board, so it has to be
                    # short. text is the full sentence, shown when the card opens.
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                    "size_min": {"type": "integer"},
                },
            },
            "close": {"type": "boolean"},
        },
    },
}

# ─────────────────────────────── opening line ───────────────────────────────────

OPENING_SYSTEM = """You are an accountability partner starting a voice call with a student in India.
They were supposed to start a task and did not. Open the call in ONE short sentence,
Hindi-English mixed, casual, ending in a question.

If prior_blockers is not "none", that sentence must name what they said last time,
specifically. That is the whole reason you are calling.
If it is "none", just ask whether they have started.

Write in Hindi-English mix — Hindi in Roman script, English words as they are.
Not pure English.

Examples of the right voice:
  "Kal bhi tumne bola tha mann nahi kar raha — aaj deadlocks wala section start kiya?"
  "Tumne kaha tha subah karoge, ab tak intro likha ya nahi?"

Never more than one sentence.

Output ONLY the JSON object. Never repeat, quote, or summarise the information you
were given — no milestone lines, no prior_blockers, no timestamps, no "conversation
so far". reply_text is read aloud exactly as written, so it must contain nothing but
what a person would say on a phone call."""

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

# ─────────────────────────────── goal extraction ────────────────────────────────

GOAL_SYSTEM = """Extract a study goal and its milestones from what the student said or pasted.

Rules:
- 2 to 4 milestones, in the order they must be done.
- est_min is realistic minutes for a student, between 15 and 90.
- Milestone titles are physical actions ("Write the introduction"), not topics
  ("Introduction"), and AT MOST 6 words. They are cards on a board, not sentences.
- note is one short sentence saying what the milestone actually involves.
- due_at is ISO 8601 if a deadline is stated or clearly implied, otherwise null.

Return only JSON."""

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
                    "required": ["title", "note", "est_min"],
                    "properties": {
                        "title": {"type": "string"},
                        "note": {"type": "string"},
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


def respond_block(milestone: dict, prior_blockers: list, turns: list,
                  blocker: str, strategy: str, said: str) -> str:
    return (
        f"{context_block(milestone, prior_blockers, turns)}\n"
        f'they just said: "{said}"\n\n'
        f"their blocker: {blocker}\n"
        f"your strategy: {strategy} — {STRATEGY_BRIEF[strategy]}"
    )
