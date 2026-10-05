<#
.SYNOPSIS
    Gera um .zip do pdf2md para levar a outro computador.

.DESCRIPTION
    Copiar a pasta inteira nao funciona: o ambiente virtual (.venv) grava
    dentro de si o caminho absoluto desta maquina, e nao roda em outra. Ele
    tambem responde por quase 90% do tamanho.

    Este script empacota so o que e portatil. No computador de destino, o
    instalar.ps1 reconstroi o resto.

.PARAMETER Destino
    Onde gravar o .zip. Padrao: a Area de Trabalho.

.PARAMETER ComModelosOcr
    Inclui os modelos de OCR (~18 MB). Sem isso o instalador os baixa da
    internet no destino. Use quando o outro computador nao tiver rede boa.

.EXAMPLE
    .\empacotar.ps1

.EXAMPLE
    .\empacotar.ps1 -ComModelosOcr -Destino "E:\"
#>
param(
    [string]$Destino = [Environment]::GetFolderPath("Desktop"),
    [switch]$ComModelosOcr
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# O que NAO viaja, e por que.
$excluir = @(
    ".venv",            # caminho absoluto gravado dentro; 340 MB
    "saida",            # resultados de conversao — podem conter documento de cliente
    "saida_teste",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    ".env"              # configuracao local da maquina
)
if (-not $ComModelosOcr) {
    $excluir += "tessdata"
}

$temporario = Join-Path $env:TEMP "pdf2md-pacote-$(Get-Random)"
New-Item -ItemType Directory -Force $temporario | Out-Null
$alvo = Join-Path $temporario "pdf2md"
New-Item -ItemType Directory -Force $alvo | Out-Null

Write-Host ""
Write-Host "  Montando o pacote..." -ForegroundColor Cyan

$copiados = 0
Get-ChildItem -Recurse -Force -File | ForEach-Object {
    $relativo = $_.FullName.Substring($PSScriptRoot.Length + 1)
    $partes = $relativo -split '\\'

    # Fixtures de teste sao PDFs sinteticos e sao regeradas no destino.
    if ($relativo -like "tests\fixtures\*") { return }
    foreach ($padrao in $excluir) {
        if ($partes -contains $padrao) { return }
    }

    $destinoArquivo = Join-Path $alvo $relativo
    New-Item -ItemType Directory -Force (Split-Path $destinoArquivo) | Out-Null
    Copy-Item $_.FullName $destinoArquivo
    $script:copiados++
}

# A versão vem do pyproject.toml — a mesma que a interface e a API exibem.
# É o nome que a Release do GitHub espera, e o que permite saber, olhando o
# arquivo, qual versão está instalada em cada máquina.
$versao = (Select-String -Path ".\pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches.Groups[1].Value
if (-not $versao) {
    Write-Host "  Versao nao encontrada no pyproject.toml." -ForegroundColor Red
    exit 1
}
$nome = "pdf2md-v$versao.zip"
$caminhoZip = Join-Path $Destino $nome
if (Test-Path $caminhoZip) { Remove-Item $caminhoZip -Force }

Compress-Archive -Path $alvo -DestinationPath $caminhoZip -CompressionLevel Optimal
Remove-Item -Recurse -Force $temporario

$mb = [math]::Round((Get-Item $caminhoZip).Length / 1MB, 1)

Write-Host ""
Write-Host "  Pacote pronto:" -ForegroundColor Green
Write-Host "      $caminhoZip" -ForegroundColor White
Write-Host "      $copiados arquivos, $mb MB" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  No outro computador:" -ForegroundColor White
Write-Host "      1. Descompacte o .zip" -ForegroundColor Gray
Write-Host "      2. Abra a pasta pdf2md" -ForegroundColor Gray
Write-Host "      3. Clique com o botao direito em instalar.ps1" -ForegroundColor Gray
Write-Host "         e escolha 'Executar com o PowerShell'" -ForegroundColor Gray
Write-Host "      4. Depois, duplo clique em iniciar.cmd" -ForegroundColor Gray
Write-Host ""
if (-not $ComModelosOcr) {
    Write-Host "  O instalador baixa os modelos de OCR (18 MB) no destino." -ForegroundColor DarkGray
    Write-Host "  Se la nao houver internet, refaca com:  .\empacotar.ps1 -ComModelosOcr" -ForegroundColor DarkGray
    Write-Host ""
}
