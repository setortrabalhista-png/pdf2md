<#
.SYNOPSIS
    Converte em lote todos os PDFs de uma pasta.

.DESCRIPTION
    Processa um PDF por vez, sequencialmente — conversão é dominada por CPU, e
    paralelizar aqui só faria os processos brigarem pelo processador.

    Ao final, imprime um quadro com o resultado de cada documento e grava um
    índice em JSON. O que importa nesse quadro é a coluna Status: ela diz quais
    documentos podem ser usados direto e quais precisam de conferência antes.

.PARAMETER Pasta
    Onde estão os PDFs. Padrão: a pasta atual.

.PARAMETER Saida
    Onde gravar. Padrão: .\saida

.PARAMETER Recursivo
    Percorre também as subpastas.

.PARAMETER Ocr
    auto (padrão) | force | never

.PARAMETER Paginas
    Recorte aplicado a todos os documentos, ex.: "1-40".

.PARAMETER Continuar
    Pula documentos que já têm resultado na pasta de saída. Útil para retomar
    um lote grande que foi interrompido.

.EXAMPLE
    .\lote.ps1 -Pasta "C:\processos\0001234-56"

.EXAMPLE
    .\lote.ps1 -Pasta "C:\digitalizados" -Ocr force -Saida "D:\convertidos"

.EXAMPLE
    .\lote.ps1 -Pasta "C:\processos" -Recursivo -Continuar
#>
param(
    [string]$Pasta = ".",
    [string]$Saida = ".\saida",
    [switch]$Recursivo,
    [ValidateSet("auto", "force", "never")]
    [string]$Ocr = "auto",
    [string]$Paginas = "",
    [switch]$Continuar
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Ambiente não encontrado. Rode .\run.ps1 uma vez para criá-lo." -ForegroundColor Red
    exit 1
}

$busca = @{ Path = $Pasta; Filter = "*.pdf" }
if ($Recursivo) { $busca["Recurse"] = $true }
$arquivos = @(Get-ChildItem @busca -File | Sort-Object FullName)

if ($arquivos.Count -eq 0) {
    Write-Host "Nenhum PDF encontrado em $Pasta" -ForegroundColor Yellow
    exit 0
}

Write-Host "$($arquivos.Count) PDF(s) para converter" -ForegroundColor Cyan
Write-Host "Saída: $Saida`n" -ForegroundColor Cyan

$resultados = @()
$inicio = Get-Date
$indice = 0

foreach ($arquivo in $arquivos) {
    $indice++
    # Nomes iguais em subpastas diferentes não podem se sobrescrever.
    $nome = $arquivo.BaseName
    $destino = Join-Path $Saida $nome
    $sufixo = 2
    while ((Test-Path $destino) -and -not $Continuar -and
           ($resultados.Destino -contains $destino)) {
        $destino = Join-Path $Saida "$nome-$sufixo"
        $sufixo++
    }

    $relatorio = Join-Path $destino "resultado_processamento.json"

    if ($Continuar -and (Test-Path $relatorio)) {
        Write-Host ("[{0,3}/{1}] {2}  (já convertido, pulando)" -f $indice, $arquivos.Count, $arquivo.Name) -ForegroundColor DarkGray
        $r = Get-Content $relatorio -Raw -Encoding UTF8 | ConvertFrom-Json
        $resultados += [pscustomobject]@{
            Arquivo   = $arquivo.Name
            Status    = $r.resumo.status
            Cobertura = $r.text_accuracy
            Paginas   = $r.documento.paginas
            Tabelas   = $r.tables_detected
            Revisar   = $r.tabelas.para_revisar
            Imagens   = $r.images_extracted
            OCR       = $r.ocr.paginas_com_ocr
            Destino   = $destino
        }
        continue
    }

    Write-Host ("[{0,3}/{1}] {2}" -f $indice, $arquivos.Count, $arquivo.Name) -NoNewline

    $argumentos = @("-m", "app.cli", $arquivo.FullName, "-o", $destino, "--ocr", $Ocr)
    if ($Paginas) { $argumentos += @("--paginas", $Paginas) }

    $cronometro = Get-Date
    & $python @argumentos 2>$null | Out-Null
    $segundos = [math]::Round(((Get-Date) - $cronometro).TotalSeconds, 1)

    if (-not (Test-Path $relatorio)) {
        Write-Host "  FALHOU" -ForegroundColor Red
        $resultados += [pscustomobject]@{
            Arquivo = $arquivo.Name; Status = "falhou"; Cobertura = 0
            Paginas = 0; Tabelas = 0; Revisar = 0; Imagens = 0; OCR = 0
            Destino = $destino
        }
        continue
    }

    $r = Get-Content $relatorio -Raw -Encoding UTF8 | ConvertFrom-Json
    $cor = switch ($r.resumo.status) {
        "ok"       { "Green" }
        "atencao"  { "Yellow" }
        default    { "Red" }
    }
    Write-Host ("  {0}  {1}%  {2}s" -f $r.resumo.status.ToUpper(), $r.text_accuracy, $segundos) -ForegroundColor $cor

    $resultados += [pscustomobject]@{
        Arquivo   = $arquivo.Name
        Status    = $r.resumo.status
        Cobertura = $r.text_accuracy
        Paginas   = $r.documento.paginas
        Tabelas   = $r.tables_detected
        Revisar   = $r.tabelas.para_revisar
        Imagens   = $r.images_extracted
        OCR       = $r.ocr.paginas_com_ocr
        Destino   = $destino
    }
}

$total = [math]::Round(((Get-Date) - $inicio).TotalSeconds, 1)

Write-Host "`n════════ RESUMO ════════`n" -ForegroundColor Cyan
$resultados | Format-Table Arquivo, Status, Cobertura, Paginas, Tabelas, Revisar, Imagens, OCR -AutoSize

$ok       = @($resultados | Where-Object Status -eq "ok").Count
$atencao  = @($resultados | Where-Object Status -eq "atencao").Count
$revisar  = @($resultados | Where-Object { $_.Status -in @("revisar", "falhou") }).Count

Write-Host ("{0} pronto(s)  ·  {1} com atenção  ·  {2} para revisar  ·  {3}s" -f $ok, $atencao, $revisar, $total)

if ($revisar -gt 0) {
    Write-Host "`nPrecisam de conferência antes de usar:" -ForegroundColor Red
    $resultados | Where-Object { $_.Status -in @("revisar", "falhou") } |
        ForEach-Object { Write-Host "  - $($_.Arquivo)  ->  $($_.Destino)" }
}

# Índice do lote, para consultar depois sem reabrir cada relatório.
$indicePath = Join-Path $Saida "_lote.json"
New-Item -ItemType Directory -Force $Saida | Out-Null
$resultados | ConvertTo-Json -Depth 3 | Set-Content $indicePath -Encoding UTF8
Write-Host "`nÍndice do lote: $indicePath" -ForegroundColor DarkGray

# Código de saída diferente de zero quando algo precisa de atenção humana.
if ($revisar -gt 0) { exit 3 }
