@echo off
title Investment Launcher

cd /d "%~dp0"

echo.
echo  Launcher running from: %CD%
echo.

REM ─ Flask 서버 ─
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found.
    pause
    exit /b 1
)

echo  [1/2] Opening Flask window...
start "Investment Server" cmd /k ".venv\Scripts\python.exe app.py"

REM Flask 부팅 대기
timeout /t 4 /nobreak >nul

REM ─ ngrok 터널 ─
echo  [2/2] Opening ngrok window...
where ngrok >nul 2>&1
if %errorlevel% equ 0 (
    start "ngrok Tunnel" cmd /k "ngrok http 5000"
) else (
    set "NG=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
    if exist "%NG%" (
        start "ngrok Tunnel" cmd /k "%NG% http 5000"
    ) else (
        echo  [WARN] ngrok not found - skipping public tunnel.
    )
)

echo.
echo  ============================================================
echo   DONE - two new windows should be visible now:
echo     1. Investment Server  (Flask on port 5000)
echo     2. ngrok Tunnel       (Forwarding URL inside)
echo.
echo   Local:  http://127.0.0.1:5000
echo  ============================================================
echo.
echo  Press any key to close this launcher (others keep running).
pause >nul
