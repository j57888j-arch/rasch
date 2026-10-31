@echo off
setlocal
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if not exist .env copy .env.example .env
echo.
echo Setup finished. Edit .env and set BOT_TOKEN, ADMIN_IDS, DATABASE_URL.
echo Run run_local.bat to start the bot locally.
pause
