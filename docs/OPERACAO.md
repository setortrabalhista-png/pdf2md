# Operação: ajuste fino e diagnóstico

## Quando o resultado não sai bom

### Páginas digitalizadas saindo vazias

Confira se o Tesseract e o modelo de português estão instalados:

```bash
python -c "from app.config import get_settings as g; from app.extractors import ocr_tesseract as o; s=g(); print(o.is_available(s.tesseract_cmd), o.available_languages(s.tesseract_cmd, s.tessdata_prefix))"
```

Se `por` não aparecer, baixe o modelo (veja o README). Se o binário não for
encontrado, aponte-o:

```bash
PDF2MD_TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### O PDF tem camada de texto, mas ela é lixo

Acontece em documento digitalizado com OCR ruim embutido pelo scanner. A camada
existe, então a rota `auto` a aproveita. Force o reconhecimento:

```bash
python -m app.cli documento.pdf --ocr force
```

### OCR com confiança baixa

Aumente a resolução de rasterização. O padrão de 300 dpi é o equilíbrio para A4;
documento com letra miúda ou fax degradado melhora a 400 ou 600, ao custo de
tempo e memória.

```bash
PDF2MD_OCR_DPI=400
```

Se o documento vier com fundo sujo ou sombra de dobra, a binarização adaptativa
já está ligada. Se, ao contrário, for um scan limpo e de alto contraste,
desligá-la às vezes ajuda:

```bash
PDF2MD_OCR_BINARIZE=false
```

### Tabela saindo torta

Veja em `tabelas.detalhe` no relatório qual motor foi usado e qual a confiança.

- Motor `lines` com confiança baixa e `malha de bordas incompleta`: a tabela tem
  bordas parciais. Instalar Ghostscript habilita o Camelot como segunda opinião.
- Motor `stream`: a tabela não tem bordas e as colunas foram inferidas por
  alinhamento. O teto de confiança desse motor é 0,88 por construção — sem
  bordas desenhadas nunca há certeza.
- `contém células mescladas`: o conteúdo está preservado, o formato não. Markdown
  não representa mescla.

Para tornar o sistema mais exigente (mais tabelas marcadas para revisão):

```bash
PDF2MD_TABLE_MIN_CONFIDENCE=0.8
```

### Texto corrido sendo confundido com tabela

O detector sem bordas exige que os vãos apareçam na mesma abscissa em pelo menos
62% das linhas do grupo. Se ainda assim houver falso positivo em documento com
espaçamento irregular, desligue o motor secundário aumentando o mínimo de linhas
exigido — ou desligue as tabelas para aquele documento:

```bash
python -m app.cli documento.pdf --sem-tabelas
```

### Títulos não detectados

Verifique `estrutura.estilo_do_corpo` e `estrutura.niveis_de_titulo` no
relatório. Se `niveis_de_titulo` estiver vazio, o documento não tem contraste
tipográfico suficiente — títulos no mesmo corpo e mesmo peso do texto. Reduza o
limiar:

```bash
PDF2MD_HEADING_SIZE_DELTA=0.4
```

Se, ao contrário, sobrarem títulos falsos, aumente para `1.0`.

### Cabeçalho do tribunal aparecendo no corpo

A recorrência exige presença em 60% das páginas elegíveis. Num documento de duas
páginas em que o carimbo só aparece numa, ele não é recorrente. Reduza:

```bash
PDF2MD_REPETITION_THRESHOLD=0.5
```

Cuidado: um limiar baixo demais remove conteúdo legítimo que se repete.

### Muitas imagens minúsculas no Markdown

São ícones, marcas d'água e artefatos de assinatura digital. Aumente o mínimo:

```bash
PDF2MD_IMAGE_MIN_AREA_PX=8000
```

### O documento tem duas colunas e saiu embaralhado

A detecção de colunas é conservadora de propósito. Se a calha central não for
perfeitamente limpa, ela não é reconhecida. Não há ajuste exposto para isso hoje
— é uma limitação conhecida.

---

## Ajuste de desempenho

Conversão é dominada por CPU: rasterização e OCR. O limitante prático é o número
de páginas que precisam de OCR.

| Situação | Tempo típico |
|---|---|
| PDF nativo, 2 páginas | menos de 1 s |
| PDF nativo, 200 páginas | 10 a 30 s |
| Página digitalizada, 300 dpi | 2 a 5 s por página |
| Página digitalizada, 600 dpi | 8 a 20 s por página |

O gerente de jobs usa dois trabalhadores — o suficiente para manter a interface
responsiva sem os dois brigarem pelo processador. Numa máquina com mais núcleos,
ajuste em [`app/core/job_manager.py`](../app/core/job_manager.py).

Para um processo de 500 páginas em que só interessam as primeiras 40:

```bash
python -m app.cli processo.pdf --paginas 1-40
```

O recorte é aplicado no primeiro estágio: as páginas fora dele não são lidas,
não geram tabela, não geram imagem e não entram na base de cálculo da cobertura.

---

## Diagnóstico

### Log detalhado

```bash
python -m app.cli documento.pdf -v
```

Mostra cada estágio, a rota de cada página, os motores de tabela disponíveis, o
estilo do corpo, os níveis de título e a decisão sobre hifenização.

### O diário de execução

`execucao.estagios` no relatório traz a duração e o desfecho de cada estágio.
Um estágio com `status: "failed"` foi isolado — a conversão seguiu sem ele, e o
motivo está em `error`.

### Camada de IA não roda

Se o relatório traz `AI_CALL_FAILED` com "não está autenticado", o Claude Code
CLI ainda não fez login. Ele guarda credenciais próprias, separadas das do
aplicativo Claude Desktop. Abra um terminal, rode `claude` e use `/login` uma
vez.

Se traz "Limite de uso do plano atingido", a cota da assinatura acabou. A
conversão continua válida: o refino é sempre opcional e nunca é necessário para
o resultado.

Para conferir qual binário foi localizado:

```bash
python -c "from app.ai.providers.claude_cli import _discover_cli; print(_discover_cli())"
```

Se estiver errado, aponte com `PDF2MD_AI_CLAUDE_CLI_PATH`.
### Reproduzir um caso

A conversão é determinística: mesmo PDF, mesmas opções, mesma saída. Há um teste
que exige isso (`test_conversao_e_deterministica`). Se um documento sai diferente
entre execuções, é bug — abra com o PDF anexado.

---

## Testes com documentos reais

Os PDFs sintéticos de `tests/fixtures/` exercitam cada caminho do pipeline, mas
não substituem material real. Para calibrar contra os documentos do escritório:

1. Coloque de cinco a oito PDFs representativos em `tests/fixtures/` — o
   `.gitignore` já impede que sejam versionados.
2. Converta cada um e leia o `resumo` do relatório.
3. Onde a cobertura ficar abaixo de 90%, abra `fidelidade.paginas_com_perda` e
   confira aquelas páginas contra o PDF.

Cobertura representativa exige: uma petição nativa do PJe, uma sentença, um PDF
inteiramente digitalizado, um contrato com tabelas complexas, um laudo com
gráficos e um documento híbrido.

---

## Segurança operacional

- **Não exponha na rede.** `PDF2MD_HOST` fora de `127.0.0.1` faz o documento
  trafegar pela rede sem autenticação nem TLS. Se precisar de acesso remoto,
  ponha atrás de um proxy reverso com autenticação.
- **Reduza o TTL** se a máquina for compartilhada:
  `PDF2MD_JOB_TTL_MINUTES=10`.
- **Camada de IA**: para documento sob sigilo, mantenha `null`. O provedor
  `claude_cli` envia trechos do documento para fora da máquina, e por isso exige
  autorização explícita a cada conversão.
- O diretório temporário fica em `%TEMP%\pdf2md` (Windows) ou `/tmp/pdf2md`.
  Ajuste com `PDF2MD_WORKSPACE_ROOT` se a política do escritório exigir um
  volume específico.
