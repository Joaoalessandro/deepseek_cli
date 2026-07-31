@echo off
setlocal
cd /d "%~dp0"
call "%~dp0start_agent.bat" --doctor
