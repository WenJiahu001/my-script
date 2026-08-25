@echo off
cd /d "%~dp0"
python "%~dp0install.py"
if %ERRORLEVEL% NEQ 0 (
    pause
)
