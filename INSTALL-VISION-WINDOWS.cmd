@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run INSTALL-WINDOWS.cmd first.
  pause
  exit /b 1
)
echo Explicit vision dependency installation. Torch packages can be large.
echo CPU is the default. GPU support depends on your compatible local Torch and hardware.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-vision.txt
if errorlevel 1 (
  echo Installation failed. Do not alter device policies, proxy, DNS or package registries to bypass a restriction.
  pause
  exit /b 1
)
echo Next run DOWNLOAD-MODEL-WINDOWS.cmd. No model files or images were uploaded by this installer.
pause
exit /b 0
