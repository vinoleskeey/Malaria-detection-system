# Debug Instrumentation Plan

## Completed Steps

- [x] Read all relevant source files

## Steps to Complete

- [x] **STEP 1**: Create TODO.md
- [ ] **STEP 2**: Edit `routes/chat.py` — Add 16 numbered debug print statements throughout `chat()`
- [ ] **STEP 3**: Edit `routes/chat.py` — Replace `except Exception` block with verbose traceback logging
- [ ] **STEP 4**: Edit `models/chat_models.py` — Replace `flush()` with `commit()` + `refresh()`
- [ ] **STEP 5**: Run static validation `python -m py_compile app.py routes/chat.py models/chat_models.py services/ollama_service.py`

