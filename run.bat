@echo off
REM One-command launcher for the Stripe Docs Assistant (Windows).
REM Requires only Docker Desktop — everything else runs inside containers.

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker is required but not installed.
  echo Install Docker Desktop: https://docs.docker.com/get-docker/
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker is installed but the daemon isn't running.
  echo Start Docker Desktop, wait for it to finish starting, then re-run run.bat
  exit /b 1
)

echo Building and starting the Stripe Docs Assistant...
echo  - First run downloads ~2GB of local AI models - this can take a few minutes.
echo  - When it's up, open  http://localhost:5055/  and click 'Load Stripe docs'.
echo  - Press Ctrl+C to stop.
echo.

docker compose up --build
