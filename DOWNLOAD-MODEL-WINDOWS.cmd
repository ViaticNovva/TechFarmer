@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 2
echo Compatibility shortcut: in the main menu choose 6 - Prepare optional model.
echo This explicit pinned download follows a real vision-package preflight and confirmation.
echo No user images are sent. Base no-reference collection needs neither model nor vision packages.
call "%~dp0START-WINDOWS.cmd"
exit /b %ERRORLEVEL%
