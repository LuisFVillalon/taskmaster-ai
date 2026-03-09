import json
import httpx
from datetime import datetime, timedelta
from typing import List, Optional
from app.schemas.task_schema import TaskCreate, Task
from app.core.ollama_client import ollama_client
from app.core.config import settings


class TaskGenerationService:
    """Service for generating task breakdowns using LLM with consideration for existing tasks."""
    
    # Task categories
    HOMEWORK_ASSIGNMENT = "homework"
    PROJECT = "project"
    TEST = "test"
    INTERVIEW = "interview"
    SKILL = "skill"
    
    VALID_CATEGORIES = {HOMEWORK_ASSIGNMENT, PROJECT, TEST, INTERVIEW, SKILL}
    
    async def get_existing_tasks(self) -> List[Task]:
        """Fetch existing tasks from the backend."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{settings.BACKEND_URL}/get-tasks")
                if response.status_code == 200:
                    return response.json()
                return []
        except Exception as e:
            print(f"Error fetching existing tasks: {e}")
            return []
    
    def _format_existing_tasks_for_prompt(self, tasks: List[Task]) -> str:
        """Format existing tasks into a readable summary for the LLM."""
        if not tasks:
            return "No existing tasks."
        
        task_summary = "Existing user tasks:\n"
        for task in tasks:
            if not task.get("completed"):
                due_info = ""
                if task.get("due_date"):
                    due_info = f" - Due: {task['due_date']}"
                    if task.get("due_time"):
                        due_info += f" at {task['due_time']}"
                task_summary += f"- {task['title']}: {task.get('category', 'uncategorized')}{due_info}\n"
        
        return task_summary
    
    async def generate_tasks_for_homework_or_project(
        self,
        title: str,
        description: Optional[str],
        due_date: str,
        due_time: Optional[str] = None,
        is_project: bool = False
    ) -> List[TaskCreate]:
        """
        Generate subtasks for homework/project completion.
        Creates timeline tasks showing when to work on the assignment.
        """
        existing_tasks = await self.get_existing_tasks()
        existing_summary = self._format_existing_tasks_for_prompt(existing_tasks)
        
        task_type = "project" if is_project else "homework assignment"
        
        prompt = f"""You are helping a college student manage their time effectively. 
Generate a breakdown schedule for completing a {task_type}.

Assignment: {title}
Description: {description or 'No description provided'}
Due Date & Time: {due_date} {due_time or 'End of day'}

{existing_summary}

Create 4-6 preparatory tasks that break down the work needed. For each task:
1. Space them out evenly before the due date
2. Avoid scheduling during existing tasks
3. Include buffer time (no tasks immediately before due date for final review)
4. Each task should be specific and actionable
5. Consider mental health - include reasonable breaks

Format your response as a JSON array with this structure:
[
  {{
    "title": "Task title",
    "description": "What specifically to do",
    "days_before_due": number (how many days before due date to schedule)
  }}
]

Only respond with valid JSON, no other text."""

        response = await ollama_client.generate(prompt, temperature=0.7)
        
        # Parse the LLM response
        tasks = self._parse_llm_task_response(response, due_date)
        return tasks
    
    async def generate_tasks_for_test_or_interview(
        self,
        title: str,
        description: Optional[str],
        due_date: str,
        due_time: Optional[str] = None,
        is_interview: bool = False
    ) -> List[TaskCreate]:
        """
        Generate preparation tasks for test/interview.
        Creates studying/preparation tasks spread throughout the prep period.
        """
        existing_tasks = await self.get_existing_tasks()
        existing_summary = self._format_existing_tasks_for_prompt(existing_tasks)
        
        prep_type = "interview" if is_interview else "test"
        
        prompt = f"""You are helping a college student prepare for an important {prep_type}.

{prep_type.capitalize()}: {title}
Details: {description or 'No additional details provided'}
Date & Time: {due_date} {due_time or 'Time TBD'}

{existing_summary}

Create a comprehensive study/preparation plan with 5-8 tasks:
1. Start with content review, move to practice, then final review
2. Include breaks between study sessions (mental health is important)
3. Avoid dense schedules - space tasks logically
4. Consider cumulative learning - tasks build on each other
5. Each task should be focused and achievable in one session

Format your response as a JSON array:
[
  {{
    "title": "Preparation task",
    "description": "Specific focus area - e.g., 'Review chapters 3-5' or 'Practice mock problems'",
    "days_before_due": number (days before the test/interview),
    "priority": "high" or "medium" or "low"
  }}
]

