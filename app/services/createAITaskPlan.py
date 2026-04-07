"""
AI task plan generator — breaks a user-created task into scheduled subtasks.

Design decisions
─────────────────
Burnout prevention
    The scheduler enforces a 6-hour/day cognitive-load cap (DAILY_CAP_HOURS).
    Load is measured in weighted hours (urgent × 1.4, complexity × 0.7–1.5),
    not raw hours, so a high-stakes deadline registers heavier than easy work.
    Subtasks that cannot fit the window are flagged with overloaded=True and
    surface a warning to the user rather than silently exceeding the cap.

Imperative tone
    SYSTEM_MESSAGE and BASE_INSTRUCTIONS mandate strong action verbs and ban
    filler phrases ("you should", "make sure to", etc.).  CATEGORY_GUIDANCE
    injects domain-specific progression steps so the LLM writes meaningful,
    concrete subtask titles rather than generic placeholders.

Two-phase approach
    1. Deterministic — hours are split and scheduled without calling the LLM,
       so the schedule is predictable and does not change on regeneration.
    2. Generative — the LLM only writes titles and descriptions, filling in the
       pre-computed schedule slots.  This prevents hallucinated dates/durations.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import HTTPException
from openai import AsyncOpenAI

from app.schemas.task_schema import TaskCreate, Task
from app.util.filterData import (
    DAILY_CAP_HOURS,
    build_active_workload_by_day,
    detect_overload,
    split_into_chunks,
    schedule_durations,
)

load_dotenv()

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = os.getenv("OPENAI_MODEL")

SYSTEM_MESSAGE = (
    "You are a precise JSON-only task planning assistant. Return ONLY valid JSON. "
    "Every title and description you write must use an imperative, instructional tone — "
    "start with a strong action verb, be direct, and contain zero filler phrases."
)

# ── Category-specific planning guidance ──────────────────────────────────────
# Tells the LLM what a natural progression looks like for each task type.
# The model uses this to generate titles/descriptions that actually match
# the work, rather than defaulting to generic or algorithm-specific content.

CATEGORY_GUIDANCE: dict[str, str] = {
    "homework": (
        "a step-by-step homework completion plan: "
        "understand the problem → research / gather notes → outline or draft → "
        "complete the work → review and polish"
    ),
    "test": (
        "a test preparation strategy: "
        "review lecture notes and readings → practice problems (easy → hard) → "
        "mock test or timed quiz → analyze mistakes → final review"
    ),
    "project": (
        "a project delivery plan: "
        "requirements analysis → design / architecture → implementation (iterative) → "
        "testing and debugging → documentation and polish"
    ),
    "interview": (
        "an interview preparation plan: "
        "company and role research → behavioral story prep → "
        "technical review (language / system design / domain) → mock interviews → "
        "final review and logistics"
    ),
    "skill": (
        "a skill-building progression: "
        "concept introduction → guided practice with worked examples → "
        "independent practice with increasing difficulty → real-world application"
    ),
}

_DEFAULT_GUIDANCE = (
    "a logical, incremental sequence of steps toward completing the task, "
    "starting with understanding / planning and ending with review or delivery"
)

BASE_INSTRUCTIONS = """
Generate EXACTLY {n} subtasks for the task below.
Follow this progression: {guidance}

TONE — non-negotiable:
- Every title MUST start with a strong action verb (Draft, Build, Research, Implement, Review, Test, Configure, Outline, Solve, Write, etc.).
- Every description MUST be 1–3 short, direct sentences in imperative form.
- NO filler: ban phrases like "you should", "it would be a good idea to", "consider", "try to", "make sure to", "don't forget to".
- NO adjectives that add no meaning (e.g., "comprehensive", "thorough", "detailed").

CONTENT:
- Each subtask must be distinct and move the work forward.
- Include one concrete, measurable deliverable per subtask (e.g., "list 5 edge cases", "implement the POST /login endpoint", "score ≥70% on a practice test").
- Increase difficulty or scope across the sequence.
- Stay domain-relevant to the task title and description below.
- Do NOT mention time estimates, dates, or JSON keys.

