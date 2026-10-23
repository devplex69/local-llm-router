@echo off
rem Start the router. Everything is resolved relative to this file, so the folder can live anywhere.
setlocal
cd /d "%~dp0"
title Local Router (all models, one port)

if not exist registry.json (
    echo registry.json is missing. Copy registry.example.json to registry.json and edit it first.
    echo    GETTING-STARTED.md, section 4, walks through that - it is about five fields.
    pause
    exit /b 1
)
if not exist logs mkdir logs

rem Point this at any Python 3.9+ that can import the stdlib only - the proxy has no dependencies.
set "PYTHON=python"
where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from python.org and tick "Add python.exe to your PATH",
    echo    then close and reopen this window. GETTING-STARTED.md, section 2.
    pause
    exit /b 1
)

echo  ============================================================
echo    Local LLM router is starting.
echo    Open the address it prints on the next line, in your browser.
echo    By default that is   http://127.0.0.1:8090   - the top-level
echo    "port" in registry.json is what sets it, so read the printed line.
echo    Leave this window open - it IS the router. Closing it stops the router.
echo    First model you pick takes a while: that is the load, not a hang.
echo    Trouble? GETTING-STARTED.md, section 6, matches the error you can see.
echo  ============================================================
echo.

"%PYTHON%" "%~dp0proxy.py"
echo.
echo  The router stopped. If that was not the end of your session, read the few lines
echo  above this message - they name the reason. The three common ones:
echo    "Expecting ',' delimiter"       - two lines in registry.json are missing a comma.
echo    "Illegal trailing comma"        - a comma after the last item in a list.
echo    "Expecting property name"       - a comma after the last item in a block, or
echo                                      straight quotes missing around a name.
echo    Any of those three = a JSON slip. Run "python proxy.py" in a cmd window here and
echo      it prints the line and column. GETTING-STARTED.md, section 6, has the rest.
echo    "port is already taken by" - another program has that engine's port; close it
echo      or move that backend to a free port in registry.json (both places at once).
echo.
pause
