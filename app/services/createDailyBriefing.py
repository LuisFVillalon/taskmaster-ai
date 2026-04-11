"""
Daily Briefing service — Chief of Staff edition.

Data flow:
  tasks_router.py fetches (in parallel):
    • /get-tasks              → all user tasks
    • /work-blocks            → confirmed + suggested blocks
    • /google-calendar/events → today's calendar events (empty if not connected)
    • /get-notes              → notes for contextual cross-referencing

  This module synthesises the raw data into three pre-computed sets:
    • scheduled_tasks   — tasks that have a confirmed OR suggested work block today
    • at_risk_tasks     — tasks due within 48 h with NO work block at all
    • timeline          — calendar events + confirmed blocks sorted chronologically
                          with gap_minutes_to_next for tight-transition detection

  All timestamps are converted to the user's local timezone (passed as an IANA
  string from the frontend via X-Timezone header) before any label is generated.

  Output: dict { "pulse": str, "timeline": str, "action_items": list[str] }
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client     = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

# ── Fallback ──────────────────────────────────────────────────────────────────
_FALLBACK: dict = {
    "pulse":        "Could not generate briefing — please try refreshing.",
    "timeline":     "",
    "action_items": [],
}

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are an elite Chief of Staff delivering a morning intelligence briefing. "
    "Your purpose is to make the user feel prepared and in control — not overwhelmed.\n\n"

    "Return ONLY valid JSON with exactly these three keys:\n\n"

    '"pulse": A 2–3 sentence paragraph. '
    "Lead with 'overdue_tasks' if any — name them urgently by title. "
    "Then set the day's tone: reference 'confirmed_blocks', 'at_risk_count', and "
    "'complexity_score'. Is this a sprint or a manageable day?\n\n"

    '"timeline": A 3–5 sentence paragraph. '
    "Narrate the day chronologically using every item in the 'timeline' array. "
    "Use ONLY the 'start_label' and 'end_label' values provided — these are already "
    "in the user's local timezone; do not convert or adjust them. "
    "Connect adjacent items into concrete strategy: "
    "\"Because you have [Calendar Event] at [start_label], use your "
    "[Work Block] right after to make progress on [Deadline].\" "
    "If a work block has gap_minutes_to_next ≤ 15, or follows a calendar event "
    "with duration_minutes ≥ 60, suggest a brief break by name: "
    "\"After that [N]-hour session, grab a coffee before diving into [Task].\" "
    "If 'timeline' is empty, note the open schedule and urge the user to assign "
    "the at-risk tasks to a slot now.\n\n"

    '"action_items": An array of short imperative strings (≤ 15 words each). '
    "CRITICAL RULE: Only include tasks from 'at_risk_tasks' where 'is_scheduled' "
    "is false. A task with 'is_scheduled: true' ALREADY has a work block assigned "
    "(confirmed or pending) — NEVER create an action item for it. "
    "Pattern per entry: \"Schedule a block for \'[Task Title]\' — due [due_label].\" "
    "If 'at_risk_tasks' is empty OR all entries have is_scheduled true, return: "
    "[\"All critical tasks have blocks assigned — you\'re on track!\"]\n\n"

    "Global rules:\n"
    "• pulse and timeline are prose paragraphs — no bullet points, no markdown.\n"
    "• Only reference data explicitly provided — never invent events or deadlines.\n"
    "• Warm, direct, executive tone — knowledgeable friend, not a calendar app.\n"
    "• Return raw JSON only — no code fences, no extra keys."
)

_USER_PROMPT = "Synthesise the briefing from this structured data:\n\n"

# ── Verb-hint rules ───────────────────────────────────────────────────────────
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


# ── Timezone helpers ──────────────────────────────────────────────────────────

def _resolve_tz(name: str | None) -> ZoneInfo:
    """Resolve an IANA timezone name; silently fall back to UTC on any error."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


def _parse_aware(iso_str: str) -> datetime:
    """Parse ISO 8601 → timezone-aware datetime; treats naive strings as UTC."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_local(iso_str: str, tz: ZoneInfo) -> datetime:
    """Parse and convert to the user's local timezone."""
    return _parse_aware(iso_str).astimezone(tz)


def _local_date_key(iso_str: str, tz: ZoneInfo) -> str:
    """
    Return 'YYYY-MM-DD' in the user's local timezone.

    Critical: a work block stored as '2026-04-11T00:00:00+00:00' (midnight UTC)
    is '2026-04-10' for a Pacific Time user (UTC-7).  Using UTC [:10] slicing
    would assign it to the wrong day.
    """
    try:
        return _to_local(iso_str, tz).date().isoformat()
    except (ValueError, TypeError):
        return iso_str[:10]


