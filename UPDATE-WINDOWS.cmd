@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_failed
if not exist "tools\update.py" goto updater_missing
if exist ".venv\Scripts\python.exe" goto try_venv
:try_py315
py -3.15 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py314
py -3.15 -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_py314
py -3.14 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py313
py -3.14 -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_py313
py -3.13 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py312
py -3.13 -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_py312
py -3.12 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py311
py -3.12 -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_py311
py -3.11 -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py -3.11 -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_python
python -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
python -I -X utf8 "%~dp0tools\update.py"
goto finished
:try_venv
".venv\Scripts\python.exe" -I -c "import sys,sysconfig; raise SystemExit(0 if sys.implementation.name == 'cpython' and (3,11) <= sys.version_info[:2] <= (3,15) and sys.version_info.releaselevel == 'final' and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto try_py315
".venv\Scripts\python.exe" -I -X utf8 "%~dp0tools\update.py"
:finished
set "TF_EXIT=%ERRORLEVEL%"
echo.
echo This updates SOURCE only. Settings, jobs, references, exports and model cache stay local.
echo Reopen START-WINDOWS.cmd after updates, choose Setup/repair base, then Start bot.
echo A dependency sync failure does not roll back Python packages. No model refresh is automatic.
echo If a source update was interrupted, select Roll back before starting TechFarmer.
pause
exit /b %TF_EXIT%
:updater_missing
echo The local updater is missing. For FIRST setup, copy the reviewed tools\update.py
echo and UPDATE-WINDOWS.cmd from the complete canonical ViaticNovva/TechFarmer source.
echo Keep your old SOURCE-MANIFEST.sha256, settings, jobs, references, cache and .venv.
echo Stop EVERY older bot window before the first upgrade; its process has no new lock.
echo Never use a documentation-only download as a complete application.
goto failed
:python_missing
echo Stable, final standard CPython 3.11-3.15 is required. Existing .venv is tried first, then 3.15 downward.
echo This independent updater never recreates or changes the interpreter of your .venv.
echo Use your approved Python installation; no administrator or policy workaround is requested.
goto failed
:folder_failed
echo The updater could not enter its own local project folder. Move the intact project
echo to an approved local folder; do not run from a ZIP preview or unsupported network path.
:failed
pause
exit /b 2
