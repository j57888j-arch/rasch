@echo off
setlocal
if not exist .venv\Scripts\python.exe (
  echo Virtual environment not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
set USE_WEBHOOK=false
uvicorn ai_math.main:app --app-dir src --host 127.0.0.1 --port 8000
