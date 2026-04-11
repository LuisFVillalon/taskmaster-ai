"""
Smart Scheduling — hybrid gap-finder + LLM reasoning.

Phase 1 (Python, deterministic)
────────────────────────────────
find_candidate_slots() receives the user's busy periods, the scheduling
window (now → deadline), and the minimum duration needed.  It:
  1. Merges overlapping busy intervals.
  2. Inverts them to produce free intervals within the window.
  3. Clips each free interval to working hours (08:00–22:00 UTC).
  4. Filters out any sub-slot shorter than the required duration.
  5. Scores each candidate on three axes:
       • time-of-day  — morning preference (deep-focus window)
       • buffer       — more time before deadline is safer
       • slot size    — larger margin allows rescheduling
  6. Returns the top-5 candidates (best first).

Phase 2 (LLM, contextual)
───────────────────────────
pick_best_slot() sends the scored candidates + task context to
gpt-4o-mini and asks it to choose the single best option.
The model reasons about the *nature* of the work (tags, title) —
it never touches raw timestamps or does date arithmetic itself.
The response is a strict JSON object: {slot_index, reasoning, confidence}.

The AI is the reasoning layer; Python is the arithmetic layer.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_MODEL = os.getenv("OPENAI_MODEL")

# Primary working-hour window (UTC).  When user timezone support is added,
# these should be converted to the user's local hours before clipping.
_WORK_START = 8   # 08:00
_WORK_END   = 22  # 22:00

# Extended window — used as a graceful-degradation fallback when no slot
# fits inside the primary window (e.g. the day is already heavily booked).
_WORK_START_EXTENDED = 7   # 07:00
_WORK_END_EXTENDED   = 23  # 23:00

# Fallback hours when estimated_time is null and complexity is unknown.
_COMPLEXITY_HOURS: dict[int, float] = {1: 0.5, 2: 1.0, 3: 2.0, 4: 3.5, 5: 5.0}
_DEFAULT_HOURS = 2.0

_SYSTEM = (
    "You are a precise scheduling assistant. "
    "You receive a task description and a numbered list of pre-computed free slots. "
    "These slots already account for the user's Google Calendar events AND their "
    "personal recurring blackout windows (e.g. gym sessions, classes, focus blocks). "
    "Your job is to pick the single best slot — consider the nature of the work, "
    "the required time, and the urgency relative to the deadline. "
    "If every available slot falls at a suboptimal time (e.g. late evening) because "
    "the user's preferred hours were blocked by their own availability settings, "
    "explicitly note this trade-off in your reasoning so the user understands why. "
    "Return ONLY valid JSON with no markdown fences. "
    'Schema: {"slot_index": integer, "reasoning": string, "confidence": float}'
)


# ── Phase 1: gap-finder ───────────────────────────────────────────────────────

def _merge_busy(
    periods: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Sort and merge overlapping/adjacent busy intervals."""
    if not periods:
        return []
    sorted_p = sorted(periods, key=lambda x: x[0])
    merged: list[list[datetime]] = [list(sorted_p[0])]
    for start, end in sorted_p[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(m[0], m[1]) for m in merged]


def _working_bounds(day: datetime) -> tuple[datetime, datetime]:
    """Return (work_start, work_end) for the calendar day of `day` (UTC)."""
    base = day.replace(minute=0, second=0, microsecond=0)
    return (
        base.replace(hour=_WORK_START),
        base.replace(hour=_WORK_END),
    )


