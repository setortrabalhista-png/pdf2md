# Arquitetura

## O princípio: nada de PDF direto para string

O erro clássico desse tipo de conversor é concatenar texto até formar um
Markdown. Perde-se rastreabilidade, não há como validar o resultado e não há
onde a IA operar com segurança.

Aqui o PDF vira primeiro um **modelo intermediário de documento** (IR): uma
lista de blocos tipados, cada um com página, geometria, ordem de leitura,
origem e confiança. O Markdown é apenas o serializador final.

```
PDF ──► DocumentModel (IR) ──► documento.md + assets/ + relatório.json
             │
             ├─► validação — confronta o IR contra o PDF de origem
             ├─► IA opcional — opera sobre blocos, nunca sobre texto solto
             └─► exportadores futuros (DOCX, HTML) sem refazer nada
```

Três consequências práticas:

1. **Auditabilidade.** Todo bloco sabe de onde veio (`native`, `ocr`, `vector`,
   `heuristic`, `ai`) e com que confiança. O relatório de qualidade é derivado,
   não estimado.
2. **Segurança da IA.** Operar sobre blocos identificados permite verificar,
   caractere a caractere, que nada foi criado ou destruído.
3. **Reprodutibilidade.** Mesmo PDF, mesma saída — a suíte tem um teste que
   exige isso.

---

## Os dez estágios

```
S01 Ingest         hash, metadados, e a rota de cada página
S02 Layout         PyMuPDF: spans com fonte, réguas vetoriais, retângulos de imagem
S03 OCR            seletivo, página a página
S04 Regiões        cabeçalho/rodapé recorrente, malhas de tabela, notas, colunas
S05 Tabelas        três motores, com score de confiança
S06 Imagens        bytes originais, recomposição de scans fatiados, figuras vetoriais
S07 Semântica      títulos, listas, parágrafos, citações, notas
S08 Ordem          ordem de leitura e junção através da quebra de página
S09 IA             opcional, sob verificação
S10 Render         Markdown, assets e relatório
```

Cada estágio é uma função sobre o contexto. O orquestrador
([`app/pipeline/runner.py`](../app/pipeline/runner.py)) cuida de ordem,
progresso, cronometragem e isolamento de falhas: um estágio não-crítico que
quebra vira aviso no relatório, não derruba a conversão.

---

## As decisões que importam

### OCR por página, não por documento

Processo judicial é quase sempre híbrido: petição nativa do PJe seguida de
anexos digitalizados. Uma decisão global "é escaneado / não é" degrada metade do
documento.

O S01 mede, **por página**, a quantidade de caracteres visíveis, a fração da
área coberta por texto e a fração coberta por imagem, e classifica em quatro
rotas:

| Rota | Situação | Tratamento |
|---|---|---|
| `native` | camada de texto suficiente | extração direta |
| `ocr` | sem texto, com tinta na página | reconhecimento óptico |
| `hybrid` | texto + imagem dominante | ambos; o OCR só complementa |
| `empty` | página em branco | nada |

Em página híbrida, o OCR roda na página inteira mas só os blocos que **não**
colidem com texto nativo são aproveitados: a camada original é sempre superior
a reconhecê-la de novo.

### Cabeçalho e rodapé por recorrência, não por posição

"Documento assinado eletronicamente por…", numeração de folhas e o brasão do
tribunal poluem todas as páginas. Descartar tudo que está no topo seria perder o
título da peça.

O S04 normaliza os blocos das faixas superior e inferior (dígitos viram `#`) e
os agrupa por similaridade difusa. O que aparece em pelo menos 60% das páginas
**elegíveis** é recorrente — elegível é a página que tem algum bloco naquela
faixa, e não o total de páginas: num processo com metade das folhas
digitalizadas, medir sobre o total afundaria a taxa e o carimbo escaparia para o
corpo.

Nada é apagado de verdade: o texto removido vai para o cabeçalho YAML do
Markdown e para o relatório.

### Tabela pelos vetores, não por biblioteca externa

O motor primário lê as **réguas realmente desenhadas** no PDF
(`page.get_drawings()`), funde os traços picotados que compõem uma borda, e
encontra as malhas fechadas como componentes conexos de um grafo bipartido
horizontal↔vertical. Cada célula é preenchida com os spans cujo centro cai
dentro dela.

Não exige Ghostscript, OpenCV nem Java. Camelot e Tabula entram apenas como
segunda opinião, e apenas quando a confiança do motor primário fica abaixo do
limiar.

**Células mescladas** são detectadas pela ausência da régua que deveria separar
duas colunas naquela faixa de linhas. O conteúdo vai para a primeira célula e as
continuações saem vazias — o Markdown não representa mescla, então a tabela é
marcada para revisão em vez de sair silenciosamente errada.

Sem bordas desenhadas, entra o motor de alinhamento
([`tables_stream.py`](../app/extractors/tables_stream.py)). O risco ali é o
falso positivo: parágrafo justificado também tem vãos largos. A defesa é exigir
**estabilidade** — um vão só vira coluna se aparecer aproximadamente na mesma
abscissa na maioria das linhas do grupo. Texto corrido não passa nesse teste.

### Hierarquia de títulos por estilo relativo

Nada de `if tamanho > 14: h1`. O sistema levanta os estilos tipográficos
efetivamente presentes (corpo de fonte arredondado a meio ponto + peso),
identifica o estilo dominante do corpo ponderado por quantidade de caracteres, e
ordena os demais entre si. Um documento inteiro em corpo 10 com títulos em 11
negrito funciona tão bem quanto um com títulos em 20.

