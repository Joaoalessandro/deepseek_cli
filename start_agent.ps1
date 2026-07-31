$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Criando ambiente virtual..."
    py -3 -m venv (Join-Path $PSScriptRoot ".venv")
}

& $python -c "import openai, dotenv, rich" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando dependencias..."
    & $python -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt")
}

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $PSScriptRoot ".env.example") $envFile
    Write-Host "Configure sua chave e o workspace no arquivo .env."
    notepad $envFile
}

& $python (Join-Path $PSScriptRoot "deepseek_dev_agent.py") @args
exit $LASTEXITCODE
