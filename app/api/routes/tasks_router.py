import asyncio
from datetime import datetime, timedelta, timezone, date

from fastapi import APIRouter, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from typing import Any, Optional
import httpx
import os
from dotenv import load_dotenv

from app.schemas.task_schema import Task, TaskCreate
from app.services.createAITaskPlan import create_subtasks_with_llm
from app.services.createDailyBriefing import create_daily_briefing
from app.services.getLearningResources import get_learning_resources
from app.services.scheduleTask import schedule_task

load_dotenv()
router = APIRouter()

BACKEND_URL = os.getenv("TASKMASTER_BACKEND_URL")

if not BACKEND_URL:
    raise RuntimeError("TASKMASTER_BACKEND_URL environment variable is not set")


@router.post("/plan-tasks")
async def plan_tasks(new_task: TaskCreate):

    # ── 1. Fetch active tasks ──────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(
        connect=10.0,
        read=300.0,   # 5 min read timeout
        write=10.0,
        pool=10.0
    )) as client:
            active_response = await client.get(f"{BACKEND_URL}/get-tasks")
            if active_response.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to fetch tasks")
            active_tasks = [Task(**task) for task in active_response.json()]

            # ── 2. Create parent task ──────────────────────────────────────
            create_response = await client.post(
                f"{BACKEND_URL}/create-task",
                json=jsonable_encoder(new_task)
            )
            if create_response.status_code != 200:
                print("Backend error:", create_response.text)
                raise HTTPException(status_code=502, detail=create_response.text)

            created_task = create_response.json()

    except httpx.RequestError as e:
        print("Backend connection error:", str(e))
        raise HTTPException(status_code=502, detail="Backend service unavailable")

    # ── 3. Generate subtasks via LLM (outside the backend client block) ───
    try:
        result = await create_subtasks_with_llm(
            active_tasks=active_tasks,
            new_task=new_task,
            created_task=created_task,
        )
    except HTTPException:
        raise
    except Exception as e:
        print("LLM error type:", type(e).__name__)
        print("LLM error message:", str(e))
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"LLM service error: {str(e)}")

    # ── 4. Persist subtasks ────────────────────────────────────────────────
    # for subtask in result["subtasks"]:
    #     async with httpx.AsyncClient(timeout=20.0) as client:
    #         await client.post(f"{BACKEND_URL}/create-task", json=subtask)

    return {
        "new_task": jsonable_encoder(created_task),
        "subtasks": result["subtasks"],
        "overload_warning": result["overload_warning"],  # None or a human-readable string
    }


# ── Daily Briefing ────────────────────────────────────────────────────────────
# The service now fetches all three data sources itself (tasks, work-blocks,
# calendar events, notes) using the forwarded Supabase JWT.  This mirrors the
# /schedule-task pattern and keeps the briefing server-authoritative.

class DailyBriefingRequest(BaseModel):
    """Request body is intentionally empty — all data is fetched server-side."""
    pass


