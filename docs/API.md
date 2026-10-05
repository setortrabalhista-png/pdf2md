# API

Base: `http://127.0.0.1:8000`. Documentação interativa em `/api/docs`.

Todos os endpoints são locais. Não há autenticação porque não há multiusuário —
o serviço é de uso pessoal, na máquina de quem converte.

---

## `GET /api/capacidades`

O que esta instalação sabe fazer. A interface se adapta à resposta: sem
Tesseract, o seletor de OCR é desabilitado; sem provedor de IA disponível, a
opção some.

```json
{
  "ocr": {
    "disponivel": true,
    "caminho": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    "idiomas": ["osd", "por"],
    "idioma_padrao": "por",
    "dpi": 300
  },
  "tabelas": {
    "motor_primario": "lines (réguas vetoriais)",
    "motor_secundario": "stream (alinhamento)",
    "reservas": { "camelot": false, "tabula": false }
  },
  "ia": {
    "provedores": [
      { "id": "null", "label": "Nenhum (apenas heurística local)",
        "available": true, "reason": "", "sends_data_externally": false },
      { "id": "claude_cli", "label": "Claude Code CLI (plano Max, sem chave de API)",
        "available": true, "reason": "", "sends_data_externally": true }
    ],
    "exige_consentimento": true
  },
  "limites": {
    "tamanho_maximo_mb": 200, "ttl_minutos": 60,
    "lote_max_arquivos": 100, "lote_max_mb": 1000
  },
  "privacidade": {
    "processamento_local": true,
    "guarda_pdf_de_entrada": false,
    "endereco": "127.0.0.1:8000"
  }
}
```

---

## `POST /api/jobs`

Cria a conversão. `multipart/form-data`. Responde **202** imediatamente; o
processamento é assíncrono.

| Campo | Tipo | Padrão | Observação |
|---|---|---|---|
| `arquivo` | arquivo | — | obrigatório; a assinatura `%PDF-` é conferida |
| `ocr` | `auto` \| `force` \| `never` | `auto` | |
| `idioma` | texto | `por` | código do Tesseract |
| `paginas` | texto | vazio | `1-10,15`; vazio = documento inteiro |
| `extrair_imagens` | bool | `true` | |
| `extrair_tabelas` | bool | `true` | |
| `figuras_vetoriais` | bool | `true` | gráficos e organogramas |
| `marcadores_de_pagina` | bool | `true` | `<!-- página N -->` |
| `unir_entre_paginas` | bool | `true` | parágrafo e tabela partidos |
| `ia_provedor` | texto | vazio | `null` ou `claude_cli` |
| `ia_consentimento` | bool | `false` | obrigatório para provedor externo |

```bash
curl -X POST http://127.0.0.1:8000/api/jobs -F "arquivo=@peticao.pdf" -F "ocr=auto"
```

```json
{ "id": "3MJY2CkaIpsdTU3m", "status": "queued" }
```

**Erros:** `400` arquivo vazio ou não-PDF; `413` acima do limite de tamanho.

---

## `GET /api/jobs/{id}`

Estado atual. Quando `status` é `done`, traz o relatório completo e o inventário
de arquivos.

```json
{
  "id": "3MJY2CkaIpsdTU3m",
  "arquivo": "peticao.pdf",
  "status": "done",
  "estagio": "finish",
  "mensagem": "Concluído",
  "progresso": 1.0,
  "erro": null,
  "codigo_erro": null,
  "criado_em": 1756130000.1,
  "concluido_em": 1756130000.8,
  "relatorio": { "text_accuracy": 97.3, "...": "..." },
  "arquivos": [
    { "nome": "peticao.md", "bytes": 2418, "tipo": "md" },
    { "nome": "assets/p003_img001.png", "bytes": 84120, "tipo": "png" }
  ]
}
```

Status possíveis: `queued`, `running`, `done`, `failed`, `expired`.

---

## `GET /api/jobs/{id}/events`

Progresso em tempo real (`text/event-stream`).

O **primeiro** evento é sempre do tipo `estado`, com o retrato atual — quem
conecta no meio da conversão não perde o contexto. Em seguida vêm os eventos de
`progresso`. O stream encerra com um segundo evento `estado`, e é esse que traz
o relatório.

```
data: {"tipo":"estado","id":"3MJY...","status":"running","progresso":0.05, ...}

data: {"tipo":"progresso","status":"running","estagio":"ocr","mensagem":"OCR — página 4 (2 de 9)","progresso":0.27}

data: {"tipo":"progresso","status":"done","estagio":"done","mensagem":"Concluído","progresso":1.0}

data: {"tipo":"estado","status":"done","relatorio":{...},"arquivos":[...]}
```

Consuma até um evento com `tipo == "estado"` e `status` em `done`/`failed`. Não
encerre no primeiro `status: done` — o evento de progresso chega antes do
relatório estar gravado.

Uma linha `: keep-alive` é emitida a cada 15 segundos de silêncio.

---

## `GET /api/jobs/{id}/markdown`

O Markdown em `text/markdown; charset=utf-8`.

## `GET /api/jobs/{id}/preview`

O Markdown renderizado em HTML, **no servidor** — nenhuma biblioteca externa,
nenhuma requisição de rede. Os caminhos de imagem são reescritos para a rota de
arquivos do próprio job.

## `GET /api/jobs/{id}/arquivo/{caminho}`

