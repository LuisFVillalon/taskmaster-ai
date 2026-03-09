# TaskMaster AI - Architecture & System Design

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          USER APPLICATION                           │
│                      (Web/Mobile Frontend)                          │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                    HTTP POST /generate-tasks
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FASTAPI APPLICATION                            │
│                    Port: 8000 (Development)                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │               API Routes Layer                              │   │
│  ├─────────────────────────────────────────────────────────────┤   │
│  │                                                              │   │
│  │  • GET  /health              → Health check                │   │
│  │  • GET  /get-tasks           → Fetch all tasks             │   │
│  │  • POST /generate-tasks      → ⭐ AI Task Generation       │   │
│  │                                                              │   │
│  └──────────────────────┬────────────────────────────────────┘   │
│                         │                                          │
│                         ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │         Task Generation Service Layer                       │   │
│  ├─────────────────────────────────────────────────────────────┤   │
│  │                                                              │   │
│  │  TaskGenerationService                                      │   │
│  │  ├─ generate_tasks()                                        │   │
│  │  ├─ get_existing_tasks()  ◄──────┐                          │   │
│  │  ├─ generate_tasks_for_homework_or_project()               │   │
│  │  ├─ generate_tasks_for_test_or_interview()                │   │
│  │  ├─ generate_tasks_for_skill()                            │   │
│  │  ├─ _format_existing_tasks_for_prompt()                   │   │
│  │  └─ _parse_llm_task_response()                            │   │
│  │                                                              │   │
│  └────────┬─────────────────────────────────┬────────────────┘   │
│           │                                  │                     │
│           ▼                                  ▼                     │
│  ┌─────────────────────┐      ┌──────────────────────────────┐   │
│  │ Config Module       │      │ Ollama Client Module         │   │
│  ├─────────────────────┤      ├──────────────────────────────┤   │
│  │                     │      │                              │   │
│  │ Settings:           │      │ • generate()                │   │
│  │ • OLLAMA_URL        │      │ • Error handling            │   │
│  │ • MODEL_NAME        │      │ • Async support             │   │
│  │ • BACKEND_URL       │      │ • Temperature control       │   │
│  │                     │      │                              │   │
│  └─────────────────────┘      └──────────────┬───────────────┘   │
│                                               │                     │
└───────────────────────────────────────────────┼─────────────────────┘
                                                │
                      HTTP API Call to Ollama   │
                                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      OLLAMA SERVICE                                  │
│                  Port: 11434 (Local Process)                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  LLM Model Engine                            │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │                                                              │  │
│  │  Selected Model: Mistral (4.1 GB)                          │  │
│  │  (Can also use: llama2, neural-chat, orca-mini, etc.)      │  │
│  │                                                              │  │
│  │  Receives:                                                  │  │
│  │  • Category-specific prompt with context                   │  │
│  │  • Existing tasks information                              │  │
│  │  • User deadline and preferences                           │  │
│  │                                                              │  │
│  │  Generates:                                                 │  │
│  │  • JSON array of task objects                              │  │
│  │  • With titles, descriptions, scheduling                   │  │
│  │  • Considering mental health & conflicts                   │  │
│  │                                                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                    BACKEND/DATABASE LAYER                            │
│                  Port: 8000 (Your Data Backend)                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  • Stores user tasks                                                │
│  • Provides GET /get-tasks endpoint                                 │
│  • Used by task generator for conflict detection                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Data Flow Diagram

```
User Request
    │
    │ {title, category, due_date, ...}
    │
    ▼
┌─────────────────────────────┐
│ Validate Input              │
│ - Check category valid      │
│ - Verify date format        │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Fetch Existing Tasks        │
│ - Call backend /get-tasks   │
│ - Format for LLM context    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Build LLM Prompt            │
│ - Inject task context       │
│ - Add category-specific     │
│   instructions              │
│ - Include best practices    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Call Ollama                 │
│ - Send prompt to model      │
│ - Set temperature (0.7)     │
│ - Wait for response (30s+)  │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Parse Response              │
│ - Extract JSON from text    │
│ - Validate task structure   │
│ - Convert dates             │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Create Task Objects         │
│ - Convert TaskCreate to Task│
│ - Assign IDs                │
│ - Add timestamps            │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Return Response             │
│ - Main task info            │
│ - Generated subtasks list   │
│ - Success message           │
└──────────┬──────────────────┘
           │
           ▼
    User Display
```

## Category-Specific Prompt Templates

