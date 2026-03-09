from fastapi import APIRouter, HTTPException
import httpx
from datetime import datetime, date
from app.schemas.task_schema import Task, TaskGenerationRequest, TaskGenerationResponse, TaskCreate
from app.services.task_generator import task_generation_service
from app.core.config import settings

router = APIRouter()

BASE_URL = settings.BACKEND_URL


@router.get("/get-tasks", response_model=list[Task])
async def read_tasks():
    """Get all tasks from the backend."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{BASE_URL}/get-tasks")

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch tasks")

    return response.json()


@router.post("/generate-tasks", response_model=TaskGenerationResponse)
async def generate_tasks_handler(request: TaskGenerationRequest):
    """
    Generate AI-powered task breakdown based on category and due date.
    
    The LLM will create a series of subtasks that help the user prepare/complete the main task.
    
    Categories:
    - homework: Study tasks for homework completion
    - project: Work breakdown for project completion
    - test: Study schedule for exam preparation
    - interview: Preparation tasks for interview
    - skill: Learning curriculum for skill mastery
    """
    try:
        # Generate tasks using the LLM service
        generated_task_creates = await task_generation_service.generate_tasks(
            title=request.title,
            category=request.category,
            description=request.description,
            due_date=request.due_date,
            due_time=request.due_time
        )
        
        if not generated_task_creates:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate tasks. Please try again."
            )
        
        # Convert TaskCreate objects to Task objects
        # Note: In a real implementation, these would be saved to the backend first
        generated_tasks = []
        for idx, task_create in enumerate(generated_task_creates, start=1):
            task = Task(
                id=idx,  # Temporary ID - would be from database in production
                title=task_create.title,
                description=task_create.description,
                category=task_create.category,
                due_date=task_create.due_date,
                due_time=task_create.due_time,
                created_date=datetime.now(),
                completed=task_create.completed,
                urgent=task_create.urgent,
                completed_date=task_create.completed_date,
                tags=task_create.tags
            )
            generated_tasks.append(task)
        
        # Create main task representation (in production, this would be saved to backend)
        due_date_obj = datetime.strptime(request.due_date, "%Y-%m-%d").date()
        
        main_task = Task(
            id=0,
            title=request.title,
            description=request.description,
            category=request.category,
            due_date=due_date_obj,
            due_time=request.due_time,
            created_date=datetime.now(),
            completed=False,
            urgent=False,
            completed_date=None,
            tags=[]
        )
        
        return TaskGenerationResponse(
            main_task=main_task,
            generated_tasks=generated_tasks,
            message=f"Successfully generated {len(generated_tasks)} tasks for '{request.title}'"
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Error generating tasks: {e}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while generating tasks"
        )