A promoção a título combina sinais: estilo acima do corpo, negrito, caixa alta,
centralização, numeração estrutural, brevidade e isolamento do bloco. Nenhum
decide sozinho.

O sinal de **isolamento** existe por causa do OCR: em página digitalizada não há
metadado de fonte, e o corpo é estimado pela altura da caixa da palavra — o que
comprime as diferenças de tamanho e faz o teste de "meio ponto acima" falhar.
A linha sozinha, curta e maior que o corpo é o sinal que sobrevive a essa perda
de resolução.

### Hifenização decidida no documento, não na linha

Remover o hífen de `sócio-administrador` só porque a linha terminou ali é um
erro de conteúdo. O sistema mede a taxa de linhas terminadas em hífen no
documento inteiro; abaixo de 2%, o documento não hifeniza e o hífen é sempre
preservado.

### Marcador de lista nunca é renumerado

`c)` continua `c)`. `3.` continua `3.`. Alíneas e itens são citados em outros
pontos da peça — trocar o número é alterar o documento. Marcador numérico vira
lista ordenada com o número original; alínea e algarismo romano viram item com o
marcador preservado literalmente.

### Recomposição de scans fatiados

Muitos geradores cortam uma página digitalizada em dezenas de faixas
horizontais. Extraí-las uma a uma produziria um Markdown com quarenta imagens de
vinte pixels de altura. Faixas contíguas de mesma largura são recompostas numa
única imagem, rasterizada na densidade original do conteúdo.

E quando a "imagem" é a página inteira de um scan, ela é descartada: o texto já
veio pelo OCR e reproduzi-la só poluiria o resultado.

---

## Estrutura de pastas

```
pdf2md/
├── app/
│   ├── main.py              FastAPI: API + interface, um processo só
│   ├── cli.py               conversão headless
│   ├── config.py            todos os limiares, com a justificativa de cada um
│   │
│   ├── model/               o IR
│   │   ├── geometry.py      BBox e operações
│   │   ├── provenance.py    origem, confiança, avisos
│   │   ├── layout.py        spans, linhas, blocos brutos, palavras
│   │   ├── blocks.py        blocos tipados do IR
│   │   └── document.py      DocumentModel
│   │
│   ├── pipeline/            os dez estágios + orquestrador
│   ├── extractors/          PyMuPDF, OCR, réguas, tabelas, imagens
│   ├── ai/                  DSL de operações, guard, provedores
│   ├── quality/             validação e relatório
│   ├── render/              Markdown e tabelas GFM
│   ├── core/                jobs, workspace, erros
│   ├── api/                 rotas HTTP
│   └── web/                 interface (HTML/CSS/JS puro)
│
├── tests/
│   ├── make_fixtures.py     gera os PDFs sintéticos de referência
│   ├── test_unidades.py     componentes isolados
│   ├── test_guard.py        a prova de que a IA não inventa
│   ├── test_pipeline.py     ponta a ponta sobre PDFs
│   └── test_api.py          HTTP, SSE e privacidade
│
└── docs/
```

---

## Escolhas técnicas, e o que foi descartado

| Escolha | Por quê | Descartado |
|---|---|---|
| **PyMuPDF** como fonte geométrica | Único que entrega spans com fonte, vetores, xrefs de imagem e render de alta resolução na mesma API | pdfminer sozinho — sem vetores nem imagens |
| **pdfplumber** como segunda opinião | Em PDF com codificação de fonte irregular, os dois motores discordam; a divergência vira aviso em vez de passar batido | Confiar num extrator só |
| **Motor de tabela próprio** | Funciona sem dependência de sistema e usa as réguas reais | Camelot como primário — exige Ghostscript e OpenCV |
| **Camelot/Tabula** como reserva | Aproveita o que estiver instalado sem tornar obrigatório | Exigir instalação |
| **Tesseract** + `por.traineddata` | Offline, licença permissiva, saída TSV com **confiança por palavra** — sem isso o relatório seria palpite | PaddleOCR como primário: ~1 GB de modelos e sem wheels para Python recente |
| **IR tipado (Pydantic)** antes do Markdown | Habilita validação, IA segura e exportadores futuros | Construção direta de string |
| **HTML/JS puro** | Zero Node, zero build, um comando sobe tudo; a API não muda se depois virar React | React/Vite |
| **SSE** | Progresso é unidirecional | WebSocket — complexidade sem retorno |
| **Estado em memória** | Um processo, uma máquina, um usuário — que é o modelo de implantação | Redis/Celery |

### Sobre as soluções prontas

Existem conversores fim-a-fim (Docling, Marker) que resolvem boa parte disso com
um `import`. Não servem de base aqui porque trazem modelos de deep learning
pesados e opacos — ruins para o requisito de relatório auditável e para rodar em
máquina de escritório. Mas a arquitetura de estágios permite plugá-los como
**mais um extrator** em S04/S05 se um dia quisermos comparar resultados.

---

## Limites conhecidos

- **Duas colunas** são detectadas de forma conservadora (calha vertical que
  nenhum bloco atravessa). Em peça jurídica a coluna única é a regra, e um falso
  positivo embaralharia o documento inteiro. Layout de três ou mais colunas não
  é tratado.
- **Notas de rodapé** não são vinculadas às suas chamadas no texto. Elas saem na
  posição correta, com o marcador original, mas sem link.
- **Fórmulas matemáticas** não são convertidas para LaTeX; saem como texto.
- **Célula mesclada** é preservada em conteúdo, não em forma — limitação do
  Markdown, sinalizada em vez de escondida.
- O **OCR pode acertar demais**: o modelo de português acentua palavras que o
  original não acentuava. É reconhecimento, não transcrição literal. Para
  conferência textual exata, o PDF continua sendo a fonte.
