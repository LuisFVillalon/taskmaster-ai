from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from app.schemas.task_schema import Task, TaskCreate
from app.services.createAITaskPlan import create_subtasks_with_llm
import httpx
import os
from dotenv import load_dotenv

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
        subtasks = await create_subtasks_with_llm(
            active_tasks=active_tasks,
            new_task=new_task,
        )
    except HTTPException:
        raise
    except Exception as e:
        print("LLM error type:", type(e).__name__)
        print("LLM error message:", str(e))
        import traceback
        traceback.print_exc()  # prints the full stack trace
        raise HTTPException(status_code=500, detail=f"LLM service error: {str(e)}")

    # ── 4. Persist subtasks ────────────────────────────────────────────────
    # for subtask in subtasks:
    #     async with httpx.AsyncClient(timeout=20.0) as client:
    #         await client.post(f"{BACKEND_URL}/create-task", json=subtask)

    return {
        "message": "Task plan created successfully",
        "parent_task_id": created_task["id"],
        # "subtasks_created": len(subtasks)
    }