def _format_12h(iso_str: str, tz: ZoneInfo) -> str:
    """
    Format an ISO 8601 timestamp as '9:00 AM' in the user's local timezone.

    Example: '2026-04-11T16:00:00+00:00' with tz=America/Los_Angeles
             → '9:00 AM'  (not '4:00 PM UTC')
    """
    try:
        dt = _to_local(iso_str, tz)
        h, m = dt.hour, dt.minute
        period = "AM" if h < 12 else "PM"
        h12    = h % 12 or 12
        return f"{h12}:{m:02d} {period}" if m else f"{h12} {period}"
    except (ValueError, TypeError):
        return iso_str


def _due_label(task: dict, today_str: str, tomorrow_str: str) -> str:
    """
    Return a human-readable due label ('9:00 AM today', 'tomorrow', '2026-04-15').

    due_time is stored as "HH:MM" in the user's local time — no conversion needed.
    """
    due_date = str(task.get("due_date") or "")[:10]
    due_time = task.get("due_time") or ""

    if due_date == today_str:
        if due_time:
            try:
                h, m   = int(due_time[:2]), int(due_time[3:5])
                period = "AM" if h < 12 else "PM"
                h12    = h % 12 or 12
                label  = f"{h12}:{m:02d} {period}" if m else f"{h12} {period}"
                return f"{label} today"
            except (ValueError, IndexError):
                pass
        return "tonight"

    if due_date == tomorrow_str:
        return "tomorrow"

    return due_date  # fall back to ISO date string


def _build_task_summary(
    t: dict,
    today_str: str,
    tomorrow_str: str,
    is_scheduled: bool,
) -> dict:
    return {
        "id":           t.get("id"),
        "title":        t.get("title", ""),
        "verb_hint":    _verb_hint(t.get("title", "")),
        "urgent":       t.get("urgent", False),
        "due_date":     str(t.get("due_date", ""))[:10] if t.get("due_date") else None,
        "due_time":     t.get("due_time") or None,
        "due_label":    _due_label(t, today_str, tomorrow_str),
        "tags":         [tg.get("name", "") for tg in (t.get("tags") or [])],
        "category":     t.get("category"),
        "complexity":   t.get("complexity") or 0,
        # Explicit flag: tells the LLM whether to produce an action item for this task.
        # True  = has a confirmed OR suggested work block → do NOT add to action_items.
        # False = no work block at all → this is genuinely at-risk.
        "is_scheduled": is_scheduled,
    }


# ── Core synthesis ────────────────────────────────────────────────────────────

