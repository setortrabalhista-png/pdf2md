"""Serialização do IR para Markdown.

O renderizador é deliberadamente burro: ele não decide nada, só projeta o que os
estágios anteriores já resolveram. Toda inteligência mora no IR — é isso que
mantém a saída reproduzível e auditável.

Convenções da saída:

* `<!-- página N -->`               marcação discreta de quebra de página
* `<!-- REVISAR: motivo -->`        precede todo bloco de baixa confiança
* cabeçalhos/rodapés recorrentes    não entram no corpo; ficam no cabeçalho YAML
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.model.blocks import (
    BlockKind,
    ParagraphStyle,
)
from app.model.document import DocumentModel
from app.render.tables_md import render_table

# Caracteres com significado em Markdown que precisam de escape no início da linha.
_LEADING_MARKUP = re.compile(r"^(\s*)([#>*+\-=]|\d+[.)])(\s)")
_INLINE_MARKUP = re.compile(r"([*_`~\[\]])")


@dataclass
class RenderOptions:
    page_markers: bool = True
    review_markers: bool = True
    footnote_style: str = "inline"     # inline | end
    front_matter: bool = True
    escape_inline: bool = False        # escape agressivo, desligado por padrão


def escape_text(text: str, aggressive: bool = False) -> str:
    """Neutraliza marcação acidental sem alterar o conteúdo visível."""
    if not text:
        return ""
    out = text.replace("\\", "\\\\") if aggressive else text
    if aggressive:
        out = _INLINE_MARKUP.sub(r"\\\1", out)
    # Uma linha que começa com "-" ou "1." viraria lista sem querer.
    out = _LEADING_MARKUP.sub(r"\1\\\2\3", out)
    return out


def _front_matter(doc: DocumentModel) -> list[str]:
    meta = doc.meta
    lines = ["---"]
    lines.append(f'origem: "{meta.source_filename}"')
    lines.append(f"paginas: {meta.page_count}")
    lines.append(f"sha256: {meta.source_sha256}")
    if meta.pdf_title:
        lines.append(f'titulo_pdf: "{_yaml_escape(meta.pdf_title)}"')
    if meta.pdf_author:
        lines.append(f'autor_pdf: "{_yaml_escape(meta.pdf_author)}"')
    if doc.discarded_headers:
        lines.append("cabecalhos_removidos:")
        for h in doc.discarded_headers[:10]:
            lines.append(f'  - "{_yaml_escape(h)}"')
    if doc.discarded_footers:
        lines.append("rodapes_removidos:")
        for f in doc.discarded_footers[:10]:
            lines.append(f'  - "{_yaml_escape(f)}"')
    lines.append("---")
    lines.append("")
    return lines


def _yaml_escape(text: str) -> str:
    return " ".join(text.split()).replace('"', '\\"')[:300]


_NUMERIC_MARKER = re.compile(r"^\(?(\d{1,3})[.)]?$")


def _list_item_line(block, escaped_text: str) -> str:
    """Monta o item **preservando o marcador original**.

    Renumerar seria alterar conteúdo: a alínea "c)" de um rol de pedidos e o
    item "3." de uma cláusula são referências citadas em outros pontos da peça.
    Marcador numérico vira lista ordenada com o número original; qualquer outro
    marcador (alíneas, algarismos romanos) é mantido literalmente dentro do item.
    """
    indent = "  " * block.level
    marker = (block.marker or "").strip()

    if block.ordered:
        m = _NUMERIC_MARKER.match(marker)
        if m:
            return f"{indent}{int(m.group(1))}. {escaped_text}"
        # Alínea ou romano: bullet + marcador original preservado no texto.
        return f"{indent}- {marker} {escaped_text}".rstrip()

    return f"{indent}- {escaped_text}"


def render(doc: DocumentModel, options: RenderOptions | None = None) -> str:
    opts = options or RenderOptions()
    out: list[str] = []

    if opts.front_matter:
        out.extend(_front_matter(doc))

    footnotes_at_end: list[tuple[str, str]] = []
    previous_kind = None

    for block in doc.blocks:
        kind = block.kind

        if opts.review_markers and getattr(block, "needs_review", False):
            reason = getattr(block, "review_reason", None) or "conferir"
            out.append(f"<!-- REVISAR: {reason} -->")

        if kind == BlockKind.PAGE_BREAK:
            if opts.page_markers:
                out.append("")
                out.append(f"<!-- página {block.page} -->")
                out.append("")
            previous_kind = kind
            continue

        if kind == BlockKind.HEADING:
            text = block.text.strip()
            if block.numbering:
                text = f"{block.numbering} {text}".strip()
            out.append("")
            out.append("#" * block.level + " " + escape_text(text, False))
            out.append("")

        elif kind == BlockKind.PARAGRAPH:
            body = escape_text(block.text.strip(), opts.escape_inline)
            if not body:
                previous_kind = kind
                continue
            if block.style == ParagraphStyle.QUOTE:
                out.append("")
                for line in body.split("\n"):
                    out.append("> " + line)
                out.append("")
            elif block.style == ParagraphStyle.CAPTION:
                out.append("")
                out.append(f"*{body}*")
                out.append("")
            elif block.style == ParagraphStyle.PREFORMATTED:
                out.append("")
                out.append("```")
                out.append(block.text)
                out.append("```")
                out.append("")
            else:
                out.append("")
                out.append(body)
                out.append("")

        elif kind == BlockKind.LIST_ITEM:
            if previous_kind != BlockKind.LIST_ITEM:
                out.append("")
            out.append(
                _list_item_line(
                    block, escape_text(block.text.strip(), opts.escape_inline)
                )
            )

        elif kind == BlockKind.TABLE:
            rendered = render_table(block)
            if rendered:
                out.append("")
                if block.caption:
                    out.append(f"**{block.caption.strip()}**")
                    out.append("")
                out.append(rendered)
                out.append("")

        elif kind == BlockKind.FIGURE:
            alt = (block.alt or "").replace("]", "\\]").strip()
            out.append("")
            out.append(f"![{alt}]({block.asset_path})")
            if block.caption:
                out.append("")
                out.append(f"*{block.caption.strip()}*")
            out.append("")

        elif kind == BlockKind.FOOTNOTE:
            marker = block.marker.strip() or "*"
            if opts.footnote_style == "end":
                key = f"{block.page}-{marker}"
                footnotes_at_end.append((key, block.text.strip()))
            else:
                out.append("")
                out.append(f"> **[{marker}]** {escape_text(block.text.strip(), False)}")
                out.append("")

        previous_kind = kind

    if footnotes_at_end:
        out.append("")
        out.append("---")
        out.append("")
        out.append("## Notas de rodapé")
        out.append("")
        for key, text in footnotes_at_end:
            out.append(f"[^{key}]: {text}")
        out.append("")

    return _collapse_blank_lines(out)


def _collapse_blank_lines(lines: list[str]) -> str:
    """No máximo uma linha em branco seguida — Markdown legível."""
    result: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        result.append(line.rstrip())

    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()

    return "\n".join(result) + "\n"