def find_candidate_slots(
    busy_periods: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    min_hours: float,
    max_candidates: int = 5,
    work_start: int = _WORK_START,
    work_end: int = _WORK_END,
) -> list[dict]:
    """
    Return up to `max_candidates` free slots within [window_start, window_end]
    that are within working hours and at least `min_hours` long, scored and
    sorted best-first.

    `work_start` / `work_end` (UTC hours, 0–23) control the working-hour clip.
    Pass `_WORK_START_EXTENDED` / `_WORK_END_EXTENDED` to use the wider window.

    Returns an empty list when no qualifying slot exists (caller handles this
    as 'no_capacity' and surfaces it to the user).
    """
    merged_busy = _merge_busy(busy_periods)

    # ── Step 1: invert busy periods to free intervals ─────────────────────
    free_intervals: list[tuple[datetime, datetime]] = []
    cursor = window_start

    for busy_start, busy_end in merged_busy:
        if cursor >= window_end:
            break
        if busy_start > cursor:
            free_intervals.append((cursor, min(busy_start, window_end)))
        cursor = max(cursor, busy_end)

    if cursor < window_end:
        free_intervals.append((cursor, window_end))

    # ── Step 2: clip to working hours, day by day ─────────────────────────
    clipped: list[dict] = []
    for free_start, free_end in free_intervals:
        # Iterate over each calendar day the interval spans.
        day = free_start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day.date() <= free_end.date():
            w_start = day.replace(hour=work_start, minute=0, second=0, microsecond=0)
            w_end   = day.replace(hour=work_end,   minute=0, second=0, microsecond=0)
            sub_start = max(free_start, w_start)
            sub_end   = min(free_end,   w_end)
            duration_h = (sub_end - sub_start).total_seconds() / 3600
            if sub_start < sub_end and duration_h >= min_hours:
                clipped.append({
                    "start":          sub_start,
                    "end":            sub_end,
                    "duration_hours": round(duration_h, 2),
                })
            day += timedelta(days=1)

    if not clipped:
        return []

    # ── Step 3: score each candidate ─────────────────────────────────────
    total_window_h = max(
        (window_end - window_start).total_seconds() / 3600, 1.0
    )

    def _score(slot: dict) -> float:
        h = slot["start"].hour
        # Morning (8–12) → deep focus premium
        time_score = 2.0 if 8 <= h < 12 else (1.0 if h < 17 else 0.0)
        # Buffer before deadline — normalised to [0, 2]
        hours_left = (window_end - slot["start"]).total_seconds() / 3600
        buffer_score = min(hours_left / total_window_h, 1.0) * 2.0
        # Slot generosity — more room = easier to reschedule if needed
        size_score = min(slot["duration_hours"] / 8.0, 1.0)
        return time_score + buffer_score + size_score

    scored = sorted(clipped, key=_score, reverse=True)
    return scored[:max_candidates]


# ── Phase 2: LLM picks the best slot ─────────────────────────────────────────

async def pick_best_slot(
    task_title: str,
    task_tags: list[str],
    estimated_hours: float,
    due_date_str: str,
    candidates: list[dict],
    constraint_summary: str = "",
) -> dict:
    """
    Ask the model to choose from pre-validated candidates.
    The model sees human-readable slot labels; it never manipulates timestamps.
    `constraint_summary` describes active blackout windows so the LLM can name
    the specific constraint when reasoning about a suboptimal slot.
    Returns {"slot": <chosen candidate dict>, "reasoning": str, "confidence": float}.
    """
    slots_text = "\n".join(
        f"  [{i}] {s['start'].strftime('%A %b %d, %I:%M %p')} – "
        f"{s['end'].strftime('%I:%M %p')}  ({s['duration_hours']:.1f} h free)"
        for i, s in enumerate(candidates)
    )

    constraint_line = (
        f"Active availability constraints: {constraint_summary}\n"
        if constraint_summary
        else ""
    )

    prompt = (
        f'Task: "{task_title}"\n'
        f"Tags: {', '.join(task_tags) if task_tags else 'none'}\n"
        f"Estimated work time: {estimated_hours:.1f} h\n"
        f"Deadline: {due_date_str}\n"
        f"{constraint_line}"
        f"\nAvailable slots:\n{slots_text}\n\n"
        "Choose the single best slot for this work. "
        "Reason about the task's nature (from title and tags) and the time needed. "
        "If the best slot is suboptimal (e.g. late evening) because earlier times "
        "were blocked by the listed availability constraints, name the constraint "
        "explicitly in your reasoning so the user understands why. "
        "Return slot_index (0-based integer), a one-sentence reasoning, "
        "and a confidence score between 0.0 and 1.0."
    )

    response = await _ai.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,         # low temperature for reproducible slot selection
        max_tokens=150,
        response_format={"type": "json_object"},
    )

    raw = (response.choices[0].message.content or "{}").strip()
    data = json.loads(raw)

    # Clamp to valid range — guard against any hallucinated index
    idx = max(0, min(int(data.get("slot_index", 0)), len(candidates) - 1))

    return {
        "slot":       candidates[idx],
        "reasoning":  str(data.get("reasoning", "Best available slot for this task.")),
        "confidence": float(data.get("confidence", 0.7)),
    }


# ── Main entry point ──────────────────────────────────────────────────────────

