@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run INSTALL-WINDOWS.cmd and INSTALL-VISION-WINDOWS.cmd first.
  pause
  exit /b 1
)
echo Explicit pinned model download from Hugging Face. No user images are sent.
".venv\Scripts\python.exe" -m tech_farmer download-model
if errorlevel 1 (
  echo Model preparation failed. Run doctor and read README. No dummy fallback is used.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m tech_farmer doctor
set "TF_EXIT=%ERRORLEVEL%"
pause
exit /b %TF_EXIT%
