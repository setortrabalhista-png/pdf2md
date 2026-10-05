"""DocumentModel — a representação intermediária completa de um PDF.

Este objeto é a fonte de verdade do sistema. O Markdown, o relatório de
qualidade e qualquer exportador futuro são derivados dele; nenhum estágio do
pipeline escreve texto formatado diretamente.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.model.blocks import Block, BlockKind, block_text
from app.model.geometry import BBox
from app.model.provenance import Warning_


class PageRoute(str, Enum):
    """Como a página foi (ou será) processada."""

    NATIVE = "native"    # camada de texto suficiente
    OCR = "ocr"          # sem texto útil, exige reconhecimento óptico
    HYBRID = "hybrid"    # texto parcial + regiões de imagem com texto
    EMPTY = "empty"      # página genuinamente em branco


class PageInfo(BaseModel):
    number: int                       # 1-indexed
    width: float
    height: float
    rotation: int = 0
    route: PageRoute = PageRoute.NATIVE
    # Fora do recorte pedido pelo usuário: nenhum estágio deve tocá-la.
    excluded: bool = False
    # Sem camada de texto e com tinta na página: precisaria de OCR. Fica
    # registrado mesmo com o OCR desligado, para que a página não suma em
    # silêncio do resultado.
    needs_ocr: bool = False

    # Métricas de triagem (estágio 01)
    native_char_count: int = 0
    image_count: int = 0
    image_area_ratio: float = 0.0     # fração da página coberta por imagens
    text_area_ratio: float = 0.0
    vector_line_count: int = 0

    # Preenchidos após o OCR
    ocr_char_count: int = 0
    ocr_mean_confidence: float | None = None

    # Cabeçalho/rodapé identificados e removidos do corpo
    header_text: str | None = None
    footer_text: str | None = None
    # Caracteres descartados deliberadamente (carimbo, numeração, assinatura
    # eletrônica). Descontados da base de cobertura: removê-los é acerto, não
    # perda, e contá-los como perda produziria alarme falso em todo processo.
    boilerplate_char_count: int = 0

    @property
    def total_char_count(self) -> int:
        return self.native_char_count + self.ocr_char_count

    @property
    def expected_char_count(self) -> int:
        """Texto que deveria chegar ao Markdown, já sem o boilerplate."""
        return max(0, self.total_char_count - self.boilerplate_char_count)


class DocumentMeta(BaseModel):
    source_filename: str
    source_sha256: str
    size_bytes: int
    page_count: int
    is_encrypted: bool = False
    pdf_title: str | None = None
    pdf_author: str | None = None
    pdf_producer: str | None = None
    pdf_creation_date: str | None = None


class DocumentModel(BaseModel):
    meta: DocumentMeta
    pages: list[PageInfo] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    warnings: list[Warning_] = Field(default_factory=list)

    # Conteúdo removido do fluxo principal, retido para auditoria
    discarded_headers: list[str] = Field(default_factory=list)
    discarded_footers: list[str] = Field(default_factory=list)

    # Preenchido pelo estágio de imagens
    assets: list[str] = Field(default_factory=list)

    # Diário do pipeline: estágio -> duração e observações
    stage_log: list[dict] = Field(default_factory=list)

    # ── Consultas de conveniência ──────────────────────────────────────

    def page(self, number: int) -> PageInfo | None:
        for p in self.pages:
            if p.number == number:
                return p
        return None

    def blocks_of_page(self, number: int) -> list:
        return [b for b in self.blocks if b.page == number]

    def blocks_of_kind(self, kind: BlockKind) -> list:
        return [b for b in self.blocks if b.kind == kind]

    def plain_text(self) -> str:
        return "\n".join(t for t in (block_text(b) for b in self.blocks) if t)

    def char_count(self) -> int:
        return sum(len(block_text(b)) for b in self.blocks)

    def add_warning(self, w: Warning_) -> None:
        self.warnings.append(w)

    def resequence(self) -> None:
        """Renumera a ordem de leitura após inserções/remoções/reordenações."""
        for i, b in enumerate(self.blocks):
            b.order = i

    def next_block_id(self, prefix: str = "b") -> str:
        return f"{prefix}{len(self.blocks):05d}"

    def full_bbox_of_page(self, number: int) -> BBox | None:
        bs = self.blocks_of_page(number)
        if not bs:
            return None
        box = bs[0].bbox
        for b in bs[1:]:
            box = box.union(b.bbox)
        return box
