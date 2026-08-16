@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Job Radar

if not exist .venv (
  echo Not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
if not exist .env (
  echo No settings file. Run setup.bat first.
  pause
  exit /b 1
)

.venv\Scripts\python run.py cycle
echo.
pause
