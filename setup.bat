@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Job Radar - Setup

echo ============================================================
echo   JOB RADAR - ONE TIME SETUP
echo ============================================================
echo.

REM ---------- 1. Python present? ----------
python --version >nul 2>&1
if errorlevel 1 (
  echo [X] Python is not installed, or not on your PATH.
  echo.
  echo     Install it from https://www.python.org/downloads/
  echo     IMPORTANT: tick "Add python.exe to PATH" on the first screen.
  echo     Then close this window and run setup.bat again.
  echo.
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/5] Python %PYVER% found.
echo.

REM ---------- 2. Virtual environment ----------
if not exist .venv (
  echo [2/5] Creating a private Python environment ^(one minute^)...
  python -m venv .venv
  if errorlevel 1 (
    echo [X] Could not create the environment. Is Python 3.10 or newer?
    pause
    exit /b 1
  )
) else (
  echo [2/5] Environment already exists, skipping.
)
echo.

REM ---------- 3. Dependencies ----------
echo [3/5] Installing libraries ^(two to three minutes, lots of scrolling^)...
.venv\Scripts\python -m pip install --upgrade pip --quiet
.venv\Scripts\pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo [X] Install failed. Scroll up for the error and send it over.
  pause
  exit /b 1
)
echo      Done.
echo.

REM ---------- 4. Settings file ----------
if not exist .env (
  copy /y ".env.example" ".env" >nul
  echo [4/5] Created your settings file.
  echo.
  echo      Notepad is about to open it. Fill in these four values:
  echo.
  echo        TG_API_ID and TG_API_HASH  ^-  https://my.telegram.org
  echo                                      Log in, "API development tools",
  echo                                      create an app, copy both values.
  echo        BOT_TOKEN                  ^-  Telegram: message @BotFather, send
  echo                                      /newbot, pick any name, copy the token.
  echo        MY_USER_ID                 ^-  Telegram: message @userinfobot, it
  echo                                      replies with your numeric Id.
  echo        ANTHROPIC_API_KEY          ^-  https://console.anthropic.com
  echo.
  echo      Leave CHANNELS alone for now. Save and close Notepad to continue.
  echo.
  pause
  notepad .env
) else (
  echo [4/5] Settings file already exists, skipping.
)
echo.

REM ---------- 5. Channel list ----------
echo [5/5] Now listing your Telegram channels.
echo.
echo      Telegram will ask for your phone number ^(with country code, e.g.
echo      +919876543210^) and then a login code it sends to your Telegram app.
echo      This happens once. If you have 2FA on, it also asks your password.
echo.
pause
.venv\Scripts\python run.py channels
if errorlevel 1 (
  echo.
  echo [X] That failed. Most likely a wrong TG_API_ID or TG_API_HASH in .env.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   ALMOST THERE - two manual bits left
echo ============================================================
echo.
echo   A^) Copy the @usernames of the job channels from the list above
echo      into the CHANNELS= line of .env, separated by commas.
echo      Example:  CHANNELS=@jobsindia,@uaejobs,@startuphiring
echo.
echo   B^) Fill in profile.yaml - your email, phone, LinkedIn. This file
echo      decides which jobs reach you, so it is worth ten real minutes.
echo.
echo   Also: drop your CV at  assets\cv.pdf
echo   Also: open Telegram, find your new bot, and send it  /start
echo         ^(Telegram blocks bots from messaging you until you do^)
echo.
echo   Opening both files now. Save and close them, then run  runme.bat
echo.
pause
notepad .env
notepad profile.yaml
echo.
echo Setup complete. From now on, just double-click runme.bat
pause
