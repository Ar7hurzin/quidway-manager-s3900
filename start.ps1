$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Test-Path 'local-settings.ps1') { . (Join-Path $PSScriptRoot 'local-settings.ps1') }
if (-not $env:QM_AUTH_USERNAME -or -not $env:QM_AUTH_PASSWORD_HASH -or -not $env:QM_SECRET_KEY) {
    throw 'Configure o acesso antes de iniciar: execute .\configure-login.ps1 e depois .\start.ps1.'
}

$port = 8787
if ($env:QM_WEB_PORT) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:QM_WEB_PORT, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw 'QM_WEB_PORT deve ser uma porta entre 1 e 65535.'
    }
    $port = $parsedPort
}
$url = "http://127.0.0.1:$port"

# If the app is already running, reuse it instead of starting a second server.
$health = $null
try { $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2 } catch { }
if ($health) {
    if ($health.service -ne 'Quidway Manager') { throw 'A porta configurada está sendo usada por outro serviço.' }
    if ($health.auth_required -ne $true) { throw 'Há uma versão antiga do Quidway Manager sem login nesta porta. Encerre-a com Ctrl+C antes de iniciar esta versão protegida.' }
    Write-Host "Quidway Manager já está ativo em $url. Abra esse endereço no navegador." -ForegroundColor Cyan
    return
}

if (-not (Test-Path '.venv/Scripts/python.exe')) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) { throw 'Instale Python 3.11 ou mais recente e tente novamente.' }
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível criar o ambiente Python.' }
}

$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -c 'import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(x) for x in ("flask","paramiko","waitress","serial")) else 1)' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Preparando as bibliotecas do Quidway Manager...' -ForegroundColor Cyan
    & $python -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível preparar as bibliotecas. Confira sua conexão e tente novamente.' }
}

if (-not $env:QM_MODE) { $env:QM_MODE = 'demo' }
$env:QM_WEB_PORT = "$port"

Write-Host "Quidway Manager será iniciado em $url. Para sair, pressione Ctrl+C." -ForegroundColor Cyan
Write-Host "Depois de iniciado, abra $url no navegador." -ForegroundColor Cyan
& $python app.py
