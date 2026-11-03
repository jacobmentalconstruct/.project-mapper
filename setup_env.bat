@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
echo [SYSTEM] Initialising ProjectMapper environment...

if not exist "%SCRIPT_DIR%\.venv" (
    echo [SYSTEM] Creating .venv...
    py -m venv "%SCRIPT_DIR%\.venv"
)

echo [SYSTEM] Installing dependencies...
"%SCRIPT_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
if exist "%SCRIPT_DIR%\requirements.txt" (
    "%SCRIPT_DIR%\.venv\Scripts\pip" install -r "%SCRIPT_DIR%\requirements.txt"
)
"%SCRIPT_DIR%\.venv\Scripts\pip" install -e "%SCRIPT_DIR%"

echo.
echo [SUCCESS] Environment ready. Launch with run.bat
endlocal