async def schedule_task(
    task_id: int,
    title: str,
    due_date_str: str,
    estimated_hours: float | None,
    complexity: int | None,
    tags: list[str],
    calendar_events: list[dict],
    preference_busy: list[tuple[datetime, datetime]] | None = None,
    constraint_summary: str = "",
) -> dict:
    """
    Orchestrate both phases and return a dict ready to POST to /work-blocks.

    `calendar_events` is the raw list from GET /google-calendar/events.
    All-day events are skipped because they don't represent timed blocks.

    Raises ValueError("no_capacity") when no qualifying slot exists so the
    router can surface a structured 422 to the frontend.
    """
    now = datetime.now(tz=timezone.utc)

    # ── Parse deadline ────────────────────────────────────────────────────
    try:
        if "T" in due_date_str:
            deadline = datetime.fromisoformat(due_date_str)
        else:
            # Date-only string — treat as end-of-day UTC
            deadline = datetime.fromisoformat(f"{due_date_str}T23:59:00+00:00")
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"Cannot parse due_date: {due_date_str!r}")

    if deadline <= now:
        raise ValueError("Task deadline is in the past — cannot schedule.")

    # ── Determine required hours ──────────────────────────────────────────
    hours = (
        estimated_hours
        or _COMPLEXITY_HOURS.get(complexity or 0)
        or _DEFAULT_HOURS
    )

    # ── Build busy periods from calendar events ────────────────────────────
    # Only time-specific events (not all-day) count as busy.
    busy: list[tuple[datetime, datetime]] = []
    for ev in calendar_events:
        if ev.get("is_all_day"):
            continue
        start_str = ev.get("start", "")
        end_str   = ev.get("end",   "")
        if not start_str or "T" not in start_str:
            continue
        try:
            ev_start = datetime.fromisoformat(start_str)
            ev_end   = datetime.fromisoformat(end_str)
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=timezone.utc)
            if ev_end.tzinfo is None:
                ev_end = ev_end.replace(tzinfo=timezone.utc)
            busy.append((ev_start, ev_end))
        except ValueError:
            continue  # skip malformed events

    # ── Merge preference blackouts into busy list ─────────────────────────
    if preference_busy:
        busy.extend(preference_busy)

    # ── Phase 1: primary working hours (08:00–22:00 UTC) ─────────────────
    candidates = find_candidate_slots(busy, now, deadline, hours)
    partial = False

    # ── Phase 2: graceful degradation — try extended hours (07:00–23:00) ─
    # Triggered when the primary window is fully booked (e.g. two tasks
    # already claimed most of the focus window for this deadline date).
    if not candidates:
        candidates = find_candidate_slots(
            busy, now, deadline, hours,
            work_start=_WORK_START_EXTENDED,
            work_end=_WORK_END_EXTENDED,
        )

    # ── Phase 3: partial fit — best available gap even if shorter ─────────
    # Triggered when no gap ≥ min_hours exists anywhere in the window.
    # Rather than failing, surface the largest available slot so the user
    # can see when to start and know the task needs to be split.
    if not candidates:
        any_gap = find_candidate_slots(
            busy, now, deadline, min_hours=0.5,
            work_start=_WORK_START_EXTENDED,
            work_end=_WORK_END_EXTENDED,
        )
        if any_gap:
            # find_candidate_slots sorts best-first; [0] is the highest-scored gap.
            candidates = [any_gap[0]]
            partial = True
        else:
            raise ValueError("no_available_slots")

    # ── LLM slot selection ────────────────────────────────────────────────
    result = await pick_best_slot(
        title, tags, hours, due_date_str, candidates, constraint_summary
    )

    chosen = result["slot"]

    # Work block end = start + task's required hours (capped to the gap end).
    # Previously this was set to `chosen["end"]` (the full free-gap boundary),
    # which caused every scheduled block to consume the rest of the day as
    # busy time — preventing subsequent tasks from finding any open slots.
    slot_hours    = min(hours, chosen["duration_hours"])
    work_end_time = chosen["start"] + timedelta(hours=slot_hours)

    reasoning = result["reasoning"]
    if partial:
        reasoning = (
            f"⚠ Partial fit: only {chosen['duration_hours']:.1f} h available "
            f"({hours:.1f} h needed) — consider splitting this task across "
            f"multiple sessions. {reasoning}"
        )

    return {
        "task_id":      task_id,
        "start_time":   chosen["start"].isoformat(),
        "end_time":     work_end_time.isoformat(),
        "ai_reasoning": reasoning,
        "confidence":   result["confidence"],
        "status":       "suggested",
    }
