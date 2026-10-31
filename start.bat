@echo off
title Flonno 2API Gateway (:8097)
cd /d "%~dp0"

echo ========================================================
echo [FLONNO 2API] Starting Autonomous Qwen3.8-27B Gateway
echo Port: 8097
echo ========================================================

py -3.12 server.py
if %ERRORLEVEL% NEQ 0 (
    python server.py
)
pause
