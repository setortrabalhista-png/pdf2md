# pdf2md

Conversão local de PDF para Markdown estruturado, pensada para documento
jurídico brasileiro: petição do PJe, sentença, contrato, laudo pericial e o
anexo digitalizado que vem junto.

Não é um extrator de texto. O sistema entende a estrutura do documento antes de
escrever qualquer coisa, e depois **presta contas** do que fez: um relatório em
JSON diz quanto do texto de origem chegou ao Markdown, quais tabelas merecem
conferência e quais páginas dependeram de OCR.

Roda inteiramente na sua máquina. Nenhum documento sai daqui, a menos que você
ligue explicitamente a camada opcional de IA e autorize o envio.

---

## O que ele entrega

Para cada PDF convertido:

```
saida/Petição Inicial - João da Silva/
├── Petição Inicial - João da Silva.md   Markdown com a estrutura preservada
├── resultado_processamento.json         relatório de qualidade auditável
└── assets/
    ├── p003_img001.jpg                  imagens em qualidade original
    └── p007_fig001.png                  figuras vetoriais rasterizadas
```

**O Markdown leva o nome do PDF de origem**, com acentos e espaços preservados —
numa conversão em lote, quarenta arquivos chamados `documento.md` seriam
inúteis. Só é removido o que o sistema de arquivos de fato recusa. Para voltar
ao nome fixo, `PDF2MD_MD_USE_SOURCE_NAME=false`.

O nome do arquivo de imagem carrega a página de origem — indispensável quando
alguém precisa conferir contra o PDF.

## O que ele preserva

| Elemento | Como |
|---|---|
| Títulos e subtítulos | Hierarquia derivada dos estilos do próprio documento, não de tamanho absoluto |
| Listas | Marcador original mantido: `c)` continua sendo `c)`, nunca vira `3.` |
| Tabelas | Réguas vetoriais; e, sem bordas, por alinhamento de colunas |
| Células mescladas | Detectadas, conteúdo preservado, tabela marcada para revisão |
| Imagens | Bytes originais, sem recompressão; scans fatiados são recompostos |
| Notas de rodapé | Separadas do corpo pelo filete e pelo corpo de fonte |
| Numeração | `1.`, `1.1`, `Art. 5º`, `CLÁUSULA` reconhecidos como estrutura |
| Parágrafos partidos | Unidos através da quebra de página |
| Cabeçalho e rodapé | Removidos do corpo por recorrência, registrados nos metadados |

## O que ele **não** faz

Corrigir, completar ou reescrever o documento. O texto que sai é o texto que
entrou. A camada de IA, quando ligada, só reorganiza — e isso é imposto por
verificação, não por instrução ao modelo. Veja [docs/QUALIDADE.md](docs/QUALIDADE.md).

---

## Instalação

Em computador novo, o caminho curto é o instalador — ele resolve Python,
Tesseract, bibliotecas e modelo de OCR sozinho:

```powershell
.\instalar.ps1
```

Para levar o sistema a **outra máquina**, veja
[docs/INSTALACAO.md](docs/INSTALACAO.md) — copiar a pasta inteira não
funciona, porque o ambiente virtual guarda o caminho absoluto desta aqui.

O passo a passo manual, se preferir fazer à mão:

Requisitos: **Python 3.11 ou 3.12** (o 3.13+ ainda não tem wheels para toda a
pilha) e, para documento digitalizado, **Tesseract OCR**.

### Windows

```powershell
winget install Python.Python.3.12
winget install UB-Mannheim.TesseractOCR
```

### Linux / macOS

```bash
sudo apt install python3.12-venv tesseract-ocr    # Debian/Ubuntu
brew install python@3.12 tesseract                # macOS
```

### O projeto

```bash
git clone <repositorio> && cd pdf2md
python3.12 -m venv .venv
```

Ative o ambiente (`.venv\Scripts\activate` no Windows, `source .venv/bin/activate`
no restante) e instale:

```bash
pip install -r requirements.txt -r requirements-ocr.txt
```

### Modelo de português para o OCR

O sistema procura os modelos em `tessdata/` dentro do projeto — sem precisar de
permissão de administrador.

```bash
mkdir -p tessdata && curl -L -o tessdata/por.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata
```

```bash
curl -L -o tessdata/osd.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/osd.traineddata
```

O `osd` detecta páginas digitalizadas de cabeça para baixo — comum em anexo de
processo físico.

### Conferir

```bash
python -m pytest -q
```

---

## Uso

### Interface web

Duplo clique em **`iniciar.cmd`** — ele sobe o servidor e abre o navegador
quando estiver pronto. Pela linha de comando, o equivalente:

```bash
.\run.ps1 -Abrir
```

**A janela do terminal precisa continuar aberta**: é ela que hospeda o
servidor. Fechá-la, ou dar Ctrl+C, encerra o serviço — e o navegador passa
a mostrar `ERR_CONNECTION_REFUSED`. Não é defeito: é o servidor desligado.

Se a porta já estiver ocupada, o script avisa e diz o que fazer, em vez de
falhar com uma mensagem obscura do Windows.

Abra `http://127.0.0.1:8000`. Arraste **um PDF, vários, ou uma pasta inteira** —
subpastas são percorridas e o que não for PDF é ignorado.

