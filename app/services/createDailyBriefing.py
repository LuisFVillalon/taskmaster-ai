"""
Daily Briefing service — Chief of Staff edition.

Data flow:
  tasks_router.py fetches (in parallel):
    • /get-tasks            → all user tasks
    • /work-blocks          → confirmed + suggested blocks
    • /google-calendar/events → today's calendar events (empty if not connected)
    • /get-notes            → notes for contextual cross-referencing

  This module synthesises the raw data into three pre-computed sets:
    • scheduled_tasks   — tasks that have a confirmed work block today
    • at_risk_tasks     — tasks due within 48 h with NO confirmed block
    • timeline          — chronologically sorted calendar events + confirmed blocks
                          with 'gap_minutes_to_next' so the LLM can spot tight transitions

  Output: dict { "pulse": str, "timeline": str, "action_items": list[str] }
"""

import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client    = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

# ── Fallback response returned when JSON parsing fails ────────────────────────
_FALLBACK: dict = {
    "pulse": "Could not generate briefing — please try refreshing.",
    "timeline": "",
    "action_items": [],
}

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are an elite Chief of Staff delivering a morning intelligence briefing. "
    "Your purpose is to make the user feel prepared and in control — not overwhelmed by a list.\n\n"

    "Return ONLY valid JSON with exactly these three keys:\n\n"

    '"pulse": A 2–3 sentence paragraph. '
    "Lead with overdue tasks if any exist — name them urgently. "
    "Then assess overall day intensity: reference 'confirmed_blocks', 'at_risk_count', and "
    "'complexity_score'. Set the emotional tone — is today a sprint or manageable?\n\n"

    '"timeline": A 3–5 sentence paragraph. '
    "Narrate the day's sequence chronologically from the 'timeline' array. "
    "Connect adjacent items into concrete strategy: "
    "\"Because you have [Calendar Event] at [start_label], I recommend using your "
    "[Work Block] right after to ensure you meet [Deadline].\" "
    "If a work block's 'gap_minutes_to_next' is ≤ 15, or it immediately follows a "
    "calendar event with duration_minutes ≥ 60, suggest a brief break: "
    "\"After that [N]-hour [meeting/session], grab a coffee before diving into [Task].\" "
    "Reference specific task titles, times, and ai_reasoning from the data. "
    "If 'timeline' is empty, note the open day as an opportunity and urge "
    "the user to schedule the at-risk tasks now.\n\n"

    '"action_items": An array of short imperative strings (≤ 15 words each), '
    "one per entry in 'at_risk_tasks'. "
    "Pattern: \"Schedule a block for \'[Task Title]\' — due [due_label].\" "
    "If 'at_risk_tasks' is empty, return: "
    "[\"All critical tasks have confirmed blocks — you\'re on track!\"]\n\n"

    "Strict rules:\n"
    "• pulse and timeline are prose — no bullet points, no markdown inside them.\n"
    "• Only reference data explicitly provided — never invent events, tasks, or deadlines.\n"
    "• Warm, direct, executive tone — knowledgeable friend, not a calendar app.\n"
    "• Return raw JSON only — no code fences, no extra keys."
)

_USER_PROMPT = "Synthesise the briefing from this data:\n\n"

# ── Verb-hint rules (imperative action verbs) ─────────────────────────────────
_VERB_RULES: list[tuple[list[str], str]] = [
    (["midterm", "final exam", "exam", "quiz", "test prep"], "Study for"),
    (["meeting", "sync", "standup", "stand-up", "call", "1:1", "interview"], "Attend"),
    (["report", "draft", "essay", "paper", "proposal", "write"], "Finalize"),
    (["gym", "workout", "work out", "run", "exercise", "training", "lift"], "Complete session for"),
    (["review", "read", "audit"], "Review"),
    (["deploy", "release", "ship", "launch"], "Deploy"),
    (["debug", "fix", "patch", "resolve", "bug"], "Fix"),
    (["presentation", "present", "slides", "demo"], "Prepare presentation for"),
    (["submit", "turn in", "upload", "hand in"], "Submit"),
    (["project", "build", "implement", "develop", "sprint"], "Work on"),
]