### Homework Category
```
Prompt Structure:
├─ Assignment context
├─ Due date/deadline
├─ Existing tasks (to avoid conflicts)
├─ Create timeline tasks
├─ Space evenly before deadline
├─ Include buffer time
└─ Format as JSON
```

### Test/Interview Categories
```
Prompt Structure:
├─ Test/Interview details
├─ Date/time of event
├─ Existing tasks (to avoid conflicts)
├─ Create preparation schedule
├─ Progressive learning path
├─ Include practice/review
├─ Consider mental health breaks
└─ Format as JSON
```

### Skill Category
```
Prompt Structure:
├─ Skill description
├─ Target completion date
├─ Existing commitments
├─ Design learning curriculum
├─ Beginner → Advanced progression
├─ Theory + Practice balance
├─ Include projects/applications
└─ Format as JSON
```

## Component Interaction Flow

```
┌─────────────┐
│   Request   │
└──────┬──────┘
       │
       ▼
┌──────────────────────┐
│ task_generation.py   │ ◄─ Receives HTTP request
│ (API Route)          │    Validates input
└──────────┬───────────┘    Calls service
           │
           ▼
┌──────────────────────┐
│ task_generator.py    │ ◄─ TaskGenerationService
│ (Service Layer)      │    Gets existing tasks
└──┬───────────┬───────┘    Builds prompt
   │           │
   │           ▼
   │    ┌──────────────────┐
   │    │ ollama_client.py │ ◄─ Calls LLM
   │    │ (LLM Client)     │    Gets JSON response
   │    └──────────────────┘    Parses result
   │           │
   │           ▼
   │    ┌──────────────────┐
   │    │ Ollama Service   │ ◄─ Runs LLM model
   │    │ (Port 11434)     │    Returns JSON
   │    └──────────────────┘
   │
   ▼
┌──────────────────────┐
│ Response Object      │ ◄─ TaskGenerationResponse
│ - main_task          │    - main_task: Task
│ - generated_tasks    │    - generated_tasks: List[Task]
│ - message            │    - message: str
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐
│ Return to User      │
│ (JSON Response)     │
└─────────────────────┘
```

## Deployment Architecture

### Development Environment
```
Local Machine
├─ Python virtual environment
├─ FastAPI on localhost:8000
├─ Ollama on localhost:11434
└─ Backend on localhost:8000
```

### Production Environment (Recommended)
```
Cloud Provider (Azure/AWS/GCP)
│
├─ API Server
│  └─ FastAPI in container
│     └─ Connected to...
│
├─ LLM Service
│  └─ Ollama in container (or cloud LLM service)
│     └─ With model caching
│
├─ Data Backend
│  └─ Database with task storage
│
└─ Cache Layer (Optional)
   └─ Redis for response caching
```

## Security Considerations

### Input Validation
- Category must be one of: homework, project, test, interview, skill
- Due date format strictly YYYY-MM-DD
- Maximum description length enforced
- No SQL injection risks (Pydantic validation)

### Error Handling
- LLM errors caught and logged
- Backend connectivity failures handled gracefully
- Invalid JSON responses detected
- Rate limiting recommended for production

### Data Privacy
- Generated queries include user's existing tasks
- No telemetry sent to external services
- Local LLM (Ollama) - no cloud exposure
- Backend connection is local/internal network

## Performance Characteristics

### First Request
- Time: 30-120 seconds
- Activity: Model initialization, warm-up
- Typical for first inference

### Subsequent Requests
- Time: 5-30 seconds
- Activity: Model already loaded
- Much faster after warm-up

### Resource Usage
- RAM: ~8GB minimum
- GPU: Optional (speeds up 5-10x)
- Storage: ~4GB per model
- Network: Only for backend connectivity

## Scalability Considerations

### For Multiple Users
- Current: Single-user friendly
- Enhancement: Add authentication
- Enhancement: User-specific task storage
- Enhancement: Request queuing if many users

### For Higher Load
- Consider: Model server infrastructure
- Consider: Load balancer for API
- Consider: Cache responses
- Consider: Async task queue

## Monitoring & Observability

### Key Metrics to Track
- Generation request success rate
- Average generation time
- LLM error rate
- Backend connectivity issues
- User task overlap incidents

### Logging Points
- Request received
- Existing tasks fetched
- LLM prompt sent
- LLM response received
- Response parsing
- Final response returned

---

This architecture is designed to be:
- **Modular**: Easy to replace/upgrade components
- **Scalable**: Can grow from single user to many
- **Reliable**: Error handling at each layer
- **Observable**: Can monitor each component
- **Maintainable**: Clear separation of concerns
