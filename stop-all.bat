@echo off
setlocal
title Stop Local LLM Stack
cd /d "%~dp0"

echo.
echo  Surgical shutdown of this router's stack only:
echo     proxy.py                (the front door port from registry.json)
echo     the backends it spawned (their own processes, matched by command line)
echo.
echo  NOT touched: unrelated python jobs, ollama, browsers, IDEs, Docker.
echo  Add -Report to list what would be stopped without stopping it:
echo     stop-all.bat -Report
echo.
echo  Stuck on an error message instead? GETTING-STARTED.md, section 6, matches the
echo  exact words the router prints to the thing to do about it.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1" %*

echo.
pause
endlocal