def _verb_hint(title: str) -> str:
    lower = title.lower()
    for keywords, verb in _VERB_RULES:
        if any(kw in lower for kw in keywords):
            return verb
    return "Complete"


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _format_12h(iso_str: str) -> str:
    """'2026-04-10T14:00:00+00:00'  →  '2:00 PM'"""
    try:
        dt = datetime.fromisoformat(iso_str)
        h, m = dt.hour, dt.minute
        period = "AM" if h < 12 else "PM"
        h12    = h % 12 or 12
        return f"{h12}:{m:02d} {period}" if m else f"{h12} {period}"
    except (ValueError, TypeError):
        return iso_str


def _due_label(task: dict) -> str:
    """Human-readable due label: '3:00 PM today', 'tomorrow', 'Apr 10', etc."""
    due_date = str(task.get("due_date") or "")[:10]
    due_time = task.get("due_time")
    today_str    = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    if due_date == today_str:
        if due_time:
            return f"{_format_12h(due_time[:5])} today"  # "HH:MM" → 12h label
        return "tonight"
    if due_date == tomorrow_str:
        return "tomorrow"
    return due_date  # fall back to ISO date


def _build_task_summary(t: dict) -> dict:
    return {
        "id":          t.get("id"),
        "title":       t.get("title", ""),
        "verb_hint":   _verb_hint(t.get("title", "")),
        "urgent":      t.get("urgent", False),
        "due_date":    str(t.get("due_date", ""))[:10] if t.get("due_date") else None,
        "due_time":    t.get("due_time") or None,
        "due_label":   _due_label(t),
        "tags":        [tg.get("name", "") for tg in (t.get("tags") or [])],
        "category":    t.get("category"),
        "complexity":  t.get("complexity") or 0,
    }


# ── Core synthesis ────────────────────────────────────────────────────────────

