from datetime import datetime, timedelta, timezone

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

class DailyBriefingRequest(BaseModel):
    tasks: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []


@router.post("/daily-briefing")
async def daily_briefing(request: DailyBriefingRequest):
    try:
        text = await create_daily_briefing(request.tasks, request.notes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Briefing generation failed: {str(e)}")
    return {"briefing": text}


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

    # ── Phase 1a: fetch Google Calendar events ────────────────────────────
    calendar_events: list[dict] = []
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
            # Any other non-200 is a transient error — also fall back gracefully.
    except httpx.RequestError:
        pass  # backend unreachable — proceed with empty calendar

    # ── Phase 1b + 2: gap-finder → LLM slot selection ─────────────────────
    try:
        work_block_payload = await schedule_task(
            task_id         = request.task_id,
            title           = request.title,
            due_date_str    = request.due_date,
            estimated_hours = request.estimated_hours,
            complexity      = request.complexity,
            tags            = request.tags,
            calendar_events = calendar_events,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "no_capacity":
            raise HTTPException(
                status_code=422,
                detail={
                    "schedulable": False,
                    "reason":      "no_capacity",
                    "message": (
                        "No available work blocks found before this deadline. "
                        "Consider adjusting the deadline or splitting the task into smaller pieces."
                    ),
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