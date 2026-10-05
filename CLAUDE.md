# pdf2md

Conversor local de PDF para Markdown, para documento jurídico brasileiro
(petição do PJe, sentença, contrato, laudo, anexo digitalizado).

Escritório: VLV Advogados. O código e os comentários são em português.

---

## Comandos

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # suíte (146 testes, ~30s)
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m app.cli arquivo.pdf -o saida\x
.\run.ps1                                        # interface em 127.0.0.1:8000
.\lote.ps1 -Pasta "C:\processos"                 # conversão em lote
```

Python **3.11 ou 3.12** — o 3.13+ ainda não tem wheels para toda a pilha.
O `.venv` não é portátil entre máquinas: use `instalar.ps1` num PC novo.

---

## Arquitetura em uma frase

PDF → **IR tipado** (`DocumentModel`, blocos com página, geometria, origem e
confiança) → Markdown. O Markdown é só o serializador final; nenhum estágio
escreve texto formatado direto.

Dez estágios em `app/pipeline/s01…s10`, orquestrados por `runner.py`. Estágio
não-crítico que falha vira aviso no relatório, não derruba a conversão.

Detalhe completo em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

---

## Regras que não se quebram

Estas nasceram de bugs reais. Cada uma tem teste.

**1. Conteúdo é imutável.** O texto que sai é o que entrou. Nada de corrigir,
completar ou normalizar valores.

**2. Marcador de lista nunca é renumerado.** `c)` continua `c)`, `3.` continua
`3.`. Alínea e item são citados em outros pontos da peça — trocar o número é
alterar o documento. Ver `app/render/markdown.py:_list_item_line`.

**3. A IA não pode inventar.** Ela devolve operações de um vocabulário fechado
(`app/ai/operations.py`), nunca texto. O `guard.py` confere três impressões
digitais — caracteres, números e palavras — antes e depois de cada operação.
As três existem porque contar caracteres é cego a transposição: `2.400,00` e
`4.200,00` têm exatamente os mesmos caracteres.

**4. Tabela duvidosa é sinalizada, não descartada.** Entra no Markdown com
`<!-- REVISAR: motivo -->`. Perder o dado é pior que entregá-lo marcado.

**5. O relatório presta contas.** Todo número em
`resultado_processamento.json` é derivado do IR, não estimado. `text_accuracy`
é *cobertura*, não acurácia contra gabarito — e desconta o boilerplate que o
sistema remove de propósito.

---

## Onde mexer

| Quero… | Vá em |
|---|---|
| Ajustar um limiar | `app/config.py` — todos estão lá, com o porquê |
| Mudar detecção de tabela | `app/extractors/tables_lines.py` (com borda), `tables_stream.py` (sem) |
| Mudar detecção de título | `app/pipeline/s07_semantics.py` |
| Mudar o OCR | `app/pipeline/s03_ocr.py`, `app/extractors/ocr_*.py` |
| Mudar a saída Markdown | `app/render/markdown.py` |
| Mudar o relatório | `app/quality/report.py` e `validators.py` |
| Mexer na interface | `app/web/` — HTML/CSS/JS puro, sem build |

Nenhum número mágico espalhado pelo código: tudo que é limiar vive em
`config.py` e é sobrescrevível por `PDF2MD_*`.

---

## Convenções

- Comentários explicam **por que**, não o que. Se um limiar tem valor
  incomum, o comentário diz qual defeito real ele resolve.
- Testes em português, nomeados pelo comportamento
  (`test_tabela_em_tres_paginas_nao_trava`).
- Fixtures de teste são PDFs sintéticos gerados por `tests/make_fixtures.py`;
  não são versionadas.
- A interface e a CLI compartilham o mesmo pipeline. Nada de lógica duplicada.

---

## Privacidade

Requisito do projeto, não detalhe: os documentos são de clientes.

- Servidor em `127.0.0.1`; expor exige mudar `PDF2MD_HOST` de propósito.
- O PDF de entrada é apagado ao fim da conversão.
- Workspace temporário destruído por TTL, por pedido, ou ao encerrar.
- Nenhuma chamada de rede, **exceto** se a camada de IA for ligada — que vem
  desligada e exige consentimento explícito por conversão.

Ao mexer em qualquer coisa que grave em disco ou faça rede, confira se essas
garantias continuam valendo. `tests/test_api.py` tem uma seção para isso.

---

## Armadilhas conhecidas

- **`.venv` não é portátil** — grava o caminho absoluto da máquina.
- **OCR "corrige" demais**: o modelo de português acentua palavras que o
  original não acentuava. É reconhecimento, não transcrição literal.
- **Markdown não representa célula mesclada.** O conteúdo é preservado, o
  formato não — daí a marcação de revisão.
- **Duas colunas** são detectadas de forma conservadora; três ou mais não são
  tratadas.
