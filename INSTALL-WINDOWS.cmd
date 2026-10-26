@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 2
echo Compatibility shortcut: in the main menu choose 1 - Setup/repair base.
echo START-WINDOWS.cmd is now the one entrypoint for setup, configuration and starting.
call "%~dp0START-WINDOWS.cmd"
exit /b %ERRORLEVEL%
