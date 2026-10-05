# Qualidade: como o sistema presta contas

Um conversor que não diz onde errou é pior que inútil num escritório — cria a
impressão de que o documento foi conferido. Este aqui produz um relatório em que
cada número é rastreável.

## `resultado_processamento.json`

As quatro chaves de topo são exatamente as especificadas:

```json
{
  "text_accuracy": 97.3,
  "tables_detected": 1,
  "images_extracted": 0,
  "warnings": []
}
```

Em volta delas vem o detalhe que torna o número auditável: `fidelidade`, `ocr`,
`tabelas`, `imagens`, `estrutura`, `paginas`, `execucao`, `ia` e `resumo`.

### O que `text_accuracy` significa de verdade

É **cobertura de texto**, não acurácia contra um gabarito — gabarito não existe.
Mede o percentual dos caracteres disponíveis na origem (camada nativa do PDF +
saída do OCR) que chegaram ao Markdown.

Duas correções importantes na base de cálculo:

- **O boilerplate removido de propósito é descontado.** Cabeçalho de tribunal,
  numeração de folhas e carimbo de assinatura eletrônica são retirados por
  decisão do sistema. Contá-los como perda transformaria todo processo bem
  convertido num alarme falso.
- **Páginas fora do recorte pedido não entram na conta.**

Cobertura de 100% não significa texto correto: um documento pode ter cobertura
total e ainda estar cheio de erro de OCR. Por isso a confiança do OCR e a
sanidade léxica são indicadores **separados**.

### Os três confrontos independentes

Um só engana; três é difícil.

**1. Cobertura de caracteres.** Compara, página a página, o que existia na
origem com o que chegou ao IR. Detecta bloco engolido por região de tabela,
coluna não lida, página pulada. Abaixo de 75% numa página, gera
`PAGE_TEXT_LOSS`.

**2. Segunda opinião textual.** O `pdfplumber` lê o mesmo PDF, de forma
independente, numa amostra de até doze páginas. Onde os dois motores discordam
muito, há algo estranho no PDF ou na nossa leitura — normalmente codificação de
fonte irregular, comum em sistemas de tribunal antigos. Gera
`EXTRACTOR_DISAGREEMENT`.

**3. Sanidade léxica.** Proporção de tokens implausíveis em português: palavra
de quatro letras ou mais sem vogal, caractere repetido quatro vezes seguidas,
alternância de caixa no meio da palavra. É o sintoma de OCR ruim, e aparece
mesmo quando a confiança média do Tesseract está alta. Acima de 6%, gera
`OCR_QUALITY_SUSPECT`.

### Catálogo de avisos

| Código | Gravidade | O que significa |
|---|---|---|
| `PAGE_NO_TEXT` | erro | Página não produziu texto algum |
| `OCR_SKIPPED` | erro | Página sem camada de texto e OCR desativado nesta conversão |
| `OCR_NO_TEXT` | erro | O OCR rodou e não encontrou nada |
| `OCR_PAGE_FAILED` | erro | O OCR falhou naquela página |
| `IMAGE_EXTRACTION_FAILED` | erro | Falha ao extrair imagens da página |
| `STAGE_FAILED` | erro | Um estágio não-crítico quebrou e foi ignorado |
| `AI_CONTENT_DRIFT` | erro | Divergência de conteúdo após o refino por IA |
| `PAGE_TEXT_LOSS` | aviso | Menos de 75% do texto de origem chegou ao Markdown |
| `TABLE_LOW_CONFIDENCE` | aviso | Tabela abaixo do limiar de confiança |
| `OCR_LOW_CONFIDENCE` | aviso | Confiança média de OCR abaixo de 70% na página |
| `OCR_QUALITY_SUSPECT` | aviso | Muitas palavras improváveis em português |
| `EXTRACTOR_DISAGREEMENT` | aviso | Os dois motores de extração divergem |
| `IMAGES_MISSING` | aviso | Menos imagens extraídas do que o PDF declara |
| `OCR_LANG_MISSING` | aviso | Idioma pedido não instalado; usou-se outro |
| `AI_CONSENT_MISSING` | aviso | IA externa selecionada sem autorização; ignorada |
| `AI_UNAVAILABLE` | aviso | Provedor de IA indisponível |
| `AI_CALL_FAILED` | aviso | A chamada de IA falhou e foi ignorada |
| `PAGES_EMPTY` | informação | Páginas genuinamente em branco |
| `AI_OPERATIONS_REJECTED` | informação | Operações da IA descartadas pelo verificador |

