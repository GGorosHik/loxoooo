@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title ModConnect Bot
cd /d "%~dp0"

echo === ModConnect Bot launcher ===
echo.

python --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  echo Install Python 3.11+ from python.org, enable "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".env" (
  echo [SETUP] .env not found - creating it and opening Notepad.
  echo         Fill BOT_TOKEN and ADMIN_ID, then SAVE and CLOSE Notepad.
  copy /y ".env.example" ".env" >nul
  start "" /wait notepad ".env"
  echo.
)

python -c "import config" >nul 2>nul
if errorlevel 1 (
  echo [SETUP] .env is incomplete - exact reason is printed above.
  echo         Opening Notepad one more time.
  start "" /wait notepad ".env"
  python -c "import config" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] .env is still incomplete. Fill BOT_TOKEN and ADMIN_ID, save, run again.
    pause
    exit /b 1
  )
  echo.
)

python -c "import aiogram, websockets, aiohttp_socks" >nul 2>nul
if errorlevel 1 (
  echo [DEPS] installing aiogram, websockets, aiohttp-socks...
  python -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip failed - see output above.
    pause
    exit /b 1
  )
  echo.
)

set /a fails=0
:run
python main.py
if errorlevel 1 (
  set /a fails+=1
  if %fails% GEQ 5 goto dead
  echo.
  echo [ModConnect] restart in 10 sec... run 5 crash limit. Ctrl+C to stop.
  timeout /t 10 /nobreak >nul
  goto run
)
echo.
echo [ModConnect] bot stopped normally.
pause
exit /b 0

:dead
echo [ERROR] 5 crashes in a row - stopped. Read the log above.
pause
exit /b 1
