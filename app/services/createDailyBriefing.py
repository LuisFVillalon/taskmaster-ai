import json
import os
import re
from collections import Counter
from datetime import date, datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

_SYSTEM = (
    "You are a personal productivity coach delivering a spoken morning briefing. "
    "Your job is to narrate the user's day chronologically, weaving together scheduled work blocks, "
    "deadlines, and priorities into a clear, actionable story. "
    "\n\n"
    "Follow these rules exactly:\n"
    "1. OVERDUE — If 'tasks_overdue' is non-empty, open with an urgent callout naming each item.\n"
    "2. SCHEDULE — Use 'schedule_today' as the spine of the narrative. For each confirmed block say: "
    "\"You're scheduled to tackle '[Task]' from [start] to [end].\" "
    "For suggested (not yet accepted) blocks say: \"There's an AI-suggested slot for '[Task]' at [start].\"\n"
    "3. BREAKS — If a block has 'needs_break_before: true', weave in a break suggestion: "
    "\"After your previous session, maybe grab a coffee before diving into [Task]!\"\n"
    "4. UNSCHEDULED — For every task in 'unscheduled_today', flag it: "
    "\"'[Task]' is due today but has no scheduled block — find a slot before it slips.\"\n"
    "5. DEADLINES — Mention due-time tasks as \"due at [time]\"; date-only tasks as \"due tonight\".\n"
    "6. TONE — Use 'total_complexity_score' to set the mood: above 10 → warn it's a heavy day; "
    "below 5 → affirm it's manageable.\n"
    "7. NOTES — If 'relevant_notes' tags match a due or overdue task, cite that note by title as a reference.\n"
    "8. TAG IMBALANCE — If one tag dominates all active tasks, warn about the imbalance.\n"
    "\n"
    "Language register: imperative, warm, direct — like a knowledgeable friend, not a robot. "
    "Return plain text only — no JSON, no markdown, no bullet points. "
    "Hard limit: 4–6 sentences total."
)

_USER_PROMPT = (
    "Using the structured data below, write 4–6 sentences that narrate the user's day:\n"
    "• Open with overdue tasks if any exist.\n"
    "• Walk through 'schedule_today' in order — confirmed blocks get specific time ranges, "
    "suggested blocks get softer language.\n"
    "• After any block where 'needs_break_before' is true, suggest a brief break.\n"
    "• Call out every task in 'unscheduled_today' as needing a slot today.\n"
    "• Surface due-time deadlines from 'tasks_due_today'.\n"
    "• Close with a tone line driven by 'total_complexity_score'.\n\n"
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
    lower = title.lower()
    for keywords, verb in _VERB_RULES:
        if any(kw in lower for kw in keywords):
            return verb
    return "Complete"


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _priority_level(task: dict) -> str:
    if task.get("urgent"):
        return "high"
    complexity = task.get("complexity") or 0
    if complexity >= 4:
        return "medium-high"
    return "normal"


def _build_task_entry(t: dict) -> dict:
    return {
        "title": t.get("title", ""),
        "verb_hint": _get_verb_hint(t.get("title", "")),
        "priority_level": _priority_level(t),
        "urgent": t.get("urgent", False),
        "due_date": str(t.get("due_date", ""))[:10] if t.get("due_date") else None,
        "due_time": t.get("due_time") or None,
        "tags": [tag.get("name", "") for tag in (t.get("tags") or [])],
        "category": t.get("category"),
    }


def _format_12h(iso_str: str) -> str:
    """Convert an ISO 8601 datetime string to a friendly '1:00 PM' label."""
    try:
        dt = datetime.fromisoformat(iso_str)
        h, m = dt.hour, dt.minute
        period = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d} {period}" if m else f"{h12} {period}"
    except (ValueError, TypeError):
        return iso_str


def _build_schedule_today(
    work_blocks: list[dict],
    task_lookup: dict[int, dict],
    today_str: str,
) -> tuple[list[dict], set[int]]:
    """
    Build a chronologically sorted list of today's work-block entries enriched
    with human-readable times and a 'needs_break_before' flag.

    Returns:
        schedule_today  — list of block dicts ready for the LLM prompt
        scheduled_ids   — set of task_ids that have at least one block today
    """
    # Filter to today and parse start times for sorting
    todays: list[tuple[datetime, dict]] = []
    for wb in work_blocks:
        if wb.get("start_time", "")[:10] != today_str:
            continue
        try:
            start_dt = datetime.fromisoformat(wb["start_time"])
            todays.append((start_dt, wb))
        except (ValueError, KeyError):
            pass

    todays.sort(key=lambda x: x[0])

    schedule_today: list[dict] = []
    scheduled_ids: set[int] = set()
    prev_end_dt: datetime | None = None

    for start_dt, wb in todays:
        task_id = wb.get("task_id")
        task_info = task_lookup.get(task_id, {}) if task_id else {}

        try:
            end_dt = datetime.fromisoformat(wb["end_time"])
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
        except (ValueError, KeyError):
            end_dt = start_dt
            duration_minutes = 0

        # Flag back-to-back: previous block ended within 15 min of this one's start
        needs_break = (
            prev_end_dt is not None
            and 0 <= (start_dt - prev_end_dt).total_seconds() <= 900  # ≤15 min gap
        )

        schedule_today.append({
            "task_title": task_info.get("title", "AI Work Block"),
            "task_tags": task_info.get("tags", []),
            "start_time_label": _format_12h(wb["start_time"]),
            "end_time_label": _format_12h(wb["end_time"]),
            "duration_minutes": duration_minutes,
            "status": wb.get("status", "suggested"),   # "confirmed" | "suggested"
            "needs_break_before": needs_break,
        })

        if task_id:
            scheduled_ids.add(task_id)

        prev_end_dt = end_dt

    return schedule_today, scheduled_ids


