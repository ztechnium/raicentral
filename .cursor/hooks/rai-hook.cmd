@echo off
REM Windows launcher for Cursor hooks (avoids bash shebang failures).
uv run rai-hook %*
if errorlevel 1 (
  echo {"permission":"allow"}
  exit /b 0
)
