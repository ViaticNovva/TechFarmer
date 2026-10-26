@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 2
echo Compatibility shortcut: in the main menu choose 5 - Install optional matching packages.
echo Base page collection needs no vision packages. The menu reports your ACTUAL Python version.
echo Standard Python 3.15 has no reviewed stable Torch wheel; no silent older-Python fallback.
call "%~dp0START-WINDOWS.cmd"
exit /b %ERRORLEVEL%