async def create_daily_briefing(
    tasks: list[dict],
    notes: list[dict],
    work_blocks: list[dict] | None = None,
) -> str:
    """
    Build a context-aware payload from tasks, notes, and today's work blocks,
    then ask the LLM to produce a 4-6 sentence chronological daily briefing.

    Key guarantees:
    - tasks_due_today is NEVER capped — every task due today is included.
    - upcoming_urgent is capped at 5 to keep the prompt manageable.
    - schedule_today is sorted chronologically with break hints.
    - unscheduled_today = tasks due today with no work block whatsoever.
    """
    if work_blocks is None:
        work_blocks = []

    today_str = date.today().isoformat()  # "YYYY-MM-DD"
    active = [t for t in tasks if not t.get("completed")]

    # ── Task buckets ──────────────────────────────────────────────────────────

    tasks_due_today = [
        _build_task_entry(t) for t in active
        if str(t.get("due_date", ""))[:10] == today_str
    ]

    tasks_overdue = [
        _build_task_entry(t) for t in active
        if (d := str(t.get("due_date") or "")[:10]) and d < today_str
    ]

    upcoming_urgent = [
        _build_task_entry(t) for t in active
        if t.get("urgent") and str(t.get("due_date", ""))[:10] != today_str
    ][:5]

    total_complexity_score = sum(
        t.get("complexity") or 0 for t in active
        if str(t.get("due_date", ""))[:10] == today_str
    )

    # ── Tag stats ─────────────────────────────────────────────────────────────

    all_tags = [tag for t in active for tag in [tg.get("name", "") for tg in (t.get("tags") or [])]]
    tag_counts = Counter(all_tags)
    top = tag_counts.most_common(1)
    tag_density = (
        {"dominant_tag": top[0][0], "count": top[0][1], "total_active_tasks": len(active)}
        if top else None
    )

    # ── Relevant notes ────────────────────────────────────────────────────────

    priority_tags: set[str] = {
        tag_name
        for t in active
        if str(t.get("due_date", ""))[:10] <= today_str
        for tag_name in [tg.get("name", "") for tg in (t.get("tags") or [])]
    }
    tag_matched = [
        n for n in notes
        if priority_tags & {tg.get("name", "") for tg in (n.get("tags") or [])}
    ]
    note_pool = tag_matched if tag_matched else notes
    relevant_notes = [
        {
            "title": n.get("title", ""),
            "note_content": _strip_html(n.get("content", ""))[:300],
            "tags": [tag.get("name", "") for tag in (n.get("tags") or [])],
            "updated_date": str(n.get("updated_date", ""))[:10] if n.get("updated_date") else None,
        }
        for n in note_pool
    ][:10]

    # ── Work-block schedule ───────────────────────────────────────────────────
    # Build a task_id → {title, tags} lookup for enriching work block entries.
    task_lookup: dict[int, dict] = {
        t["id"]: {
            "title": t.get("title", ""),
            "tags": [tg.get("name", "") for tg in (t.get("tags") or [])],
        }
        for t in tasks
        if t.get("id") is not None
    }

    schedule_today, scheduled_task_ids = _build_schedule_today(
        work_blocks, task_lookup, today_str
    )

    # Tasks due today that have zero scheduled work blocks — flag as unscheduled.
    unscheduled_today = [
        entry for entry in tasks_due_today
        # Match by title since _build_task_entry dropped the id field.
        # Cross-reference against the original active list to get task ids.
        if not any(
            t.get("id") in scheduled_task_ids
            and str(t.get("due_date", ""))[:10] == today_str
            and t.get("title") == entry["title"]
            for t in active
        )
    ]

    # ── Assemble prompt payload ───────────────────────────────────────────────

    context = json.dumps(
        {
            "today": today_str,
            "schedule_today": schedule_today,         # chronological work blocks
            "unscheduled_today": unscheduled_today,   # due today, no block assigned
            "tasks_due_today": tasks_due_today,       # full list (includes scheduled)
            "tasks_overdue": tasks_overdue,
            "upcoming_urgent": upcoming_urgent,       # capped at 5
            "total_complexity_score": total_complexity_score,
            "tag_density": tag_density,
            "relevant_notes": relevant_notes,
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
        max_tokens=600,
    )

    return (response.choices[0].message.content or "").strip()
