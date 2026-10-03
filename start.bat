@echo off
rem Zapusk bota na Windows: dvojnoj shhelchok po etomu fajlu.
cd /d "%~dp0"

where python >nul 2>nul || (
  echo Python not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)

if exist .env goto install
echo.
set /p TOKEN="Paste your bot token from @BotFather (right click = paste) and press Enter: "
>.env echo BOT_TOKEN=%TOKEN%

:install
echo Installing dependencies...
python -m pip install --disable-pip-version-check -q -r requirements.txt || (
  echo Failed to install dependencies, see the error above.
  pause
  exit /b 1
)

echo.
echo Bot is running. Do not close this window. Press Ctrl+C to stop.
python -m ispu_bot
pause
