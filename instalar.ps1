<#
.SYNOPSIS
    Instala o pdf2md num computador novo.

.DESCRIPTION
    Cuida de tudo o que a máquina precisa e que ela ainda não tem: Python 3.12,
    Tesseract OCR, o ambiente virtual, as dependências e o modelo de português.

    Pode ser executado quantas vezes quiser — o que já estiver instalado é
    detectado e pulado.

.PARAMETER PularOcr
    Não instala o Tesseract. O sistema funciona sem ele, mas páginas
    digitalizadas sairão sem texto.

.PARAMETER PularTestes
    Não roda a suíte de verificação ao final. A suíte leva cerca de 40 segundos
    e é a única forma de confirmar que a instalação ficou boa.

.EXAMPLE
    .\instalar.ps1
#>
param(
    [switch]$PularOcr,
    [switch]$PularTestes
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$falhas = @()
$avisos = @()

function Titulo($texto) {
    Write-Host ""
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host "  $('-' * $texto.Length)" -ForegroundColor DarkGray
}

function Ok($texto)     { Write-Host "    [ok]   $texto" -ForegroundColor Green }
function Info($texto)   { Write-Host "    ...    $texto" -ForegroundColor Gray }
function Aviso($texto)  { Write-Host "    [!]    $texto" -ForegroundColor Yellow; $script:avisos += $texto }
function Erro($texto)   { Write-Host "    [x]    $texto" -ForegroundColor Red; $script:falhas += $texto }

Write-Host ""
Write-Host "  Instalacao do pdf2md" -ForegroundColor White
Write-Host "  Conversor local de PDF para Markdown" -ForegroundColor DarkGray

# ── 1. Python 3.12 ──────────────────────────────────────────────────────
Titulo "1. Python"

function Achar-Python312 {
    # O lançador `py` é o caminho confiável no Windows: ele conhece todas as
    # versões instaladas, inclusive as que não estão no PATH.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $versoes = & py -0p 2>$null
        foreach ($linha in $versoes) {
            if ($linha -match "3\.(1[12])" -and $linha -match "([A-Za-z]:\\[^\s].*python\.exe)") {
                return $Matches[1]
            }
        }
    }
    foreach ($v in @("312", "311")) {
        $candidato = "$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe"
        if (Test-Path $candidato) { return $candidato }
        $candidato = "C:\Program Files\Python$v\python.exe"
        if (Test-Path $candidato) { return $candidato }
    }
    return $null
}

$python312 = Achar-Python312

if ($python312) {
    $v = & $python312 --version
    Ok "$v encontrado"
} else {
    Info "Python 3.11/3.12 nao encontrado. Instalando via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Erro "winget nao disponivel. Instale o Python 3.12 manualmente: https://www.python.org/downloads/release/python-3120/"
    } else {
        winget install --id Python.Python.3.12 -e --scope user `
            --accept-source-agreements --accept-package-agreements | Out-Null

        # O PATH da sessao atual nao enxerga o que acabou de ser instalado.
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $python312 = Achar-Python312

        if ($python312) { Ok "Python 3.12 instalado" }
        else { Erro "Python instalado, mas nao localizado. Feche e reabra o terminal e rode este script de novo." }
    }
}

# ── 2. Tesseract OCR ────────────────────────────────────────────────────
Titulo "2. Tesseract (OCR de documentos digitalizados)"

$tesseract = $null
foreach ($c in @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
)) {
    if (Test-Path $c) { $tesseract = $c; break }
}
if (-not $tesseract -and (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    $tesseract = (Get-Command tesseract).Source
}

if ($PularOcr) {
    Aviso "Pulado a pedido. Paginas digitalizadas sairao sem texto."
} elseif ($tesseract) {
    $v = (& $tesseract --version 2>&1 | Select-Object -First 1)
    Ok "$v"
} else {
    Info "Nao encontrado. Instalando via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Aviso "winget nao disponivel. Baixe em: https://github.com/UB-Mannheim/tesseract/wiki"
    } else {
        winget install --id UB-Mannheim.TesseractOCR -e `
            --accept-source-agreements --accept-package-agreements | Out-Null
        if (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe") {
            Ok "Tesseract instalado"
        } else {
            Aviso "Instalacao do Tesseract nao confirmada. O sistema roda sem ele, mas sem OCR."
        }
    }
}

# ── 3. Ambiente virtual e dependencias ──────────────────────────────────
Titulo "3. Ambiente Python e bibliotecas"

$venvPython = ".\.venv\Scripts\python.exe"