def _synthesize(
    tasks:           list[dict],
    work_blocks:     list[dict],
    calendar_events: list[dict],
    notes:           list[dict],
    user_tz:         ZoneInfo,
) -> dict:
    """
    Pre-compute logical task sets and the day timeline.

    today_str / tomorrow_str are derived from the user's local clock (via user_tz)
    so that a PT user at 6 PM sees April 10, not the server's UTC date of April 11.
    """
    # ── Local date anchor ─────────────────────────────────────────────────────
    now_local      = datetime.now(user_tz)
    today_local    = now_local.date()
    today_str      = today_local.isoformat()
    tomorrow_str   = (today_local + timedelta(days=1)).isoformat()

    active = [t for t in tasks if not t.get("completed")]

    # ── Task sets ─────────────────────────────────────────────────────────────

    overdue_tasks = [
        _build_task_summary(t, today_str, tomorrow_str, False)
        for t in active
        if (d := str(t.get("due_date") or "")[:10]) and d < today_str
    ]

    due_soon = [
        t for t in active
        if str(t.get("due_date") or "")[:10] in (today_str, tomorrow_str)
    ]

    # ── Work-block sets ───────────────────────────────────────────────────────
    #
    # SCHEDULED = confirmed OR suggested blocks that fall on today in the user's
    # local timezone.  Both statuses mean a slot is already claimed — generating
    # an action item for such a task would be misleading and redundant.
    #
    # Key: use _local_date_key() to handle the UTC-midnight edge case where a
    # block stored as "T00:00Z" (midnight UTC) belongs to the previous local day
    # for users west of Greenwich.

    scheduled_today = [
        wb for wb in work_blocks
        if wb.get("status") in ("confirmed", "suggested")
        and _local_date_key(wb.get("start_time", ""), user_tz) == today_str
    ]
    scheduled_task_ids: set[int] = {
        wb["task_id"] for wb in scheduled_today if wb.get("task_id") is not None
    }

    # Timeline only shows confirmed blocks — suggested ones aren't committed.
    confirmed_today = [wb for wb in scheduled_today if wb.get("status") == "confirmed"]

    # ── Task-ID → full task lookup for enriching work block timeline entries ──
    task_by_id: dict[int, dict] = {
        t["id"]: t for t in tasks if t.get("id") is not None
    }

    scheduled_tasks = [
        _build_task_summary(t, today_str, tomorrow_str, is_scheduled=True)
        for t in due_soon
        if t.get("id") in scheduled_task_ids
    ]
    at_risk_tasks = [
        _build_task_summary(t, today_str, tomorrow_str, is_scheduled=False)
        for t in due_soon
        if t.get("id") not in scheduled_task_ids
    ]

    # ── Timeline ──────────────────────────────────────────────────────────────
    # Merge today's non-all-day calendar events with confirmed work blocks.
    # All time labels are converted to the user's local timezone before storage
    # so the LLM receives pre-localised strings and must not adjust them.

    raw_items: list[tuple[datetime, dict]] = []

    for ev in calendar_events:
        if ev.get("is_all_day"):
            continue
        start_str = ev.get("start", "")
        if _local_date_key(start_str, user_tz) != today_str:
            continue
        try:
            s_dt = _parse_aware(start_str)
            e_dt = _parse_aware(ev["end"]) if ev.get("end") else s_dt
            raw_items.append((s_dt, {
                "type":             "calendar_event",
                "title":            ev.get("title", ""),
                "start_label":      _format_12h(start_str, user_tz),
                "end_label":        _format_12h(ev["end"], user_tz) if ev.get("end") else "",
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
            s_dt = _parse_aware(wb["start_time"])
            e_dt = _parse_aware(wb["end_time"])
            raw_items.append((s_dt, {
                "type":             "work_block",
                "title":            task_title,
                "task_tags":        task_tags,
                "start_label":      _format_12h(wb["start_time"], user_tz),
                "end_label":        _format_12h(wb["end_time"], user_tz),
                "duration_minutes": int((e_dt - s_dt).total_seconds() / 60),
                "ai_reasoning":     wb.get("ai_reasoning", ""),
                "_end_dt":          e_dt,
            }))
        except (ValueError, KeyError):
            pass

    # Sort by UTC start time (order is the same in any timezone)
    raw_items.sort(key=lambda x: x[0])

    # Annotate gap_minutes_to_next for the LLM's break-suggestion logic
    timeline: list[dict] = []
    for i, (_, item) in enumerate(raw_items):
        if i + 1 < len(raw_items):
            gap = int((raw_items[i + 1][0] - item["_end_dt"]).total_seconds() / 60)
            item["gap_minutes_to_next"] = max(gap, 0)
        else:
            item["gap_minutes_to_next"] = None
        item.pop("_end_dt", None)
        timeline.append(item)

    # ── Relevant notes ────────────────────────────────────────────────────────

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
            "title":   n.get("title", ""),
            "snippet": _strip_html(n.get("content", ""))[:200],
            "tags":    [tg.get("name", "") for tg in (n.get("tags") or [])],
        }
        for n in note_pool
    ][:6]

    # ── Complexity & tag density ──────────────────────────────────────────────

    complexity_score = sum(
        t.get("complexity") or 0 for t in active
        if str(t.get("due_date", ""))[:10] == today_str
    )

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
        "timezone":         str(user_tz),           # e.g. "America/Los_Angeles"
        # Scalar signals for the LLM
        "confirmed_blocks": len(confirmed_today),
        "at_risk_count":    len(at_risk_tasks),
        "complexity_score": complexity_score,
        "has_calendar":     bool(calendar_events),
        # Task sets (each entry carries is_scheduled so the LLM can verify)
        "overdue_tasks":    overdue_tasks,
        "scheduled_tasks":  scheduled_tasks,
        "at_risk_tasks":    at_risk_tasks,
        # Narrative spine
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
    timezone_name:   str = "UTC",
) -> dict:
    """
    Synthesise tasks, work blocks, calendar events, and notes into a structured
    briefing: { "pulse": str, "timeline": str, "action_items": list[str] }.

    timezone_name: IANA timezone string forwarded from the browser via the
                   X-Timezone header (e.g. "America/Los_Angeles").  Defaults to
                   UTC so the service is still usable without the header.
    """
    user_tz = _resolve_tz(timezone_name)
    context = json.dumps(
        _synthesize(tasks, work_blocks, calendar_events, notes, user_tz),
        separators=(",", ":"),
    )

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": _USER_PROMPT + context},
        ],
        response_format={"type": "json_object"},
        temperature=0.35,
        max_tokens=700,
    )

    raw = (response.choices[0].message.content or "").strip()

    try:
        result = json.loads(raw)
        return {
            "pulse":        str(result.get("pulse", "")),
            "timeline":     str(result.get("timeline", "")),
            "action_items": list(result.get("action_items", [])),
        }
    except (json.JSONDecodeError, TypeError):
        return _FALLBACK.copy()
