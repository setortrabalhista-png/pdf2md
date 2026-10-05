"""Orquestrador do pipeline.

Cada estágio é uma função pura sobre o contexto: recebe o `PipelineContext`,
muta o `DocumentModel` e devolve nada. O runner cuida de ordem, progresso,
cronometragem e isolamento de falhas — um estágio opcional que quebra vira
warning, não derruba a conversão.
"""

from __future__ import annotations

import contextlib
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pymupdf

from app.config import Settings, get_settings
from app.model.document import DocumentModel
from app.model.provenance import Severity, Warning_
from app.pipeline.options import ConversionOptions

log = logging.getLogger("pdf2md.pipeline")

ProgressFn = Callable[[str, float, str], None]


def _noop_progress(stage: str, fraction: float, message: str) -> None:
    pass


@dataclass
class PipelineContext:
    pdf_path: Path
    out_dir: Path
    options: ConversionOptions
    settings: Settings = field(default_factory=get_settings)
    doc: DocumentModel | None = None
    pdf: pymupdf.Document | None = None
    progress: ProgressFn = _noop_progress
    scratch: dict = field(default_factory=dict)   # dados entre estágios vizinhos

    @property
    def assets_dir(self) -> Path:
        return self.out_dir / self.settings.md_assets_dir

    def warn(
        self,
        code: str,
        message: str,
        *,
        page: int | None = None,
        block_id: str | None = None,
        severity: Severity = Severity.WARNING,
        **detail,
    ) -> None:
        if self.doc is None:
            return
        self.doc.add_warning(
            Warning_(
                code=code,
                severity=severity,
                message=message,
                page=page,
                block_id=block_id,
                detail=detail,
            )
        )

    def active_pages(self) -> list[int]:
        """Páginas a processar, respeitando o recorte pedido pelo usuário."""
        if self.doc is None:
            return []
        allowed = set(self.options.page_range) if self.options.page_range else None
        return [p.number for p in self.doc.pages if allowed is None or p.number in allowed]


class Stage(Protocol):
    name: str
    label: str
    weight: float
    critical: bool

    def __call__(self, ctx: PipelineContext) -> None: ...


@dataclass
class StageSpec:
    name: str
    label: str          # texto mostrado ao usuário
    fn: Callable[[PipelineContext], None]
    weight: float = 1.0
    critical: bool = True   # False ⇒ falha vira warning e o pipeline segue
    enabled: Callable[[PipelineContext], bool] = lambda ctx: True


def build_default_stages() -> list[StageSpec]:
    # Import tardio: mantém o módulo leve e evita ciclos.
    from app.pipeline import (
        s01_ingest,
        s02_layout,
        s03_ocr,
        s04_regions,
        s05_tables,
        s06_images,
        s07_semantics,
        s08_reading_order,
        s09_ai_refine,
        s10_render,
    )

    return [
        StageSpec("ingest", "Analisando o PDF", s01_ingest.run, weight=0.5),
        StageSpec("layout", "Lendo a estrutura das páginas", s02_layout.run, weight=2.0),
        StageSpec(
            "ocr",
            "Reconhecendo texto de páginas digitalizadas",
            s03_ocr.run,
            weight=5.0,
            critical=False,
        ),
        StageSpec("regions", "Detectando regiões e recorrências", s04_regions.run, weight=1.5),
        StageSpec(
            "tables",
            "Extraindo tabelas",
            s05_tables.run,
            weight=2.5,
            critical=False,
            enabled=lambda ctx: ctx.options.extract_tables,
        ),
        StageSpec(
            "images",
            "Extraindo imagens",
            s06_images.run,
            weight=1.5,
            critical=False,
            enabled=lambda ctx: ctx.options.extract_images,
        ),
        StageSpec("semantics", "Identificando títulos e listas", s07_semantics.run, weight=1.5),
        StageSpec("reading_order", "Ordenando o conteúdo", s08_reading_order.run, weight=1.0),
        StageSpec(
            "ai",
            "Refinando a estrutura com IA",
            s09_ai_refine.run,
            weight=3.0,
            critical=False,
            enabled=lambda ctx: ctx.options.ai_enabled and ctx.options.ai_consent,
        ),
        StageSpec("render", "Gerando o Markdown", s10_render.run, weight=1.0),
    ]


def run_pipeline(
    pdf_path: Path,
    out_dir: Path,
    options: ConversionOptions | None = None,
    progress: ProgressFn | None = None,
    settings: Settings | None = None,
    stages: list[StageSpec] | None = None,
) -> DocumentModel:
    """Executa o pipeline completo e devolve o IR final."""
    ctx = PipelineContext(
        pdf_path=Path(pdf_path),
        out_dir=Path(out_dir),
        options=options or ConversionOptions(),
        settings=settings or get_settings(),
        progress=progress or _noop_progress,
    )
    ctx.out_dir.mkdir(parents=True, exist_ok=True)

    specs = [s for s in (stages or build_default_stages())]
    total_weight = sum(s.weight for s in specs if s.enabled(ctx)) or 1.0
    done_weight = 0.0

    try:
        for spec in specs:
            if not spec.enabled(ctx):
                log.debug("estágio %s desabilitado", spec.name)
                continue

            ctx.progress(spec.name, done_weight / total_weight, spec.label)
            started = time.perf_counter()
            try:
                spec.fn(ctx)
                status = "ok"
                error = None
            except Exception as exc:
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
                log.error("estágio %s falhou\n%s", spec.name, traceback.format_exc())
                if spec.critical:
                    raise
                ctx.warn(
                    "STAGE_FAILED",
                    f"O estágio '{spec.label}' falhou e foi ignorado: {error}",
                    severity=Severity.ERROR,
                    stage=spec.name,
                )

            elapsed = time.perf_counter() - started
            if ctx.doc is not None:
                ctx.doc.stage_log.append(
                    {
                        "stage": spec.name,
                        "label": spec.label,
                        "status": status,
                        "seconds": round(elapsed, 3),
                        "error": error,
                    }
                )
            done_weight += spec.weight

        # Estágio "finish", não "done": quem declara o job concluído é o
        # gerente de jobs, depois de gravar o relatório. Usar o mesmo nome
        # aqui criava uma corrida com o stream de progresso.
        ctx.progress("finish", 1.0, "Finalizando")
        assert ctx.doc is not None, "o estágio de ingest não produziu documento"
        return ctx.doc

    finally:
        if ctx.pdf is not None:
            # Fechar já-fechado ou documento corrompido não importa aqui.
            with contextlib.suppress(Exception):
                ctx.pdf.close()
