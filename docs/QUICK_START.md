# TaskMaster AI - Quick Start Guide

## 5-Minute Setup

### Prerequisites
- Python 3.8+
- Already have a backend API running at `http://127.0.0.1:8000`

### Step 1: Install Dependencies
```bash
cd c:\Users\17602\documents\sdsu\projects\TaskMaster\TaskMaster-AI
pip install -r requirements.txt
```

### Step 2: Install & Start Ollama

**Windows:**
1. Download from https://ollama.ai
2. Run the installer
3. In a terminal: `ollama serve`

**In another terminal, pull a model:**
```bash
ollama pull mistral
```

> Note: First pull takes 5-10 minutes (downloads ~4GB model)

### Step 3: Start FastAPI Server

```bash
uvicorn app.main:app --reload
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Step 4: Test It!

Open your browser or use curl:

```bash
# Check health
curl http://localhost:8000/health

# Generate tasks for a homework assignment
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Complete Physics homework chapter 3",
    "category": "homework",
    "description": "Problems 1-30 from textbook",
    "due_date": "2024-03-20",
    "due_time": "23:59"
  }'
```

## Common Issues & Fixes

### Issue: Connection refused (OLLAMA)
```
Error: ConnectionError: Failed to connect to http://localhost:11434
```

**Fix:**
1. Make sure Ollama is installed and running: `ollama serve`
2. Check `.env` has `OLLAMA_URL=http://localhost:11434`

### Issue: Model not found
```
Error: "Failed to generate tasks"
```

**Fix:**
1. Pull a model: `ollama pull mistral`
2. Check `.env` has `MODEL_NAME=mistral`

### Issue: Backend not accessible
```
Error: Failed to fetch tasks from backend
```

**Fix:**
1. Start your backend API at `http://127.0.0.1:8000`
2. Ensure it has a `GET /get-tasks` endpoint
3. Update `.env` BACKEND_URL if needed

## Testing Different Categories

### Homework
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "CS 101 Homework",
    "category": "homework",
    "due_date": "2024-03-22"
  }'
```

### Project
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Build weather app",
    "category": "project",
    "description": "Python GUI application with weather API",
    "due_date": "2024-04-15"
  }'
```

### Test
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Midterm Exam",
    "category": "test",
    "description": "Calculus - Chapters 1-5",
    "due_date": "2024-03-25",
    "due_time": "14:00"
  }'
```

### Interview
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Google Engineering Interview",
    "category": "interview",
    "description": "Full-stack position",
    "due_date": "2024-04-01"
  }'
```

### Skill
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Learn React",
    "category": "skill",
    "description": "Complete React fundamentals to advanced",
    "due_date": "2024-06-30"
  }'
```

## API Documentation

Once the server is running, visit:
```
http://localhost:8000/docs
```

This opens interactive Swagger UI where you can:
- See all available endpoints
- Try requests directly
- View response schemas
- Auto-generate request payloads

## Next Steps

1. **Customize models**: Try different Ollama models:
   ```bash
   ollama pull llama2
   ollama pull neural-chat
   ollama pull orca-mini
   ```
   Update `MODEL_NAME` in `.env`

2. **Connect to frontend**: Integrate with your React/Vue app
   - Use the `/generate-tasks` endpoint
   - Display generated tasks to user
   - Allow editing/customization before saving

3. **Persist generated tasks**: Currently returns tasks but doesn't save them
   - POST generated tasks to your backend
   - Track which tasks came from AI generation
   - Allow users to modify before committing

4. **Feedback loop**: Track which generated plans users follow
   - Improve LLM prompts based on success patterns
   - Store generation metadata for analytics

## Performance Tips

- **Faster inference**: Use smaller model
  ```bash
  ollama pull neural-chat:latest  # ~5 min inference time
  ollama pull orca-mini            # ~2 min inference time
  ```

- **GPU acceleration**: Ollama auto-uses GPU if available
  - NVIDIA: Install CUDA
  - Mac: M1/M2/M3 automatic

- **Cache warmup**: First request is slower; subsequent ones faster

## File Structure

```
TaskMaster-AI/
├── .env                           # Configuration
├── requirements.txt               # Dependencies
├── TASK_GENERATION_GUIDE.md      # Full documentation
├── QUICK_START.md                # This file
├── app/
│   ├── main.py                    # FastAPI app
│   ├── core/
│   │   ├── config.py             # Settings
│   │   └── ollama_client.py      # LLM client
│   ├── services/
│   │   └── task_generator.py     # Task generation logic
│   ├── schemas/
│   │   ├── task_schema.py        # Data models
│   │   └── tag_schema.py         # Tag model
│   └── api/routes/
│       ├── tasks_router.py       # Basic endpoints
│       └── task_generation.py    # AI endpoint
```

## Support

For detailed explanations, see: `TASK_GENERATION_GUIDE.md`
