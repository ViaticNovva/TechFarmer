@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_failed
if not exist "tech_farmer\__main__.py" goto source_missing
if not exist "SOURCE-MANIFEST.sha256" goto source_missing
if exist ".venv\Scripts\python.exe" goto try_venv
if exist ".venv" goto venv_failed
:try_py315
py -3.15 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py314
py -3.15 -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_py314
py -3.14 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py313
py -3.14 -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_py313
py -3.13 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py312
py -3.13 -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_py312
py -3.12 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py311
py -3.12 -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_py311
py -3.11 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py -3.11 -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_python
python -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
python -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
goto finished
:try_venv
".venv\Scripts\python.exe" -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto venv_failed
".venv\Scripts\python.exe" -I -X utf8 "%~dp0tech_farmer\__main__.py" menu
:finished
set "TF_EXIT=%ERRORLEVEL%"
if "%TF_EXIT%"=="0" exit /b 0
echo Local action failed. Read the message above and README. Settings/data/.venv were kept.
pause
exit /b %TF_EXIT%
:venv_failed
echo The existing .venv is broken or outside stable, final standard CPython 3.11-3.15. It was NOT rebuilt.
echo Preserve the whole folder. Read README: Choose Python 3.15, or use UPDATE-WINDOWS.cmd for source recovery.
echo Do not copy a .venv between locations, delete user data or bypass device policy.
goto failed
:source_missing
echo This folder is incomplete. Obtain the COMPLETE reviewed ViaticNovva/TechFarmer source.
echo A README-only repository or ZIP preview is not an installed application.
goto failed
:python_missing
echo Install approved stable, final standard CPython 3.15, or a supported 3.11-3.14 installation, then retry.
echo A new install prefers 3.15; an existing working .venv keeps its actual version.
echo Optional matching has no reviewed stable 3.15 Torch wheel; base collection needs no model.
goto failed
:folder_failed
echo Could not enter this project's own folder. Extract the complete source first into
echo a local, non-shared, non-synced folder you control. No files are moved automatically.
:failed
pause
exit /b 2