Um arquivo específico da saída. O Markdown leva o nome do PDF de origem —
`peticao.md` para `peticao.pdf` — e esse nome vem em
`relatorio.documento.arquivo_markdown` e na lista `arquivos` do job.

Travessia de caminho é bloqueada: qualquer alvo fora do diretório do job devolve
`404`.

## `GET /api/jobs/{id}/download`

Tudo num `.zip`: Markdown, assets e relatório.

Os quatro endpoints acima devolvem **409** enquanto a conversão não terminou.

---

## Lotes

Vários documentos numa tacada. Os jobs continuam individuais: cada documento do
lote tem o seu `/api/jobs/{id}` e pode ser baixado sozinho.

### `POST /api/lotes`

`multipart/form-data`, com o campo `arquivos` repetido. As demais opções são as
mesmas de `POST /api/jobs` e valem para todos os documentos do lote.

```bash
curl -X POST http://127.0.0.1:8000/api/lotes -F "arquivos=@a.pdf" -F "arquivos=@b.pdf" -F "ocr=auto"
```

```json
{ "id": "Kx7f2QpLmNa9", "total": 2, "recusados": [] }
```

Arquivo inválido no meio do lote não derruba o resto: ele entra em `recusados`
com o motivo, e os demais são processados. Só devolve `400` se **nenhum**
arquivo for aproveitável, e `413` se o lote passar do limite de quantidade.

### `GET /api/lotes/{id}`

Estado agregado, a lista de documentos e o quadro de conferência.

```json
{
  "id": "Kx7f2QpLmNa9",
  "total": 3, "concluidos": 3, "convertidos": 3, "falharam": 0,
  "progresso": 1.0, "terminado": true,
  "documentos": [ { "id": "...", "arquivo": "peticao.pdf", "status": "done", "...": "..." } ],
  "resumo": [
    {
      "id": "...", "arquivo": "peticao.pdf", "pasta": "peticao",
      "estado": "done", "status": "ok", "cobertura": 97.3,
      "paginas": 2, "tabelas": 1, "tabelas_para_revisar": 0,
      "imagens": 0, "paginas_com_ocr": 0, "erro": null, "pendencias": []
    }
  ]
}
```

`progresso` é a média do progresso individual — uma barra que anda de forma
contínua, em vez de saltar a cada documento pronto.

### `GET /api/lotes/{id}/events`

Progresso de todos os documentos num stream só. Três formatos de evento:

```
data: {"tipo":"estado","total":3,"concluidos":0,"progresso":0.0,"documentos":[...]}

data: {"tipo":"documento","job":"...","arquivo":"peticao.pdf","indice":0,
       "status":"running","mensagem":"OCR — página 4 (2 de 9)","progresso":0.27,
       "documento":null,"lote":{"concluidos":0,"progresso":0.09,"...":"..."}}

data: {"tipo":"estado","terminado":true,"resumo":[...]}
```

O evento `documento` traz o agregado do lote junto, em `lote` — a interface
atualiza a linha da fila e a barra geral com o mesmo evento. Quando o documento
termina, o campo `documento` vem preenchido com o snapshot completo.

Consuma até um evento `estado` com `terminado: true`.

### `GET /api/lotes/{id}/resumo.csv`

O quadro de conferência em CSV, separado por `;` e com BOM — abre direto no
Excel com os acentos corretos.

### `GET /api/lotes/{id}/download`

Um `.zip` com tudo. Devolve `409` enquanto o lote não terminou.

```
_lote.json                            índice, com o quadro de conferência
peticao/peticao.md
peticao/resultado_processamento.json
peticao/assets/p003_img001.png
contrato/contrato.md
contrato-2/contrato.md                pasta repetida ganha sufixo
```

Cada pasta é uma conversão completa e independente: o `.md`, o relatório e as
imagens que ele referencia.

### `DELETE /api/lotes/{id}`

Purga o lote inteiro: todos os documentos e o `.zip`.

```json
{ "purgado": true, "documentos": 3 }
```

---

## `DELETE /api/jobs/{id}`

Purga imediata: apaga o diretório do job do disco e remove o registro da
memória.

```json
{ "purgado": true }
```

---

## `GET /api/jobs`

Lista os jobs vivos nesta sessão.

## `GET /health`

```json
{ "ok": true }
```

---

## Ciclo de vida e privacidade

- O PDF enviado é apagado assim que a conversão termina
  (`PDF2MD_KEEP_SOURCE_PDF=false`, padrão).
- Todo o diretório do job é destruído quando o TTL expira (60 minutos por
  padrão), quando o `DELETE` é chamado, ou quando o servidor é encerrado.
- Uma varredura em segundo plano remove os expirados a cada 5 minutos.
- Jobs em execução nunca são varridos no meio.

## Erros

Erros de domínio devolvem código e mensagem:

```json
{ "detail": "PDF protegido por senha. Remova a proteção antes de converter.",
  "codigo": "ENCRYPTED_PDF" }
```

| Código | HTTP |
|---|---|
| `INVALID_PDF` | 400 |
| `ENCRYPTED_PDF` | 400 |
| `FILE_TOO_LARGE` | 413 |
| `JOB_NOT_FOUND` | 404 |
| `OCR_UNAVAILABLE` | 503 |
| `AI_REFUSED` | 422 |
| `INTERNAL_ERROR` | 500 |
