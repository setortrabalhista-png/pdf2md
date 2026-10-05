"""Serialização de tabela para Markdown (GFM).

Markdown não representa célula mesclada nem quebra de linha dentro de célula. As
duas limitações são tratadas explicitamente:

* mescla → o conteúdo fica na primeira célula e as continuações saem vazias; a
  tabela é marcada para revisão pelo estágio 05;
* quebra de linha → vira `<br>`, que o GFM renderiza.

Nunca inventamos coluna nem descartamos célula: se a tabela chegou torta, ela
sai torta e sinalizada, e não silenciosamente "consertada".
"""

from __future__ import annotations

import re

from app.model.blocks import Alignment, TableBlock

_PIPE = re.compile(r"\|")
_WS = re.compile(r"[ \t]+")

ALIGN_MARKERS = {
    Alignment.LEFT: ":---",
    Alignment.CENTER: ":---:",
    Alignment.RIGHT: "---:",
}


def escape_cell(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _PIPE.sub(r"\|", cleaned)
    cleaned = cleaned.replace("\n", "<br>")
    return _WS.sub(" ", cleaned).strip()


def render_table(table: TableBlock) -> str:
    if not table.rows:
        return ""

    n_cols = table.n_cols
    if n_cols == 0:
        return ""

    grid: list[list[str]] = []
    for row in table.rows:
        cells = [escape_cell(c.text) for c in row]
        cells.extend([""] * (n_cols - len(cells)))
        grid.append(cells[:n_cols])

    alignments = list(table.alignments)
    alignments.extend([Alignment.LEFT] * (n_cols - len(alignments)))
    alignments = alignments[:n_cols]

    header_rows = max(0, min(table.header_rows, len(grid) - 1))

    lines: list[str] = []

    if header_rows == 0:
        # GFM exige linha de cabeçalho. Sem cabeçalho detectado, emitimos uma
        # linha vazia — preserva as colunas sem promover dado a título.
        lines.append("| " + " | ".join([""] * n_cols) + " |")
        lines.append("| " + " | ".join(ALIGN_MARKERS[a] for a in alignments) + " |")
        body_start = 0
    else:
        if header_rows == 1:
            lines.append("| " + " | ".join(grid[0]) + " |")
        else:
            # Cabeçalho de múltiplas linhas: funde com <br> numa só.
            fused = [
                "<br>".join(filter(None, (grid[r][c] for r in range(header_rows))))
                for c in range(n_cols)
            ]
            lines.append("| " + " | ".join(fused) + " |")
        lines.append("| " + " | ".join(ALIGN_MARKERS[a] for a in alignments) + " |")
        body_start = header_rows

    for row in grid[body_start:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)
