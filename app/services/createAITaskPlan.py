import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import HTTPException
from openai import AsyncOpenAI

from app.schemas.task_schema import TaskCreate, Task
from app.util.filterData import (
    build_active_workload_by_day,
    split_into_chunks,
    schedule_durations
)

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

SYSTEM_MESSAGE = "You are a precise JSON-only task planning assistant. Return ONLY valid JSON."

INSTRUCTIONS = """
You are an academic productivity planner for a CS undergrad.

Input JSON fields:
- new_task {title, description, category, estimated_time, due_date, due_time, tags?}
- new_task_id
- active_workload_by_day: {"YYYY-MM-DD": hours}

Rules:
1) Daily cap: (subtasks scheduled that day + active_workload_by_day[day]) <= 6.0.
2) If new_task.estimated_time <= 1.0: output exactly 1 subtask mirroring new_task.
3) Else: MUST output multiple subtasks.
   - Each subtask estimated_time must be in 0.5h increments.
   - 1.0 <= estimated_time <= 3.0 for every subtask (except one 0.5h leftover allowed).
   - Target subtask size = 2.0h (use ceil(estimated_time / 2.0) subtasks; ±1 allowed only for rounding).
   - Ensure max(subtask.estimated_time) <= 3.0 (no large lumps).
4) Sum(subtask estimated_time) == new_task.estimated_time (exact except for 0.5h rounding).
5) Scheduling: finish major work at least 2 days before new_task.due_date (leave buffer day(s)).
   Spread subtasks evenly between earliest feasible start and (due_date - 2 days); avoid back-to-back clusters when possible.
6) Use category sequencing templates; include a light review/refinement near the end when appropriate.

Output:
Return ONLY a valid JSON array.
Each item must be:
{"title":"...","description":"...","category":"test|project|interview|homework|skill|null",
 "due_date":"YYYY-MM-DD","due_time":"HH:MM:SS.000Z",
 "estimated_time":1.5,"complexity":3,"tags":[...]}
""".strip()


async def create_subtasks_with_llm(active_tasks: list[Task], new_task: TaskCreate):

    active_workload_by_day = build_active_workload_by_day(active_tasks)

    # ✅ STEP 1: Deterministically split hours FIRST (so we know how many subtasks we need)
    total_hours = float(new_task.estimated_time or 0.0)

    if total_hours <= 1.0:
        durations = [total_hours]
    else:
        durations = split_into_chunks(
            total_hours,
            target_size=2.0,
            min_size=1.0,
            max_size=3.0,
            increment=0.5
        )

    subtask_count = len(durations)

    payload = {
        "new_task": new_task.model_dump(mode="json"),
        "new_task_id": -1,
        "active_workload_by_day": active_workload_by_day,
        "required_subtask_count": subtask_count,  # helpful for the model
    }
    payload_json = json.dumps(payload, separators=(",", ":"))

    # ✅ Ask the LLM for EXACTLY N semantic steps, with exercises/examples
    semantic_prompt = INSTRUCTIONS + f"""
IMPORTANT:
Return ONLY a valid JSON array with EXACTLY {subtask_count} items.
Each item must contain ONLY:
{{"title":"...","description":"..."}}

Quality requirements for EACH description:
- 2 to 4 sentences
- Must include at least one concrete exercise (e.g., "solve 2 easy + 1 medium", "implement from scratch", "write a pattern checklist")
- Must mention specific pattern areas when relevant (e.g., sliding window, two pointers, BFS/DFS, DP, greedy, backtracking, heaps)
- Gradually increase difficulty over the list
Do NOT include estimated_time, due_date, due_time, complexity, tags, or any extra keys.
"""

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": semantic_prompt},
            {"role": "user", "content": payload_json},
        ],
        temperature=0.25,
        max_tokens=2500,  # more room for richer descriptions
    )

    llm_text = (response.choices[0].message.content or "").strip()

    try:
        start = llm_text.find("[")
        end = llm_text.rfind("]") + 1
        semantic_items = json.loads(llm_text[start:end])
        if not isinstance(semantic_items, list):
            semantic_items = []
    except Exception:
        semantic_items = []

    # ✅ Hard fallback: if model returns wrong count, pad/truncate to match
    if len(semantic_items) < subtask_count:
        for i in range(len(semantic_items), subtask_count):
            semantic_items.append({
                "title": f"{new_task.title} - Part {i+1}",
                "description": "Auto-generated fallback: do focused LeetCode pattern practice and update your notes/checklist."
            })
    elif len(semantic_items) > subtask_count:
        semantic_items = semantic_items[:subtask_count]

    # ✅ STEP 2: Build scheduling window
    today = datetime.utcnow().date()
    start_date = today

    due_date = new_task.due_date
    if not due_date:
        buffer_end = start_date + timedelta(days=max(0, subtask_count - 1))
    else:
        buffer_days = 2
        buffer_end = due_date - timedelta(days=buffer_days)
        if buffer_end < start_date:
            buffer_end = due_date

    # ✅ STEP 3: Schedule respecting daily caps
    scheduled = schedule_durations(
        durations,
        start_date,
        buffer_end,
        active_workload_by_day,
        daily_cap=6.0,
        due_time_str=str(new_task.due_time) if new_task.due_time else "11:59:34.000Z"
    )

    # ✅ STEP 4: Merge semantic items + schedule + durations
    final_subtasks = []
    for i, scheduled_item in enumerate(scheduled):
        title = semantic_items[i].get("title") or f"{new_task.title} - Part {i+1}"
        description = semantic_items[i].get("description") or ""

        final_subtasks.append({
            "title": title,
            "description": description,
            "category": new_task.category,
            "due_date": scheduled_item["due_date"],
            "due_time": scheduled_item["due_time"],
            "estimated_time": scheduled_item["estimated_time"],  # ✅ sums exactly to parent
            "complexity": new_task.complexity or 3,
            "tags": new_task.tags if new_task.tags else [],
        })
    print(final_subtasks)
    return final_subtasks

