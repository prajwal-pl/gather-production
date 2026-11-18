"""Reply classification: does this inbound message change a task?"""

from __future__ import annotations

from datetime import date

from ..domain import Message, ReplyIntent, due_from_text, parse_due_date
from ..llm import BaseLLM

SYSTEM = """You classify a single email message against one tracked follow-up task.

Choose exactly one intent:
- done: the work is finished or delivered
- in_progress: actively being worked on, no date change
- blocked: waiting on someone or something else
- date_change: the delivery date moved, or a new date is proposed
- unrelated: nothing in this message changes the task
- needs_review: ambiguous, or the message is about the task but you cannot tell

Rules:
- Judge only what this message says. Do not assume progress from politeness.
- Ignore quoted text from earlier messages; it is not new information.
- For date_change, set new_due_date to YYYY-MM-DD only if a date is explicit or unambiguous.
- Use needs_review when confidence would be below 0.6.
- confidence is your own calibrated 0.0-1.0.

Return ONLY JSON matching the schema."""


def classify_reply(
    llm: BaseLLM,
    *,
    task: dict,
    message: Message,
    today: date | None = None,
) -> ReplyIntent:
    today = today or date.today()
    user = f"""TASK: {task['title']}
OWNER: {task.get('owner_email') or 'unassigned'}
DUE: {task.get('due_date') or 'none'}
STATUS: {task.get('status')}
TODAY: {today.isoformat()}

FROM: {message.from_email}
SUBJECT: {message.subject}
MESSAGE:
<<<
{message.body_text[:4000]}
>>>"""
    intent = llm.json(ReplyIntent, SYSTEM, user, hint="reply_intent")

    # Never trust the model with date arithmetic; recover it from the text.
    parsed = due_from_text(message.body_text, today) or parse_due_date(
        intent.new_due_date, today
    )
    intent.new_due_date = parsed.isoformat() if parsed else None
    return intent