def _synthesize(
    tasks:           list[dict],
    work_blocks:     list[dict],
    calendar_events: list[dict],
    notes:           list[dict],
    today_str:       str,
    tomorrow_str:    str,
) -> dict:
    """
    Pre-compute the three logical sets and build the day timeline so the LLM
    receives structured facts rather than raw API dumps.
    """
    active = [t for t in tasks if not t.get("completed")]

    # ── Task sets ─────────────────────────────────────────────────────────────

    overdue_tasks = [
        _build_task_summary(t) for t in active
        if (d := str(t.get("due_date") or "")[:10]) and d < today_str
    ]

    # Tasks due in the next 48 h (today + tomorrow)
    due_soon = [
        t for t in active
        if str(t.get("due_date") or "")[:10] in (today_str, tomorrow_str)
    ]

    # Confirmed work blocks for today
    confirmed_today = [
        wb for wb in work_blocks
        if wb.get("status") == "confirmed"
        and wb.get("start_time", "")[:10] == today_str
    ]
    confirmed_task_ids: set[int] = {
        wb["task_id"] for wb in confirmed_today if wb.get("task_id") is not None
    }

    # Build task-id → title lookup (for enriching work block timeline entries)
    task_by_id: dict[int, dict] = {
        t["id"]: t for t in tasks if t.get("id") is not None
    }

    scheduled_tasks = [
        _build_task_summary(t) for t in due_soon
        if t.get("id") in confirmed_task_ids
    ]
    at_risk_tasks = [
        _build_task_summary(t) for t in due_soon
        if t.get("id") not in confirmed_task_ids
    ]

    # ── Timeline ──────────────────────────────────────────────────────────────
    # Merge today's calendar events (non-all-day) with confirmed work blocks.

    raw_items: list[tuple[datetime, dict]] = []

    for ev in calendar_events:
        if ev.get("is_all_day"):
            continue
        start_str = ev.get("start", "")
        if start_str[:10] != today_str:
            continue
        try:
            s_dt = datetime.fromisoformat(start_str)
            e_dt = datetime.fromisoformat(ev["end"]) if ev.get("end") else s_dt
            raw_items.append((s_dt, {
                "type":             "calendar_event",
                "title":            ev.get("title", ""),
                "start_label":      _format_12h(start_str),
                "end_label":        _format_12h(ev.get("end", "")),
                "duration_minutes": int((e_dt - s_dt).total_seconds() / 60),
                "_end_dt":          e_dt,
            }))
        except (ValueError, KeyError):
            pass

    for wb in confirmed_today:
        task_info  = task_by_id.get(wb.get("task_id"))
        task_title = task_info["title"] if task_info else "Work Block"
        task_tags  = [tg.get("name", "") for tg in (task_info.get("tags") or [])] if task_info else []
        try:
            s_dt = datetime.fromisoformat(wb["start_time"])
            e_dt = datetime.fromisoformat(wb["end_time"])
            raw_items.append((s_dt, {
                "type":             "work_block",
                "title":            task_title,
                "task_tags":        task_tags,
                "start_label":      _format_12h(wb["start_time"]),
                "end_label":        _format_12h(wb["end_time"]),
                "duration_minutes": int((e_dt - s_dt).total_seconds() / 60),
                "ai_reasoning":     wb.get("ai_reasoning", ""),
                "_end_dt":          e_dt,
            }))
        except (ValueError, KeyError):
            pass

    raw_items.sort(key=lambda x: x[0])

    # Annotate gap_minutes_to_next so the LLM can spot tight transitions
    timeline: list[dict] = []
    for i, (_, item) in enumerate(raw_items):
        if i + 1 < len(raw_items):
            next_start = raw_items[i + 1][0]
            gap = int((next_start - item["_end_dt"]).total_seconds() / 60)
            item["gap_minutes_to_next"] = max(gap, 0)
        else:
            item["gap_minutes_to_next"] = None
        # Remove internal datetime before JSON serialisation
        item.pop("_end_dt", None)
        timeline.append(item)

    # ── Relevant notes (tag-matched to due/overdue tasks) ─────────────────────

    priority_tags: set[str] = {
        tg.get("name", "")
        for t in active
        if str(t.get("due_date", ""))[:10] <= today_str
        for tg in (t.get("tags") or [])
    }
    tag_matched = [
        n for n in notes
        if priority_tags & {tg.get("name", "") for tg in (n.get("tags") or [])}
    ]
    note_pool = tag_matched if tag_matched else notes
    relevant_notes = [
        {
            "title":        n.get("title", ""),
            "snippet":      _strip_html(n.get("content", ""))[:200],
            "tags":         [tg.get("name", "") for tg in (n.get("tags") or [])],
        }
        for n in note_pool
    ][:6]

    # ── Complexity score (today's tasks) ─────────────────────────────────────

    complexity_score = sum(
        t.get("complexity") or 0 for t in active
        if str(t.get("due_date", ""))[:10] == today_str
    )

    # ── Tag density ───────────────────────────────────────────────────────────

    all_tags   = [tg for t in active for tg in [x.get("name", "") for x in (t.get("tags") or [])]]
    tag_counts = Counter(all_tags)
    top        = tag_counts.most_common(1)
    tag_density = (
        {"dominant_tag": top[0][0], "count": top[0][1], "total_active": len(active)}
        if top else None
    )

    return {
        "today":            today_str,
        "tomorrow":         tomorrow_str,
        # Scalars the LLM can reason about without counting arrays
        "confirmed_blocks": len(confirmed_today),
        "at_risk_count":    len(at_risk_tasks),
        "complexity_score": complexity_score,
        "has_calendar":     bool(calendar_events),  # tells LLM if events are real
        # Task sets
        "overdue_tasks":    overdue_tasks,
        "scheduled_tasks":  scheduled_tasks,
        "at_risk_tasks":    at_risk_tasks,
        # Day narrative spine
        "timeline":         timeline,
        # Supporting context
        "relevant_notes":   relevant_notes,
        "tag_density":      tag_density,
    }


# ── Public entry point ────────────────────────────────────────────────────────

async def create_daily_briefing(
    tasks:           list[dict],
    work_blocks:     list[dict],
    calendar_events: list[dict],
    notes:           list[dict],
) -> dict:
    """
    Synthesise tasks, work blocks, calendar events, and notes into a structured
    briefing dict: { "pulse": str, "timeline": str, "action_items": list[str] }.

    The caller (tasks_router.py) is responsible for fetching all four data sources
    from the backend before invoking this function.
    """
    today_str    = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

    context = json.dumps(
        _synthesize(tasks, work_blocks, calendar_events, notes, today_str, tomorrow_str),
        separators=(",", ":"),
    )

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system",  "content": _SYSTEM},
            {"role": "user",    "content": _USER_PROMPT + context},
        ],
        response_format={"type": "json_object"},   # enforce valid JSON output
        temperature=0.35,
        max_tokens=700,
    )

    raw = (response.choices[0].message.content or "").strip()

    try:
        result = json.loads(raw)
        # Normalise: ensure all three keys are present
        return {
            "pulse":        str(result.get("pulse", "")),
            "timeline":     str(result.get("timeline", "")),
            "action_items": list(result.get("action_items", [])),
        }
    except (json.JSONDecodeError, TypeError):
        return _FALLBACK.copy()
