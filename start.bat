@echo off
REM Launch annotation tool without a console window (double-click this file).
cd /d "%~dp0"

where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0main.py" %*
) else (
    start "" python "%~dp0main.py" %*
)
exit /b 0
