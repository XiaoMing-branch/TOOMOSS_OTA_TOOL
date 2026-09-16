@echo off
setlocal enabledelayedexpansion

:: Check explicit Python 3.12 path
set "PY=C:\Users\MING\AppData\Local\Programs\Python\Python312\python.exe"
if exist "!PY!" goto :RUN

:: Check LocalAppData
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "!PY!" goto :RUN

:: Check where python and filter out WindowsApps
for /f "tokens=*" %%a in ('where python 2^>nul') do (
    echo "%%a" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
        if exist "%%a" (
            set "PY=%%a"
            goto :RUN
        )
    )
)

:: Try py launcher
where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py"
    goto :RUN
)

echo [ERROR] Python 3 not found.
pause
exit /b 1

:RUN
cd /d "%~dp0"
"!PY!" cli.py --help
echo.
pause
