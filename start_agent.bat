@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    py -3 -m venv ".venv"
    if errorlevel 1 goto :error
)

set "PYTHON=.venv\Scripts\python.exe"
"%PYTHON%" -c "import openai, dotenv, rich" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias...
    "%PYTHON%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
    if errorlevel 1 goto :error
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Configure sua chave e o workspace no arquivo .env.
    notepad ".env"
)

"%PYTHON%" "%~dp0deepseek_dev_agent.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto :error_code
exit /b 0

:error
set "EXIT_CODE=1"

:error_code
echo.
echo O agente terminou com erro ^(codigo %EXIT_CODE%^).
echo Execute: start_agent.bat --doctor
pause
exit /b %EXIT_CODE%