Task title: {title}
Task description: {description}
""".strip()


# ── Main service function ─────────────────────────────────────────────────────

async def create_subtasks_with_llm(
    active_tasks: list[Task],
    new_task: TaskCreate,
    created_task: Task,
) -> dict:
    """
    Return a dict with keys:
        "subtasks": list of subtask dicts ready for persistence
        "overload_warning": str | None  — non-None if the user's schedule is tight
    """

    active_workload_by_day = build_active_workload_by_day(active_tasks)

    # ── STEP 1: Deterministically split hours ────────────────────────────────
    total_hours = float(new_task.estimated_time or 0.0)

    if total_hours <= 1.0:
        durations = [total_hours] if total_hours > 0 else [0.5]
    else:
        durations = split_into_chunks(
            total_hours,
            target_size=2.0,
            min_size=1.0,
            max_size=3.0,
            increment=0.5,
        )

    subtask_count = len(durations)

    # ── STEP 2: Build scheduling window ─────────────────────────────────────
    today = datetime.now(timezone.utc).date()
    start_date = today
    due_date = new_task.due_date

    if not due_date:
        buffer_end = start_date + timedelta(days=max(0, subtask_count - 1))
    else:
        buffer_end = due_date - timedelta(days=2)
        if buffer_end < start_date:
            buffer_end = due_date

    # ── STEP 3: Overload detection ───────────────────────────────────────────
    overload_info = detect_overload(
        workload_by_day=active_workload_by_day,
        start_date=start_date,
        end_date=buffer_end,
        new_task_hours=total_hours,
        daily_cap=DAILY_CAP_HOURS,
    )

    overload_warning: str | None = None
    if not overload_info["can_fit"]:
        overload_warning = (
            f"Your schedule is {overload_info['utilization_pct']}% full between "
            f"{start_date} and {buffer_end}. Only {overload_info['available_hours']} h "
            f"of slack remain — this plan may push some subtasks past the daily cap."
        )

    # ── STEP 4: LLM call — semantic titles + descriptions ───────────────────
    guidance = CATEGORY_GUIDANCE.get(new_task.category or "", _DEFAULT_GUIDANCE)
    semantic_prompt = BASE_INSTRUCTIONS.format(
        n=subtask_count,
        guidance=guidance,
        title=new_task.title or "(no title)",
        description=new_task.description or "(no description)",
    ) + f"""

Return ONLY a valid JSON array with EXACTLY {subtask_count} objects.
Each object must have exactly two keys:
{{"title": "...", "description": "..."}}
"""

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": semantic_prompt},
        ],
        temperature=0.3,
        max_tokens=2500,
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

    # Pad or truncate to match the required count
    if len(semantic_items) < subtask_count:
        for i in range(len(semantic_items), subtask_count):
            semantic_items.append({
                "title": f"Complete {new_task.title} — Step {i + 1}",
                "description": "Execute the next logical step. Review prior output, identify the remaining work, and deliver a concrete result.",
            })
    elif len(semantic_items) > subtask_count:
        semantic_items = semantic_items[:subtask_count]

    # ── STEP 5: Schedule durations onto dates ────────────────────────────────
    scheduled = schedule_durations(
        durations,
        start_date,
        buffer_end,
        active_workload_by_day,
        daily_cap=DAILY_CAP_HOURS,
        due_time_str=str(new_task.due_time) if new_task.due_time else "11:59:34.000Z",
    )

    # Surface an overload warning if any slot was force-placed
    if any(item.get("overloaded") for item in scheduled):
        overload_warning = overload_warning or (
            "One or more subtasks could not be placed within the daily cap "
            "and were scheduled on the last available day. Consider reducing "
            "scope or extending the due date."
        )

    # ── STEP 6: Merge semantic content + schedule ────────────────────────────
    final_subtasks = []
    for i, slot in enumerate(scheduled):
        title = semantic_items[i].get("title") or f"Complete {new_task.title} — Step {i + 1}"
        description = semantic_items[i].get("description") or "Execute this step and deliver a concrete result."

        final_subtasks.append({
            "parent_task_id": created_task["id"],
            "title": title,
            "description": description,
            "category": new_task.category,
            "due_date": slot["date"],
            "due_time": slot["due_time"],
            "estimated_time": slot["duration"],
            "complexity": new_task.complexity or 3,
            "tags": new_task.tags if new_task.tags else [],
        })

    return {"subtasks": final_subtasks, "overload_warning": overload_warning}