Com mais de um documento, a tela mostra a fila com o progresso de cada um e,
ao final, o **quadro de conferência**: uma linha por documento com cobertura,
tabelas, imagens e o status. Clicar numa linha abre o resultado daquele
documento. O botão de download traz tudo num `.zip` único, com uma pasta por
documento e um `_lote.json` de índice.

Os arquivos são apagados sozinhos após 60 minutos, e o botão **Apagar agora**
faz isso na hora — para o lote inteiro.

### Linha de comando

```bash
python -m app.cli peticao.pdf -o saida/peticao
```

```bash
python -m app.cli processo.pdf --ocr force --idioma por --paginas 1-40
```

Opções: `--ocr auto|force|never`, `--paginas 1-10,15`, `--sem-imagens`,
`--sem-tabelas`, `--sem-marcador-de-pagina`, `--ia claude_cli --consentir`, `-v`.

O código de saída é `0` quando está tudo certo e `3` quando o relatório pede
revisão — útil para encadear em script.

### Conversão em lote pela linha de comando

O mesmo que a interface faz, sem abrir o navegador — e com a vantagem de poder
retomar um lote interrompido:

```bash
.\lote.ps1 -Pasta "C:\processos\0001234-56"
```

O quadro diz, documento por documento, a cobertura, quantas tabelas saíram e
quantas pedem conferência. O que interessa é a coluna **Status**: `ok` pode ser
usado direto, `atencao` merece uma olhada, `revisar` não deve ser usado sem
conferir.

| Opção | Para quê |
|---|---|
| `-Recursivo` | Percorre também as subpastas |
| `-Ocr force` | Força o reconhecimento em todos os documentos |
| `-Paginas "1-40"` | Aplica o mesmo recorte a todos |
| `-Continuar` | Pula o que já foi convertido — retoma um lote interrompido |
| `-Saida "D:\destino"` | Muda a pasta de destino |

Cada documento vai para a sua própria subpasta, e um `_lote.json` guarda o
índice do lote inteiro. O script devolve código de saída `3` se algum documento
precisar de revisão.

A conversão é sequencial de propósito: o trabalho é dominado por CPU, e rodar em
paralelo só faria os processos brigarem pelo processador.

### Docker

```bash
docker compose up --build
```

A imagem já traz Tesseract, Ghostscript e Java, então Camelot e Tabula ficam
disponíveis como motores de reserva de tabela.

---

## Camada de IA (opcional, desligada por padrão)

Serve para casos que a heurística não resolve: título que ficou colado no
parágrafo, tabela cujo cabeçalho não foi identificado, bloco fora de ordem.

Dois provedores:

| Provedor | Dados saem da máquina? | Custo |
|---|---|---|
| `null` (padrão) | Não | — |
| `claude_cli` | **Sim** | Nenhum: usa a sessão do Claude Code já instalada |

O `claude_cli` invoca o binário do Claude Code em modo headless, aproveitando a
assinatura existente — sem chave de API e sem cobrança por token. Em troca,
trechos do documento (identificadores de bloco, tipo, página e um recorte de
texto de até 400 caracteres) vão para os servidores da Anthropic. O PDF nunca é
enviado.

**Antes do primeiro uso**, o CLI precisa ser autenticado uma vez. Ele tem
credenciais próprias, separadas das do aplicativo Claude Desktop:

```bash
claude
```

e, dentro dele, `/login`. Sem isso o refino é ignorado com um aviso claro no
relatório — a conversão em si continua correta, porque a camada de IA nunca é
necessária para o resultado.

Por isso a interface exige uma autorização explícita, e a CLI exige
`--consentir`. **Para documento sob sigilo profissional, use o provedor `null`.**

Como a IA fica impedida de inventar conteúdo: [docs/QUALIDADE.md](docs/QUALIDADE.md).

---

## Configuração

Qualquer parâmetro pode ser ajustado por variável de ambiente com o prefixo
`PDF2MD_`, ou por um arquivo `.env` na raiz do projeto:

```bash
PDF2MD_OCR_DPI=400
PDF2MD_JOB_TTL_MINUTES=15
PDF2MD_TABLE_MIN_CONFIDENCE=0.75
PDF2MD_KEEP_SOURCE_PDF=false
PDF2MD_MD_USE_SOURCE_NAME=true
```

A lista completa, com o porquê de cada limiar, está em
[`app/config.py`](app/config.py).

---

## Documentação

| Documento | Assunto |
|---|---|
| [docs/ARQUITETURA.md](docs/ARQUITETURA.md) | Os dez estágios, o modelo intermediário e o porquê de cada escolha |
| [docs/QUALIDADE.md](docs/QUALIDADE.md) | Como o relatório é calculado e como a IA é contida |
| [docs/API.md](docs/API.md) | Endpoints, formato do SSE, exemplos |
| [docs/INSTALACAO.md](docs/INSTALACAO.md) | Levar o sistema para outro computador |
| [docs/OPERACAO.md](docs/OPERACAO.md) | Ajuste fino, diagnóstico e limitações conhecidas |

---

## Privacidade

- O servidor escuta em `127.0.0.1`. Expor na rede exige mudar `PDF2MD_HOST` de
  propósito.
- O PDF enviado é apagado assim que a conversão termina.
- Todo o diretório temporário do job é destruído quando o TTL expira, quando o
  usuário pede, ou quando o servidor é encerrado.
- Nenhuma requisição de rede é feita, exceto se a camada de IA for ligada.
- Nome de arquivo não vai para o log — só o hash SHA-256.
