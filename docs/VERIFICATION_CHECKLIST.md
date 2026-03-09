# TaskMaster AI - Verification Checklist

## Pre-Launch Verification

Use this checklist to ensure everything is properly installed and configured before launching.

### ✅ Python Environment

- [ ] Python 3.8+ installed: `python --version`
- [ ] Virtual environment activated: `.\venv\Scripts\activate` (Windows)
- [ ] Dependencies installed: `pip install -r requirements.txt`

### ✅ Project Structure

Verify these files exist:

**Core Files:**
- [ ] `app/main.py` - FastAPI application
- [ ] `app/core/config.py` - Settings management
- [ ] `app/core/ollama_client.py` - LLM client
- [ ] `app/services/task_generator.py` - Task generation service
- [ ] `app/api/routes/task_generation.py` - Generation endpoint

**Configuration:**
- [ ] `.env` - Environment variables configured
- [ ] `requirements.txt` - Updated with dependencies

**Documentation:**
- [ ] `TASK_GENERATION_GUIDE.md` - Full documentation
- [ ] `QUICK_START.md` - Quick setup guide
- [ ] `IMPLEMENTATION_SUMMARY.md` - Implementation details

### ✅ Ollama Setup

- [ ] Ollama installed from ollama.ai
- [ ] Ollama server running: `ollama serve`
- [ ] Model pulled: `ollama pull mistral`
- [ ] Verify model: `ollama list`

Expected output shows:
```
NAME            ID              SIZE    MODIFIED
mistral:latest  2dfb...         4.1 GB  2 days ago
```

### ✅ Environment Configuration

Check `.env` contains:
```env
OLLAMA_URL=http://localhost:11434
MODEL_NAME=mistral
BACKEND_URL=http://127.0.0.1:8000
```

### ✅ Backend API

- [ ] Backend running at `http://127.0.0.1:8000`
- [ ] Backend has `GET /get-tasks` endpoint
- [ ] Test with: `curl http://127.0.0.1:8000/get-tasks`

### ✅ FastAPI Server

Start the server:
```bash
uvicorn app.main:app --reload
```

Verify startup shows:
```
INFO:     Will watch for changes in these directories: [...]
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

- [ ] Server running on http://localhost:8000
- [ ] Health check: `curl http://localhost:8000/health`
- [ ] API docs available: http://localhost:8000/docs

## API Tests

### Test 1: Health Check
```bash
curl http://localhost:8000/health
```
Expected response: `{"status":"ok"}`

### Test 2: Get Tasks
```bash
curl http://localhost:8000/get-tasks
```
Expected response: `[]` (empty list) or list of tasks

### Test 3: Generate Tasks (Homework)
```bash
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Physics Homework",
    "category": "homework",
    "description": "Chapter 3 problems 1-20",
    "due_date": "2024-04-01"
  }'
```

- [ ] Request accepted (200-202 response)
- [ ] Response includes: main_task, generated_tasks, message
- [ ] Generated tasks have due_dates spread before deadline
- [ ] No errors in server logs

### Test 4: Try Each Category
Run generation with each category and verify:

- [ ] **Homework**: Tasks spread timeline before deadline
- [ ] **Project**: Multiple phases included
- [ ] **Test**: Study schedule with review sessions
- [ ] **Interview**: Prep tasks included
- [ ] **Skill**: Progressive learning path

## Performance Checks

### First Request (Model Warm-up)
- Expected time: 30-120 seconds
- This is normal - model is initializing
- Subsequent requests are faster

### Ollama Resource Usage
- Monitor with: `ollama ps` (shows running models)
- Check GPU: `ollama stats` (if GPU-enabled)
- Should see ~4GB VRAM allocated

## Error Troubleshooting

### Error: "Connection refused" (Ollama)
```
ConnectionError: Failed to connect to http://localhost:11434
```
**Solution:**
- Run `ollama serve` in a separate terminal
- Verify OLLAMA_URL in `.env`

### Error: "Model not found"
```
Error: model 'mistral' not found
```
**Solution:**
- Run: `ollama pull mistral`
- Wait for download to complete (~5 min)
- Verify: `ollama list`

### Error: "Backend not accessible"
```
Error: Failed to fetch tasks from backend
```
**Solution:**
- Ensure backend API running at `http://127.0.0.1:8000`
- Check backend has `/get-tasks` endpoint
- Verify BACKEND_URL in `.env`

### Error: "Failed to parse JSON"
```
Error: No JSON found in response
```
**Solution:**
- Try different model: `ollama pull llama2`
- Or: `ollama pull neural-chat`
- Update `MODEL_NAME` in `.env`
- Restart app

## Production Checklist

Before deploying to production:

- [ ] Environment variables properly set via secrets manager
- [ ] Ollama running as service (not manual process)
- [ ] Error logging configured
- [ ] Request validation in place
- [ ] Rate limiting implemented
- [ ] Generated tasks saved to database
- [ ] User authentication/authorization added
- [ ] API documentation up to date
- [ ] Load testing completed
- [ ] Database backups configured

## Useful Commands

### Development
```bash
# Start development server with auto-reload
uvicorn app.main:app --reload

# Manual request with all parameters
curl -X POST http://localhost:8000/generate-tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "My Goal",
    "category": "project",
    "description": "Full details",
    "due_date": "2024-05-15",
    "due_time": "17:30"
  }'

# Format JSON response nicely
curl ... | python -m json.tool
```

### Ollama Management
```bash
# Start server
ollama serve

# Pull models
ollama pull mistral
ollama pull llama2
ollama pull neural-chat

# List installed models
ollama list

# Remove model
ollama rm mistral

# Show running models
ollama ps

# Copy model to new name
ollama cp mistral my-custom
```

### Debugging
```bash
# View Ollama logs (if service installed)
# Windows: Event Viewer → Application
# Mac: Console.app → System Output
# Linux: journalctl -u ollama

# Test backend connectivity
curl http://127.0.0.1:8000/get-tasks

# Test Ollama connectivity
curl http://localhost:11434/api/tags
```

## Performance Optimization

### Faster Inference
```bash
# Use faster models
ollama pull neural-chat    # ~5 min
ollama pull orca-mini       # ~2-3 min

# Update .env
MODEL_NAME=neural-chat
```

### Better Responses
```bash
# Try more capable models
ollama pull llama2           # Better context understanding
ollama pull mistral:instruct # Better instruction following
```

### System Resources
- Ensure 8GB+ RAM available
- GPU recommended (4GB VRAM)
- Fast SSD for model caching
- Stable internet for first pull

## Next Steps After Verification

1. **Custom Configuration**
   - Adjust prompts in `task_generator.py`
   - Modify temperature for creativity
   - Add new task categories

2. **Frontend Integration**
   - Connect `/generate-tasks` endpoint to UI
   - Display generated tasks to user
   - Allow task customization before saving

3. **Database Integration**
   - Save generated tasks to database
   - Implement edit/delete functionality
   - Track generation history

4. **Advanced Features**
   - User preferences storage
   - Scheduling conflicts detection
   - Multi-user task coordination
   - Analytics/reporting

## Support Contacts

For issues:
- Check `TASK_GENERATION_GUIDE.md` for detailed explanations
- Review `QUICK_START.md` for setup help
- API documentation: http://localhost:8000/docs
- Check server logs for error details

---

✅ **All checks passing?** Your TaskMaster AI is ready to go! 🚀
