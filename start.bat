@echo off
echo Stopping processes on port 8000...

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo Killing PID %%a
    taskkill /F /PID %%a >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo Starting app.py...
cd /d "%~dp0"
python app.py