Only respond with valid JSON, no other text."""

        response = await ollama_client.generate(prompt, temperature=0.6)
        
        tasks = self._parse_llm_task_response(response, due_date, is_prep=True)
        return tasks
    
    async def generate_tasks_for_skill(
        self,
        title: str,
        description: Optional[str],
        due_date: str
    ) -> List[TaskCreate]:
        """
        Generate a learning curriculum for skill development.
        Creates a structured learning path with progressive complexity.
        """
        existing_tasks = await self.get_existing_tasks()
        existing_summary = self._format_existing_tasks_for_prompt(existing_tasks)
        
        prompt = f"""You are creating a structured learning curriculum for a college student to learn a new skill.

Skill to Learn: {title}
Context: {description or 'General skill learning'}
Target Completion Date: {due_date}

{existing_summary}

Design a progressive learning curriculum with 6-10 tasks:
1. Start with fundamentals and gradually increase complexity
2. Include hands-on practice and application
3. Space tasks realistically for skill mastery (not cramming)
4. Include review/consolidation tasks
5. Balance theory with practical exercises
6. Consider mental load - don't overwhelm

Format as JSON:
[
  {{
    "title": "Learning task",
    "description": "What to learn/practice - be specific",
    "days_before_due": number (days before target date to complete),
    "level": "beginner" or "intermediate" or "advanced"
  }}
]

Only respond with valid JSON, no other text."""

        response = await ollama_client.generate(prompt, temperature=0.6)
        
        tasks = self._parse_llm_task_response(response, due_date)
        return tasks
    
    def _parse_llm_task_response(
        self, 
        response: str, 
        due_date: str,
        is_prep: bool = False
    ) -> List[TaskCreate]:
        """
        Parse LLM response and convert to TaskCreate objects.
        """
        try:
            # Extract JSON from response
            json_start = response.find('[')
            json_end = response.rfind(']') + 1
            if json_start == -1 or json_end == 0:
                print(f"Could not find JSON in response: {response}")
                return []
            
            json_str = response[json_start:json_end]
            task_data = json.loads(json_str)
            
            # Convert due_date string to datetime
            due_datetime = datetime.strptime(due_date, "%Y-%m-%d")
            
            tasks = []
            for task_info in task_data:
                days_before = task_info.get("days_before_due", 0)
                scheduled_date = due_datetime - timedelta(days=days_before)
                
                task = TaskCreate(
                    title=task_info.get("title", "Unnamed task"),
                    description=task_info.get("description", ""),
                    category=task_info.get("category", "task"),
                    due_date=scheduled_date.date(),
                    urgent=task_info.get("priority", "low").lower() == "high",
                    tags=[]
                )
                tasks.append(task)
            
            return tasks
            
        except json.JSONDecodeError as e:
            print(f"Error parsing LLM response: {e}")
            print(f"Response was: {response}")
            return []
    
    async def generate_tasks(
        self,
        title: str,
        category: str,
        description: Optional[str] = None,
        due_date: Optional[str] = None,
        due_time: Optional[str] = None
    ) -> List[TaskCreate]:
        """
        Main method to generate tasks based on category.
        
        Args:
            title: Main task/goal title
            category: One of HOMEWORK, PROJECT, TEST, INTERVIEW, SKILL
            description: Additional context
            due_date: Due date in YYYY-MM-DD format
            due_time: Optional due time
            
        Returns:
            List of generated TaskCreate objects
        """
        if category.lower() not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {self.VALID_CATEGORIES}")
        
        if not due_date:
            raise ValueError("due_date is required for task generation")
        
        category_lower = category.lower()
        
        if category_lower == self.HOMEWORK_ASSIGNMENT:
            return await self.generate_tasks_for_homework_or_project(
                title, description, due_date, due_time, is_project=False
            )
        elif category_lower == self.PROJECT:
            return await self.generate_tasks_for_homework_or_project(
                title, description, due_date, due_time, is_project=True
            )
        elif category_lower == self.TEST:
            return await self.generate_tasks_for_test_or_interview(
                title, description, due_date, due_time, is_interview=False
            )
        elif category_lower == self.INTERVIEW:
            return await self.generate_tasks_for_test_or_interview(
                title, description, due_date, due_time, is_interview=True
            )
        elif category_lower == self.SKILL:
            return await self.generate_tasks_for_skill(title, description, due_date)


task_generation_service = TaskGenerationService()
