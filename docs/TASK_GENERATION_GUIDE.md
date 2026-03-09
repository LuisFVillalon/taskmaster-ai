# TaskMaster AI - Task Generation Feature Guide

## Overview

The Task Generation feature uses AI (via Ollama) to intelligently generate subtasks based on a user's main goal and category. The system:

- **Breaks down complex goals** into manageable subtasks
- **Prevents task overlap** by checking existing tasks
- **Considers mental health** with appropriate breaks and spacing
- **Tailors recommendations** for undergraduate college students
- **Creates realistic timelines** based on due dates

## Categories & Behavior

### 1. **Homework** Assignment
When a student creates a homework task, the LLM generates:
- A timeline of work sessions before the due date
- Specific focus areas for each session
- Buffer time before the deadline for final review

Example: A homework assignment due in 10 days might generate:
- Day 10: Review problem statement and requirements
- Day 7: Complete initial problems
- Day 3: Review and fix difficult problems
- Day 1: Final proof-reading

### 2. **Project**
Similar to homework but typically with:
- More tasks (4-6)
- Consideration for different project phases (planning, execution, review)
- Slightly more spaced out due to project complexity

### 3. **Test**
Generates a study schedule:
- Content review tasks (initial learning)
- Practice tasks (applying knowledge)
- Final review tasks (consolidation)
- Mental breaks between study sessions

Example: 2-week test prep might generate:
- Day 14: Review chapters 1-2
- Day 10: Review chapters 3-4
- Day 7: Practice problems from all chapters
- Day 3: Mock exam
- Day 1: Final review of weak areas

### 4. **Interview**
Generates interview preparation tasks:
- Technical/domain knowledge review
- Interview practice tasks (mock interviews, common questions)
- Company/role research
- Final preparation and confidence building

### 5. **Skill**
Generates a learning curriculum:
- Progressive complexity (beginner → advanced)
- Theory and practice balance
- Hands-on projects/applications
- Review and mastery consolidation

Example: Learning Python programming over 8 weeks:
- Week 1: Syntax basics and data types
- Week 2: Control flow and functions
- Week 3: Data structures
- Week 4-5: Object-oriented programming
- Week 6: Practice projects
- Week 7: Advanced topics
- Week 8: Integration project

## Conflict Avoidance Strategy

The system prevents task overlap by:

1. **Fetching existing tasks** from the backend
2. **Creating a summary** of all active tasks with their due dates
3. **Sharing this context** with the LLM
4. **LLM considers the schedule** when generating new tasks
5. **Spacing out tasks** to respect the user's existing commitments

The LLM actively considers:
- Days when the user has many existing tasks
- Recommended break days between intensive study/work
- Peak stress periods (e.g., midterm/final seasons)

## Mental Health Considerations

The system incorporates:

- **Reasonable task load**: Limiting to 1-3 new subtasks per day
- **Break scheduling**: Never scheduling tasks back-to-back
- **Progressive intensity**: Building up to harder tasks gradually
- **Consolidation periods**: Time to review and integrate learning
- **Buffer time**: Always leaving time before due dates for unexpected issues
- **Weekly breaks**: For longer projects/learning goals

These are built into the LLM prompts to match undergraduate stress levels and learning best practices.

## API Usage

### Endpoint: `POST /generate-tasks`

**Request Body:**
```json
{
  "title": "Complete CS 101 Project",
  "category": "project",
  "description": "Build a Python calculator with GUI",
  "due_date": "2024-04-15",
  "due_time": "23:59"
}
```

**Response:**
```json
{
  "main_task": {
    "id": 0,
    "title": "Complete CS 101 Project",
    "description": "Build a Python calculator with GUI",
    "category": "project",
    "due_date": "2024-04-15",
    "due_time": "23:59",
    "completed": false,
    "urgent": false,
    "created_date": "2024-03-15T10:30:00",
    "completed_date": null,
    "tags": []
  },
  "generated_tasks": [
    {
      "id": 1,
      "title": "Design calculator UI mockup",
      "description": "Create a sketch of the calculator interface",
      "category": "project",
      "due_date": "2024-04-13",
      "completed": false,
      "urgent": false,
      "tags": []
    },
    ...
  ],
  "message": "Successfully generated 5 tasks for 'Complete CS 101 Project'"
}
```

### Valid Categories
- `homework` - For homework assignments
- `project` - For projects with multiple phases
- `test` - For exam preparation
- `interview` - For interview preparation
- `skill` - For learning new skills

### Date Format
- **due_date**: `YYYY-MM-DD` (required)
- **due_time**: `HH:MM` in 24-hour format (optional)

## Setup Requirements

