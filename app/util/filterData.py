from collections import defaultdict
from datetime import date, timedelta
from fastapi import HTTPException
from app.schemas.task_schema import Task
from math import ceil
from typing import List, Dict, Any
import json


def build_active_workload_by_day(active_tasks: list[Task]) -> dict[str, float]:
    from collections import defaultdict

    by_day = defaultdict(float)

    for task in active_tasks:
        if task.completed:
            continue
        if not task.estimated_time:
            continue
        if not task.due_date:
            continue

        by_day[task.due_date.isoformat()] += float(task.estimated_time)

    return dict(by_day)


# ---------- Utility: split total hours into chunks that exactly sum ----------
def split_into_chunks(
    total_hours: float,
    target_size: float = 2.0,
    min_size: float = 1.0,
    max_size: float = 3.0,
    increment: float = 0.5,
) -> List[float]:
    """
    Return a list of durations (in hours) that sum exactly to total_hours.
    Uses target_size to compute a target count, then adjusts with increments to match total_hours.
    """
    # Defensive rounding helper to avoid float noise
    def round_inc(x):
        return round(round(x / increment) * increment, 10)

    # minimum and maximum checks
    if total_hours <= 0:
        return []

    # initial target count
    n = max(1, int(ceil(total_hours / target_size)))
    # adjust if total would require any chunk > max_size
    while n * max_size < total_hours:
        n += 1

    # Now distribute total_hours across n chunks in increments.
    base = total_hours / n
    # clamp base into [min_size, max_size] if possible; if not possible, adjust n
    if base > max_size:
        # need more chunks
        while base > max_size:
            n += 1
            base = total_hours / n
    elif base < min_size:
        # need fewer chunks (but do not go below 1)
        while n > 1 and (total_hours / (n - 1)) >= min_size:
            n -= 1
            base = total_hours / n

    # Start with even split, then make all values multiples of increment
    chunks = [round_inc(base) for _ in range(n)]
    current_sum = sum(chunks)
    diff = round_inc(total_hours - current_sum)

    # Adjust by +/- increments until diff is zero
    # If diff > 0, add increments to chunks not exceeding max_size.
    # If diff < 0, subtract increments from chunks not going below min_size.
    step = increment
    attempts = 0
    while abs(diff) >= 1e-9 and attempts < 10000:
        attempts += 1
        if diff > 0:
            # try to add to smallest chunks first
            chunks.sort()
            for i in range(len(chunks)):
                if chunks[i] + step <= max_size + 1e-9:
                    chunks[i] = round_inc(chunks[i] + step)
                    diff = round_inc(diff - step)
                    break
            else:
                # can't add, force increase n
                n += 1
                chunks.append(round_inc(min(max_size, step)))
                diff = round_inc(total_hours - sum(chunks))
        else:
            # diff < 0: subtract from largest chunks first
            chunks.sort(reverse=True)
            for i in range(len(chunks)):
                if chunks[i] - step >= min_size - 1e-9:
                    chunks[i] = round_inc(chunks[i] - step)
                    diff = round_inc(diff + step)
                    break
            else:
                # can't subtract further; try reducing n if possible
                if len(chunks) > 1:
                    # merge two smallest chunks
                    chunks.sort()
                    a = chunks.pop(0)
                    b = chunks.pop(0)
                    merged = round_inc(a + b)
                    # If merged exceeds max, split differently
                    if merged <= max_size + 1e-9:
                        chunks.append(merged)
                    else:
                        # push back and break to avoid infinite loop
                        chunks.extend([a, b])
                        break
                    diff = round_inc(total_hours - sum(chunks))
                else:
                    break

    # Final safety: ensure exact sum via final rounding of tiny float error
    total = round(sum(chunks), 10)
    if abs(total - round(total_hours, 10)) > 1e-6:
        # last resort adjust last element
        correction = round(total_hours - total, 10)
        chunks[-1] = round(round(chunks[-1] + correction, 10), 10)

    # sort for stable output (optional)
    return chunks

# ---------- Scheduler: map durations to dates respecting daily caps ----------
def schedule_durations(
    durations: List[float],
    start_date: date,
    buffer_end: date,
    active_workload_by_day: Dict[str, float],
    daily_cap: float = 6.0,
    due_time_str: str = "11:59:34.000Z",
) -> List[Dict[str, Any]]:
    """
    Spread durations evenly between start_date and buffer_end inclusive, then place each duration
    on the nearest day that can accept it without exceeding daily_cap (considering active_workload_by_day).
    """
    if not durations:
        return []

    n = len(durations)
    total_days = (buffer_end - start_date).days
    if total_days < 0:
        total_days = 0

    # generate N target indices evenly spaced between 0..total_days
    if n == 1:
        target_day_offsets = [0]
    else:
        # compute float positions then round to nearest integer day offsets
        step = total_days / max(1, (n - 1))
        target_day_offsets = [int(round(i * step)) for i in range(n)]

    # collapse offsets into actual dates and then attempt to place each duration
    scheduled = []
    # keep a mutable copy of workloads
    workloads = {k: float(v) for k, v in active_workload_by_day.items()}

    for i, dur in enumerate(durations):
        preferred_date = start_date + timedelta(days=target_day_offsets[i])
        # search forward (and optionally backward if forward out of range) up to buffer_end
        placed = False
        for forward_delta in range(0, (buffer_end - preferred_date).days + 1 if preferred_date <= buffer_end else 0):
            candidate = preferred_date + timedelta(days=forward_delta)
            key = candidate.isoformat()
            current = workloads.get(key, 0.0)
            if current + dur <= daily_cap + 1e-9:
                # place here
                workloads[key] = current + dur
                scheduled.append({"date": candidate, "duration": dur})
                placed = True
                break
        if not placed:
            # if we couldn't place between preferred_date and buffer_end, search all days from start_date to buffer_end
            candidate = start_date
            found = False
            while candidate <= buffer_end:
                key = candidate.isoformat()
                current = workloads.get(key, 0.0)
                if current + dur <= daily_cap + 1e-9:
                    workloads[key] = current + dur
                    scheduled.append({"date": candidate, "duration": dur})
                    found = True
                    break
                candidate += timedelta(days=1)
            if not found:
                # As a fallback: place on the last buffer_end day (may exceed cap)
                key = buffer_end.isoformat()
                workloads[key] = workloads.get(key, 0.0) + dur
                scheduled.append({"date": buffer_end, "duration": dur})

    # convert to required dicts with due_time
    result = []
    for idx, item in enumerate(scheduled, start=1):
        result.append({
            "title": f"Learn to recognize algorithm patterns - Part {idx}",
            "description": "Subtask to learn/recognize algorithm patterns (auto-generated).",
            "category": "skill",
            "due_date": item["date"].isoformat(),
            "due_time": due_time_str,
            "estimated_time": item["duration"],
            "complexity": 3,
            "tags": [],
        })
    return result