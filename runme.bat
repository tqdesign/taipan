@echo off
rem Taipan! - start the server and open the game in the default browser.
cd /d "%~dp0"

echo Starting Taipan! server on http://127.0.0.1:8000 ...
echo (Press Ctrl+C in this window to stop the game.)

rem Open the browser once the server has had a moment to start.
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8000"

uv run main.py
