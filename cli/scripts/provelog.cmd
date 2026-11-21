@echo off
setlocal
rem ProveLog launcher: a text file, not a Windows PE. This keeps the CLI usable
rem on machines where an unsigned provelog.exe is blocked by App Control.
if exist "%~dp0python.exe" (
  "%~dp0python.exe" -m provelog %*
) else if exist "%~dp0python3.exe" (
  "%~dp0python3.exe" -m provelog %*
) else (
  python -m provelog %*
)
