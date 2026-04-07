import json
import os
import re
from collections import Counter
from datetime import date
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

_SYSTEM = (
    "You are a precise productivity assistant. "
    "You MUST acknowledge every task listed in 'tasks_due_today' — do not skip, merge, or omit any. "
    "For each due task, use its 'verb_hint' as the opening action verb. "
    "Output 3-4 imperative sentences that cover all deadlines, associate their tags, "
    "and suggest preparation steps based on recent notes. "
    "If one tag dominates the workload, warn about the imbalance. "
    "Return plain text only — no JSON, no markdown, no bullet points."
)

_USER_PROMPT = (
    "Using the data below, write exactly 3-4 imperative sentences:\n"
    "1. For EVERY task in 'tasks_due_today', use its 'verb_hint' and include its tags in brackets.\n"
    "2. For urgent tasks in 'upcoming_urgent', suggest one specific preparation step to start today.\n"
    "3. If 'tag_density.dominant_tag' is heavily represented, warn about workload imbalance.\n"
    "4. Recommend a note from 'recent_notes' to review for a due task; if none fits, suggest creating one.\n\n"
)

# Ordered keyword → verb mapping; first match wins
_VERB_RULES: list[tuple[list[str], str]] = [
    (["midterm", "final exam", "exam", "quiz", "test prep"], "Study for"),
    (["meeting", "sync", "standup", "stand-up", "call", "1:1", "interview"], "Attend"),
    (["report", "draft", "essay", "paper", "proposal", "write"], "Finalize"),
    (["gym", "workout", "work out", "run", "exercise", "training", "lift"], "Complete your session for"),
    (["review", "read", "audit"], "Review"),
    (["deploy", "release", "ship", "launch"], "Deploy"),
    (["debug", "fix", "patch", "resolve", "bug"], "Fix"),
    (["presentation", "present", "slides", "demo"], "Prepare your presentation for"),
    (["submit", "turn in", "upload", "hand in"], "Submit"),
    (["project", "build", "implement", "develop", "sprint"], "Work on"),
]


def _get_verb_hint(title: str) -> str:
    """Return the best imperative action verb for a task title via keyword matching.

    _VERB_RULES is evaluated in order and the first match wins.
    Falls back to "Complete" if no keyword matches.
    """
    lower = title.lower()
    for keywords, verb in _VERB_RULES:
        if any(kw in lower for kw in keywords):
            return verb
    return "Complete"


def _strip_html(text: str) -> str:
    """Remove all HTML tags from a string so raw note content is safe to send to the LLM."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _priority_level(task: dict) -> str:
    """Derive a human-readable priority label from urgent flag and complexity score."""
    if task.get("urgent"):
        return "high"
    complexity = task.get("complexity") or 0
    if complexity >= 4:
        return "medium-high"
    return "normal"


def _build_task_entry(t: dict) -> dict:
    """Flatten a raw task dict into the minimal shape sent to the LLM."""
    return {
        "title": t.get("title", ""),
        "verb_hint": _get_verb_hint(t.get("title", "")),
        "priority_level": _priority_level(t),
        "urgent": t.get("urgent", False),
        "due_date": str(t.get("due_date", ""))[:10] if t.get("due_date") else None,
        "tags": [tag.get("name", "") for tag in (t.get("tags") or [])],
        "category": t.get("category"),
    }


async def create_daily_briefing(tasks: list[dict], notes: list[dict]) -> str:
    """
    Build a context-aware payload from tasks + notes and ask the LLM to produce
    a 3-4 sentence strategic daily briefing.

    Key guarantees:
    - tasks_due_today is NEVER capped — every task due today is included.
    - upcoming_urgent is capped at 5 to keep the prompt manageable.
    - Each task carries a verb_hint derived from keyword-matching the title.
    - Tag density is pre-computed so the LLM doesn't have to count.
    """
    today_str = date.today().isoformat()  # "YYYY-MM-DD"

    active = [t for t in tasks if not t.get("completed")]

    # Separate today's tasks from the rest — never cap today's list
    tasks_due_today = [
        _build_task_entry(t) for t in active
        if str(t.get("due_date", ""))[:10] == today_str
    ]

    # Upcoming urgent tasks that are NOT due today, capped to avoid prompt bloat
    upcoming_urgent = [
        _build_task_entry(t) for t in active
        if t.get("urgent") and str(t.get("due_date", ""))[:10] != today_str
    ][:5]

    # Tag density across all active tasks
    all_tags = [tag for t in active for tag in [tg.get("name", "") for tg in (t.get("tags") or [])]]
    tag_counts = Counter(all_tags)
    top = tag_counts.most_common(1)
    tag_density = (
        {"dominant_tag": top[0][0], "count": top[0][1], "total_active_tasks": len(active)}
        if top else None
    )

    recent_notes = [
        {
            "title": n.get("title", ""),
            "note_content": _strip_html(n.get("content", ""))[:300],
            "tags": [tag.get("name", "") for tag in (n.get("tags") or [])],
            "updated_date": str(n.get("updated_date", ""))[:10] if n.get("updated_date") else None,
        }
        for n in notes
    ][:10]

    context = json.dumps(
        {
            "today": today_str,
            "tasks_due_today": tasks_due_today,    # uncapped — all must be mentioned
            "upcoming_urgent": upcoming_urgent,     # capped at 5
            "tag_density": tag_density,
            "recent_notes": recent_notes,
        },
        separators=(",", ":"),
    )

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _USER_PROMPT + context},
        ],
        temperature=0.4,
        max_tokens=500,
    )

    return (response.choices[0].message.content or "").strip()