### O veredito

O bloco `resumo` traz um status em três níveis:

- **ok** — nada a conferir.
- **atencao** — há avisos ou tabela marcada para revisão.
- **revisar** — há erros, ou a cobertura ficou abaixo de 85%.

A CLI devolve código de saída `3` no status `revisar`, o que permite encadear a
conversão em script sem ler o JSON.

### Marcações no Markdown

Todo bloco de baixa confiança é precedido de um comentário HTML — invisível na
renderização, visível para quem revisa:

```markdown
<!-- REVISAR: tabela precisa de conferência — contém células mescladas, que o Markdown não representa -->
```

---

## Como a IA fica impedida de inventar conteúdo

Não é uma instrução no prompt. É verificação.

### 1. A IA não devolve texto

Ela devolve **operações** de um vocabulário fechado, definido em
[`app/ai/operations.py`](../app/ai/operations.py):

```
merge_blocks          set_heading_level      promote_to_heading
demote_to_paragraph   promote_to_list        mark_as_footnote
mark_as_quote         reorder                set_table_header_rows
flag_review
```

Não existe operação de escrever, corrigir ou substituir. A ausência dela no
vocabulário é a primeira barreira: mesmo um modelo comprometido não tem como
expressar "troque este valor".

### 2. Cada operação é verificada isoladamente

Antes e depois de **cada** operação, o sistema calcula uma impressão digital do
conteúdo do documento. Divergiu, a operação é revertida e registrada como
rejeitada — sem contaminar as operações boas da mesma resposta.

### 3. A impressão digital tem três camadas

Porque uma sozinha é cega para o tipo de dano mais caro.

**Caracteres.** Multiconjunto dos caracteres de conteúdo, ignorando espaços e
hifens (que as operações legítimas mexem). Pega qualquer inserção ou remoção.

**Números.** Multiconjunto das sequências numéricas: `2.400,00`, `03/02/2020`,
`0001234-56.2026.5.21.0001`. Existe porque a camada de caracteres é cega a
transposição — trocar `2.400,00` por `4.200,00` mantém **exatamente** os mesmos
caracteres. Num documento jurídico, é o erro mais caro possível.

**Palavras.** Multiconjunto dos vocábulos. Pega transposição dentro da palavra
(`Silva` → `Sliva`), igualmente invisível para a contagem de caracteres.

As três são insensíveis à ordem, porque reordenar blocos é legítimo.

### 4. A única exceção é reconhecida explicitamente

A dehifenização (`trabalha-` + `dor` → `trabalhador`) altera o vocabulário de
forma legítima. Ela é aceita **apenas** quando cada palavra nova é a
concatenação exata de duas palavras que desapareceram. Qualquer outra mudança de
vocabulário reprova.

### 5. Uma conferência final sobre o documento inteiro

Ao término do estágio, a impressão digital é recalculada contra a linha de base.
Divergiu, entra `AI_CONTENT_DRIFT` como erro no relatório.

### O que os testes provam

[`tests/test_guard.py`](../tests/test_guard.py) exercita cada caminho:

| Teste | Verifica |
|---|---|
| `test_guard_barra_transposicao_de_valor_monetario` | `2.400,00` → `4.200,00` é barrado |
| `test_guard_barra_transposicao_de_data` | `03/02/2020` → `02/03/2020` é barrado |
| `test_guard_barra_transposicao_dentro_da_palavra` | `Reclamante` → `Recalmante` é barrado |
| `test_guard_barra_acrescimo_de_texto` | Palavra acrescentada é barrada |
| `test_guard_barra_remocao_de_bloco` | Bloco removido é barrado |
| `test_dehifenizacao_continua_aceita` | A exceção legítima segue passando |
| `test_uniao_de_paragrafos_preserva_conteudo` | União legítima é aceita |
| `test_reordenacao_preserva_conteudo` | Reordenação legítima é aceita |

### O que isso não cobre

O guard protege contra alteração de conteúdo. Ele **não** impede que a IA
proponha uma reorganização estruturalmente ruim — um título promovido ao nível
errado, por exemplo. Isso é degradação de forma, não de conteúdo, e o relatório
registra toda operação aplicada em `ia.applied_detail` para conferência.

E, naturalmente, o guard não interfere no que o provedor faz com os dados que
recebe. Essa é uma decisão de privacidade, tomada antes, no momento de autorizar
o envio.