### 1. **Ollama Installation & Setup**

The system uses Ollama to run local LLM models.

**Install Ollama:**
- Windows/Mac/Linux: Download from [ollama.ai](https://ollama.ai)

**Start Ollama Server:**
```bash
ollama serve
```

**Pull a Model (in another terminal):**
```bash
ollama pull mistral
```

Or use another model:
```bash
ollama pull llama2
ollama pull neural-chat
```

### 2. **Backend Requirements**

Ensure your backend API is running at `http://127.0.0.1:8000` with:
- `GET /get-tasks` endpoint that returns existing tasks

**Expected response format:**
```json
[
  {
    "id": 1,
    "title": "Existing task",
    "category": "homework",
    "due_date": "2024-03-20",
    "completed": false
  }
]
```

### 3. **Environment Configuration**

Create/update `.env` file:
```env
# Ollama Configuration
OLLAMA_URL=http://localhost:11434
MODEL_NAME=mistral

# Backend Configuration
BACKEND_URL=http://127.0.0.1:8000
```

### 4. **Dependencies**

Install requirements:
```bash
pip install -r requirements.txt
```

## Running the Application

### Development Server
```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`

### Health Check
```bash
curl http://localhost:8000/health
```

### Generate Tasks Example
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Prepare for Midterm Exam",
    "category": "test",
    "description": "Math 201 - Calculus midterm",
    "due_date": "2024-03-25",
    "due_time": "14:00"
  }'
```

## Architecture

```
app/
├── core/
│   ├── config.py              # Settings management
│   └── ollama_client.py       # LLM API client
├── services/
│   └── task_generator.py      # Task generation logic
├── schemas/
│   ├── task_schema.py         # Task models and request/response formats
│   └── tag_schema.py          # Tag model
├── api/
│   └── routes/
│       ├── tasks_router.py    # Basic task endpoints
│       └── task_generation.py # AI-powered task generation endpoint
└── main.py                     # FastAPI app initialization
```

## How Task Generation Works

### Step-by-Step Process

1. **User submits request** with goal title, category, and due date
2. **Service fetches existing tasks** from backend API
3. **LLM receives prompt** with:
   - User's goal and category
   - Existing tasks (to avoid conflicts)
   - Category-specific instructions
   - Best practices for college students
4. **LLM generates tasks** in JSON format with:
   - Task title
   - Specific description
   - Days before due date to complete
   - Priority/level indicator
5. **Service converts** LLM response to Task objects
6. **Response sent** with all generated tasks

### LLM Prompt Strategy

Different prompts are optimized for each category:

- **Homework/Project**: Focus on timeline and work phases
- **Test**: Emphasis on progressive learning, practice, and review
- **Interview**: Mix of theory, practice, and confidence building
- **Skill**: Progressive curriculum with theory and application balance

All prompts include:
- Existing task summary
- Mental health reminders
- Undergraduate student context
- Specific, actionable task requirements

## Troubleshooting

### "Connection refused" error
- Ensure Ollama is running: `ollama serve`
- Check OLLAMA_URL in `.env` (default: `http://localhost:11434`)

### "Failed to fetch tasks" error
- Ensure backend API is running at configured BACKEND_URL
- Verify `/get-tasks` endpoint exists and returns valid JSON

### "No JSON found in response" error
- The LLM model may need adjustment
- Try changing MODEL_NAME in `.env` (llama2, neural-chat, etc.)
- Ensure the model is pulled: `ollama pull <model_name>`

### Slow task generation
- LLM inference can take 30-120 seconds initially
- Ensure adequate system resources
- Consider using a faster model (neural-chat vs mistral)

## Future Enhancements

Potential improvements:

1. **User preferences**: Store learning style, preferred schedule patterns
2. **Real-time updates**: Update task suggestions as user completes tasks
3. **Performance tracking**: Learn from which task schedules work best
4. **Difficulty estimation**: Adjust tasks based on subject difficulty
5. **Collaborative features**: Share task plans with study groups
6. **Push notifications**: Remind users when subtasks should start
7. **Flexible models**: Support multiple LLM providers (OpenAI, Claude, etc.)

## Best Practices

1. **Be descriptive**: Provide detailed descriptions for better task generation
2. **Realistic due dates**: Give the LLM enough time to create spacing
3. **Check existing tasks**: Ensure your most important tasks are already recorded
4. **Review generated tasks**: The LLM generates suggestions - users should verify
5. **Iterate**: Regenerate if the first attempt doesn't match your preferred style
6. **Break smaller goals**: Very short timeframes (1-2 days) will generate fewer tasks

## Support & Contributing

For issues, suggestions, or contributions, please reach out to the development team.
