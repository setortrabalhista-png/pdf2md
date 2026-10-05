"""Estágio 10 — Escrita da saída.

Produz, no diretório do job:

    <nome-do-pdf>.md
    resultado_processamento.json
    assets/…

A validação roda **antes** da escrita do relatório, para que os avisos que ela
gera entrem no JSON. O Markdown é escrito primeiro porque é o produto; o
relatório descreve o que foi escrito.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from app.pipeline.runner import PipelineContext
from app.quality import report as report_builder
from app.quality import validators
from app.render.filenames import markdown_filename
from app.render.markdown import RenderOptions, render

log = logging.getLogger("pdf2md.s10")

_SLUG_STRIP = re.compile(r"[^a-zA-Z0-9]+")


def slugify(value: str, fallback: str = "documento") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-").lower()
    return slug[:80] or fallback


def run(ctx: PipelineContext) -> None:
    assert ctx.doc is not None
    doc = ctx.doc
    settings = ctx.settings

    ctx.progress("render", 0.92, "Validando o resultado")
    metrics = validators.validate(
        doc, ctx.pdf_path, ctx.scratch, settings.table_min_confidence
    )
    for warning in metrics.pop("new_warnings", []):
        doc.add_warning(warning)

    ctx.progress("render", 0.96, "Gerando o Markdown")
    options = RenderOptions(
        page_markers=ctx.options.page_markers and settings.md_page_markers,
        review_markers=ctx.options.review_markers and settings.md_review_markers,
        footnote_style=settings.md_footnote_style,
    )
    markdown = render(doc, options)

    # O Markdown herda o nome do PDF: num lote, quarenta arquivos
    # chamados "documento.md" seriam inúteis.
    md_path = ctx.out_dir / markdown_filename(
        ctx.pdf_path.name, settings.md_use_source_name
    )
    md_path.write_text(markdown, encoding="utf-8")

    # Registrado antes de montar o relatório: é lá que o nome gerado fica
    # disponível para a interface e para a linha de comando.
    ctx.scratch["markdown_path"] = str(md_path)
    ctx.scratch["markdown_name"] = md_path.name

    report = report_builder.build(doc, metrics, ctx.scratch, ctx.options)
    report_path = ctx.out_dir / "resultado_processamento.json"
    report_builder.write(report, report_path)

    ctx.scratch["report_path"] = str(report_path)
    ctx.scratch["report"] = report
    ctx.scratch["markdown_bytes"] = len(markdown.encode("utf-8"))

    log.info(
        "escrito: %s (%.1f KB) | cobertura %.1f%% | status %s",
        md_path.name,
        len(markdown.encode("utf-8")) / 1024,
        report["text_accuracy"],
        report["resumo"]["status"],
    )