if (-not $python312 -and -not (Test-Path $venvPython)) {
    Erro "Sem Python nao da para continuar."
} else {
    if (Test-Path $venvPython) {
        Ok "Ambiente virtual ja existe"
    } else {
        Info "Criando o ambiente virtual..."
        & $python312 -m venv .venv
        Ok "Ambiente criado"
    }

    Info "Instalando as bibliotecas (pode levar alguns minutos)..."
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r requirements.txt -r requirements-ocr.txt --quiet
    if ($LASTEXITCODE -eq 0) { Ok "Bibliotecas instaladas" }
    else { Erro "Falha ao instalar as bibliotecas" }
}

# ── 4. Modelo de portugues do OCR ───────────────────────────────────────
Titulo "4. Modelo de portugues para o OCR"

New-Item -ItemType Directory -Force .\tessdata | Out-Null
$base = "https://github.com/tesseract-ocr/tessdata_best/raw/main"

foreach ($modelo in @("por", "osd")) {
    $destino = ".\tessdata\$modelo.traineddata"
    if (Test-Path $destino) {
        Ok "$modelo.traineddata ja presente"
    } else {
        try {
            Info "Baixando $modelo.traineddata..."
            Invoke-WebRequest "$base/$modelo.traineddata" -OutFile $destino -UseBasicParsing
            Ok "$modelo.traineddata baixado"
        } catch {
            Aviso "Nao foi possivel baixar $modelo.traineddata (sem internet?). O OCR ficara limitado."
        }
    }
}

# ── 5. Verificacao ──────────────────────────────────────────────────────
Titulo "5. Verificacao"

if ($PularTestes) {
    Info "Verificacao pulada a pedido"
} elseif (Test-Path $venvPython) {
    # Converter um PDF de verdade prova mais do que qualquer teste unitario:
    # exercita leitura, tabelas, OCR e escrita, de ponta a ponta.
    Info "Convertendo documentos de teste..."

    $conferencia = Join-Path $env:TEMP "pdf2md-verificacao"
    Remove-Item -Recurse -Force $conferencia -ErrorAction SilentlyContinue

    & $venvPython -m tests.make_fixtures 2>&1 | Out-Null

    # 1) Peca juridica nativa: titulos, listas, nota de rodape e tabela.
    & $venvPython -m app.cli "tests\fixtures\juridico.pdf" -o "$conferencia\a" 2>&1 | Out-Null
    $relatorio = Join-Path $conferencia "a\resultado_processamento.json"

    if (Test-Path $relatorio) {
        $r = Get-Content $relatorio -Raw -Encoding UTF8 | ConvertFrom-Json
        $md = Get-ChildItem "$conferencia\a\*.md" | Select-Object -First 1
        if ($r.text_accuracy -ge 90 -and $md) {
            Ok "Documento nativo: $($r.text_accuracy)% do texto, $($r.tables_detected) tabela(s) -> $($md.Name)"
        } else {
            Erro "Conversao com resultado ruim: $($r.text_accuracy)% de cobertura"
        }
    } else {
        Erro "A conversao de teste nao produziu relatorio"
    }

    # 2) Documento digitalizado: so faz sentido se houver OCR.
    if ($tesseract -and -not $PularOcr) {
        & $venvPython -m app.cli "tests\fixtures\escaneado.pdf" -o "$conferencia\b" 2>&1 | Out-Null
        $relatorioOcr = Join-Path $conferencia "b\resultado_processamento.json"
        if (Test-Path $relatorioOcr) {
            $r2 = Get-Content $relatorioOcr -Raw -Encoding UTF8 | ConvertFrom-Json
            $conf = $r2.ocr.confianca_media_pct
            if ($r2.ocr.paginas_com_ocr -ge 1 -and $conf -ge 70) {
                Ok "Documento digitalizado: OCR com $conf% de confianca"
            } else {
                Aviso "OCR com resultado fraco (confianca $conf%). Confira o modelo de portugues."
            }
        } else {
            Aviso "A conversao com OCR nao produziu relatorio"
        }
    }

    Remove-Item -Recurse -Force $conferencia -ErrorAction SilentlyContinue
}

# ── Resultado ───────────────────────────────────────────────────────────
Write-Host ""
if ($falhas.Count -eq 0) {
    Write-Host "  Instalacao concluida." -ForegroundColor Green
    if ($avisos.Count) {
        Write-Host ""
        Write-Host "  Com $($avisos.Count) ressalva(s):" -ForegroundColor Yellow
        $avisos | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    }
    Write-Host ""
    Write-Host "  Para usar: duplo clique em iniciar.cmd" -ForegroundColor White
    Write-Host "  (ou rode  .\run.ps1 -Abrir  neste terminal)" -ForegroundColor DarkGray
    Write-Host ""
    exit 0
} else {
    Write-Host "  Instalacao incompleta:" -ForegroundColor Red
    $falhas | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host ""
    exit 1
}