@router.post("/daily-briefing")
async def daily_briefing(
    authorization: Optional[str] = Header(default=None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    auth_headers = {"Authorization": authorization}

    # Time window: today 00:00 → 23:59 UTC for calendar events
    today      = date.today()
    today_str  = today.isoformat()
    time_min   = datetime(today.year, today.month, today.day,  0,  0,  0, tzinfo=timezone.utc).isoformat()
    time_max   = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()

    # ── Parallel fetch ────────────────────────────────────────────────────────
    async def _get(client: httpx.AsyncClient, url: str, **kwargs):
        try:
            return await client.get(url, headers=auth_headers, **kwargs)
        except httpx.RequestError:
            return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks_res, wb_res, cal_res, notes_res = await asyncio.gather(
            _get(client, f"{BACKEND_URL}/get-tasks"),
            _get(client, f"{BACKEND_URL}/work-blocks"),
            _get(client, f"{BACKEND_URL}/google-calendar/events",
                 params={"time_min": time_min, "time_max": time_max}),
            _get(client, f"{BACKEND_URL}/get-notes"),
        )

    tasks:           list[dict] = tasks_res.json()  if tasks_res  and tasks_res.status_code  == 200 else []
    work_blocks:     list[dict] = wb_res.json()     if wb_res     and wb_res.status_code     == 200 else []
    calendar_events: list[dict] = cal_res.json()    if cal_res    and cal_res.status_code    == 200 else []
    notes:           list[dict] = notes_res.json()  if notes_res  and notes_res.status_code  == 200 else []
    # 403 on calendar = not connected → leave as empty list (scheduler treats all time as free)

    try:
        result = await create_daily_briefing(tasks, work_blocks, calendar_events, notes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Briefing generation failed: {str(e)}")

    return {"briefing": result}


# ── Learning Resources ────────────────────────────────────────────────────────

class LearningResourcesRequest(BaseModel):
    note_content: str


@router.post("/learning-resources")
async def learning_resources(request: LearningResourcesRequest):
    try:
        result = await get_learning_resources(request.note_content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resource generation failed: {str(e)}")
    return result


# ── Smart Scheduling ──────────────────────────────────────────────────────────

class ScheduleTaskRequest(BaseModel):
    task_id:          int
    title:            str
    due_date:         str            # "YYYY-MM-DD" or ISO datetime string
    # estimated_hours maps to tasks.estimated_time (hours).  If null, the
    # service infers duration from complexity using a fixed table.
    estimated_hours:  Optional[float] = None
    complexity:       Optional[int]   = None
    tags:             list[str]       = []


@router.post("/schedule-task")
async def schedule_task_endpoint(
    request: ScheduleTaskRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Hybrid AI scheduling endpoint.

    The frontend forwards its Supabase JWT as the Authorization header.
    This service uses it to:
      1. Fetch the user's Google Calendar events from the backend (Phase 1 input).
      2. POST the finished work block back to the backend (persists with user_id).

    If Google Calendar is not connected (403), the gap-finder treats all
    working-hour time as free — the schedule will still be valid, just
    unaware of the user's external commitments.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    auth_headers = {"Authorization": authorization}

    # ── Parse deadline for the calendar fetch range ───────────────────────
    try:
        if "T" in request.due_date:
            deadline = datetime.fromisoformat(request.due_date)
        else:
            deadline = datetime.fromisoformat(f"{request.due_date}T23:59:00+00:00")
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid due_date: {request.due_date!r}")

    now      = datetime.now(tz=timezone.utc)
    time_min = now.isoformat()
    time_max = (deadline + timedelta(days=1)).isoformat()

    # ── Phase 1a: fetch Google Calendar events, availability preferences,
    #              and existing work blocks (all treated as hard-busy time) ──
    calendar_events: list[dict] = []
    raw_prefs: list[dict] = []
    existing_work_blocks: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            cal_resp = await client.get(
                f"{BACKEND_URL}/google-calendar/events",
                params={"time_min": time_min, "time_max": time_max},
                headers=auth_headers,
            )
            if cal_resp.status_code == 200:
                calendar_events = cal_resp.json()
            # 403 = Google Calendar not connected; treat as empty schedule.

            pref_resp = await client.get(
                f"{BACKEND_URL}/availability-preferences",
                headers=auth_headers,
            )
            if pref_resp.status_code == 200:
                raw_prefs = pref_resp.json()

            # Both 'suggested' and 'confirmed' work blocks occupy real time on
            # the calendar and must be treated as busy.  Excluding 'suggested'
            # blocks caused the scheduler to overlap its own previous suggestions
            # when multiple tasks were scheduled before the user had a chance to
            # accept or dismiss them.  Only 'dismissed' blocks are truly free.
            wb_resp = await client.get(
                f"{BACKEND_URL}/work-blocks",
                headers=auth_headers,
            )
            if wb_resp.status_code == 200:
                existing_work_blocks = [
                    b for b in wb_resp.json() if b.get("status") != "dismissed"
                ]
    except httpx.RequestError:
        pass  # backend unreachable — proceed with empty calendar/preferences

    # ── Convert weekly preference blackouts to concrete busy intervals ─────
    # Preferences use JS Date.getDay() convention: 0=Sun … 6=Sat.
    # Python datetime.weekday(): 0=Mon … 6=Sun → convert with (weekday+1)%7.
    #
    # Overnight windows (end_time < start_time, e.g. "22:00"→"06:00"):
    # p_end is placed on the *next* calendar day so the busy interval correctly
    # spans midnight.  The gap-finder receives datetime objects and handles
    # cross-midnight spans naturally via its day-by-day clipping logic.
    _DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    preference_busy: list[tuple[datetime, datetime]] = []
    constraint_summary: str = ""
    if raw_prefs:
        # Start one day before `now` so that overnight windows whose *start*
        # fell on the previous calendar day are still captured.
        # Example: a "Friday 21:00 → 08:00" preference generates the interval
        # (Fri 21:00, Sat 08:00).  Without the -1, a Saturday 01:00 run would
        # never scan Friday and the interval would be silently omitted, leaving
        # Sat 01:00–08:00 appearing free when it should be busy.
        scan = now.date() - timedelta(days=1)
        deadline_date = (deadline + timedelta(days=1)).date()
        while scan <= deadline_date:
            js_dow = (scan.weekday() + 1) % 7
            for p in raw_prefs:
                if p.get("day_of_week") != js_dow:
                    continue
                try:
                    sh, sm = map(int, p["start_time"].split(":"))
                    eh, em = map(int, p["end_time"].split(":"))
                    p_start = datetime(scan.year, scan.month, scan.day, sh, sm, tzinfo=timezone.utc)
                    p_end   = datetime(scan.year, scan.month, scan.day, eh, em, tzinfo=timezone.utc)
                    # Overnight: end earlier in the day than start → end is next morning
                    if p_end <= p_start:
                        p_end += timedelta(days=1)
                    preference_busy.append((p_start, p_end))
                except (ValueError, KeyError):
                    pass
            scan += timedelta(days=1)

        # Build a human-readable summary of the distinct preference rules so
        # the LLM can name specific constraints in its reasoning field.
        seen: set[tuple] = set()
        parts: list[str] = []
        for p in raw_prefs:
            key = (p.get("day_of_week"), p.get("start_time"), p.get("end_time"))
            if key in seen:
                continue
            seen.add(key)
            dow   = _DOW_NAMES[p.get("day_of_week", 0)]
            label = f" ({p['label']})" if p.get("label") else ""
            parts.append(f"{dow} {p['start_time']}–{p['end_time']}{label}")
        if parts:
            constraint_summary = "Recurring blackout windows: " + "; ".join(parts) + "."

    # ── Merge existing work blocks into busy list ─────────────────────────
    # Both suggested and confirmed blocks occupy a specific time window that
    # the scheduler already claimed.  Adding them here ensures no two tasks
    # are ever assigned overlapping slots.
    for wb in existing_work_blocks:
        try:
            wb_start = datetime.fromisoformat(wb["start_time"])
            wb_end   = datetime.fromisoformat(wb["end_time"])
            if wb_start.tzinfo is None:
                wb_start = wb_start.replace(tzinfo=timezone.utc)
            if wb_end.tzinfo is None:
                wb_end = wb_end.replace(tzinfo=timezone.utc)
            preference_busy.append((wb_start, wb_end))
        except (ValueError, KeyError):
            pass

    # ── Phase 1b + 2: gap-finder → LLM slot selection ─────────────────────
    try:
        work_block_payload = await schedule_task(
            task_id             = request.task_id,
            title               = request.title,
            due_date_str        = request.due_date,
            estimated_hours     = request.estimated_hours,
            complexity          = request.complexity,
            tags                = request.tags,
            calendar_events     = calendar_events,
            preference_busy     = preference_busy,
            constraint_summary  = constraint_summary,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "no_available_slots":
            if constraint_summary:
                user_message = (
                    "No available work blocks found before this deadline. "
                    "Your recurring blackout windows are consuming all available time in this window. "
                    "Consider adjusting a blackout window, extending the deadline, or breaking the task into smaller pieces."
                )
            else:
                user_message = (
                    "No available work blocks found before this deadline. "
                    "Consider adjusting the deadline or splitting the task into smaller pieces."
                )
            raise HTTPException(
                status_code=422,
                detail={
                    "schedulable": False,
                    "reason":      "no_available_slots",
                    "message":     user_message,
                },
            )
        raise HTTPException(status_code=400, detail=msg)

    # ── Persist: POST work block to backend (user-scoped via JWT) ────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            save_resp = await client.post(
                f"{BACKEND_URL}/work-blocks",
                json=work_block_payload,
                headers={**auth_headers, "Content-Type": "application/json"},
            )
        if save_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Failed to persist work block: {save_resp.text}",
            )
        return save_resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Backend unavailable: {exc}")