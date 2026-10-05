<#
.SYNOPSIS
    Sobe o pdf2md no Windows.

.DESCRIPTION
    Na primeira execução cria o ambiente virtual, instala as dependências e
    baixa o modelo de português do OCR. Depois disso, só inicia o servidor.

    A janela precisa continuar aberta: é ela que hospeda o servidor. Fechá-la,
    ou dar Ctrl+C, encerra o serviço — e o navegador passa a mostrar
    "conexão recusada".

.PARAMETER Cli
    Converte um PDF pela linha de comando, sem subir o servidor.

.PARAMETER Porta
    Porta do servidor. Padrão: 8000.

.PARAMETER Abrir
    Abre o navegador assim que o servidor responder.

.EXAMPLE
    .\run.ps1

.EXAMPLE
    .\run.ps1 -Abrir

.EXAMPLE
    .\run.ps1 -Cli "C:\processos\peticao.pdf"
#>
param(
    [string]$Cli = "",
    [int]$Porta = 8000,
    [switch]$Abrir
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"

# ── Primeira execução: ambiente e dependências ──────────────────────────
if (-not (Test-Path $python)) {
    Write-Host "Criando o ambiente virtual (Python 3.12)..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
    } else {
        python -m venv .venv
    }

    Write-Host "Instalando as dependências..." -ForegroundColor Cyan
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r requirements.txt -r requirements-ocr.txt
}

if (-not (Test-Path ".\tessdata\por.traineddata")) {
    Write-Host "Baixando o modelo de português para o OCR..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force .\tessdata | Out-Null
    $base = "https://github.com/tesseract-ocr/tessdata_best/raw/main"
    Invoke-WebRequest "$base/por.traineddata" -OutFile ".\tessdata\por.traineddata"
    Invoke-WebRequest "$base/osd.traineddata" -OutFile ".\tessdata\osd.traineddata"
}

# ── Modo linha de comando ───────────────────────────────────────────────
if ($Cli) {
    & $python -m app.cli $Cli
    exit $LASTEXITCODE
}

# ── Porta ocupada ───────────────────────────────────────────────────────
# É o erro mais comum ao reabrir o programa. Sem esta checagem o uvicorn
# morre com uma mensagem críptica do Winsock, e não fica claro que o serviço
# provavelmente já está no ar.
$ocupada = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if ($ocupada) {
    # $pid é variável reservada do PowerShell; daí o nome próprio.
    $processoId = $ocupada[0].OwningProcess
    $nome = (Get-Process -Id $processoId -ErrorAction SilentlyContinue).ProcessName

    Write-Host ""
    Write-Host "  A porta $Porta ja esta em uso pelo processo $processoId ($nome)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Se for uma instancia anterior do pdf2md, o endereco abaixo ja funciona:" -ForegroundColor Gray
    Write-Host "      http://127.0.0.1:$Porta" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Para encerra-la:      Stop-Process -Id $processoId -Force" -ForegroundColor Gray
    Write-Host "  Ou usar outra porta:  run.ps1 -Porta $($Porta + 1)" -ForegroundColor Gray
    Write-Host ""
    exit 3
}

$env:PDF2MD_PORT = $Porta

# ── Abertura do navegador ───────────────────────────────────────────────
if ($Abrir) {
    # Só depois que o servidor responder: abrir antes mostraria justamente a
    # tela de "conexao recusada" que este script existe para evitar.
    Start-Job -ScriptBlock {
        param($p)
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 400
            try {
                Invoke-RestMethod "http://127.0.0.1:$p/health" -TimeoutSec 2 | Out-Null
                Start-Process "http://127.0.0.1:$p"
                return
            } catch {
                continue
            }
        }
    } -ArgumentList $Porta | Out-Null
}

Write-Host ""
Write-Host "  pdf2md em http://127.0.0.1:$Porta" -ForegroundColor Green
Write-Host "  Deixe esta janela aberta. Ctrl+C encerra o servidor." -ForegroundColor DarkGray
Write-Host ""

& $python -m app.main
