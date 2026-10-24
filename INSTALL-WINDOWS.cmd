@echo off
setlocal
cd /d "%~dp0"
echo TechFarmer 0.2 - install base dependencies into this folder only
set "TF_PYTHON=py -3.12"
%TF_PYTHON% -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto python_ready
set "TF_PYTHON=py -3.11"
%TF_PYTHON% -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto python_ready
set "TF_PYTHON=python"
%TF_PYTHON% -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
if not errorlevel 1 goto python_ready
echo Install trusted Python 3.11 or 3.12, then retry. Newer Python is not a verified vision combination here.
pause
exit /b 1
:python_ready
if exist ".venv\Scripts\python.exe" goto install_packages
%TF_PYTHON% -m venv .venv
if errorlevel 1 goto failed
:install_packages
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,12) else 1)"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
echo Base install complete. Next: INSTALL-VISION-WINDOWS.cmd, then DOWNLOAD-MODEL-WINDOWS.cmd.
pause
exit /b 0
:failed
echo Installation failed. Check Python and your configured package-index access.
echo No administrator permission or policy, proxy, DNS or registry workaround is requested. Stop and get approval if blocked.
pause
exit /b 1
