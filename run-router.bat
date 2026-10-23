@echo off
rem Start the router. Everything is resolved relative to this file, so the folder can live anywhere.
setlocal
cd /d "%~dp0"
title Local Router (all models, one port)

if not exist registry.json (
    echo registry.json is missing. Copy registry.example.json to registry.json and edit it first.
    pause
    exit /b 1
)
if not exist logs mkdir logs

rem Point this at any Python 3.9+ that can import the stdlib only - the proxy has no dependencies.
set "PYTHON=python"

"%PYTHON%" "%~dp0proxy.py"
pause
