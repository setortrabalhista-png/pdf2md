"""Blocos tipados do IR.

Um bloco é a menor unidade semântica que o renderizador sabe transformar em
Markdown. Todos carregam página, geometria e proveniência — o Markdown é apenas
a projeção final destes objetos.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.model.geometry import BBox
from app.model.provenance import Provenance


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    FOOTNOTE = "footnote"
    PAGE_BREAK = "page_break"


class ParagraphStyle(str, Enum):
    BODY = "body"
    QUOTE = "quote"          # citação recuada (ementa, transcrição de doutrina)
    CAPTION = "caption"      # legenda de figura/tabela
    SIGNATURE = "signature"  # bloco de assinatura ao final da peça
    PREFORMATTED = "pre"     # conteúdo com espaçamento significativo


class BaseBlock(BaseModel):
    id: str
    page: int                       # 1-indexed
    bbox: BBox
    order: int = 0                  # posição na ordem de leitura do documento
    column: int = 0                 # índice da coluna (0 = coluna única)
    provenance: Provenance = Field(default_factory=Provenance)
    needs_review: bool = False
    review_reason: str | None = None

    def flag_review(self, reason: str) -> None:
        self.needs_review = True
        self.review_reason = reason


class HeadingBlock(BaseBlock):
    kind: Literal[BlockKind.HEADING] = BlockKind.HEADING
    level: int = Field(ge=1, le=6)
    text: str
    numbering: str | None = None    # "1.2", "II", "Art. 5º" quando detectado


class ParagraphBlock(BaseBlock):
    kind: Literal[BlockKind.PARAGRAPH] = BlockKind.PARAGRAPH
    text: str
    style: ParagraphStyle = ParagraphStyle.BODY
    continues_previous: bool = False  # parágrafo iniciado na página anterior


class ListItemBlock(BaseBlock):
    kind: Literal[BlockKind.LIST_ITEM] = BlockKind.LIST_ITEM
    text: str
    ordered: bool = False
    marker: str = "-"               # marcador original: "a)", "1.", "•", "–"
    level: int = 0                  # profundidade de aninhamento


class TableCell(BaseModel):
    text: str = ""
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False


class Alignment(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TableBlock(BaseBlock):
    kind: Literal[BlockKind.TABLE] = BlockKind.TABLE
    rows: list[list[TableCell]] = Field(default_factory=list)
    header_rows: int = 0
    alignments: list[Alignment] = Field(default_factory=list)
    engine: str = "unknown"
    caption: str | None = None
    continues_previous: bool = False   # tabela que atravessou a quebra de página

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def has_merged_cells(self) -> bool:
        return any(c.rowspan > 1 or c.colspan > 1 for r in self.rows for c in r)


class FigureBlock(BaseBlock):
    kind: Literal[BlockKind.FIGURE] = BlockKind.FIGURE
    asset_path: str                 # caminho relativo: "assets/p003_img001.png"
    caption: str | None = None
    alt: str = ""
    width_px: int = 0
    height_px: int = 0
    is_vector: bool = False         # figura rasterizada a partir de desenho vetorial
    xref: int | None = None         # xref original no PDF (nulo para vetoriais)


class FootnoteBlock(BaseBlock):
    kind: Literal[BlockKind.FOOTNOTE] = BlockKind.FOOTNOTE
    marker: str                     # "1", "*", "[2]"
    text: str


class PageBreakBlock(BaseBlock):
    kind: Literal[BlockKind.PAGE_BREAK] = BlockKind.PAGE_BREAK
    label: str = ""


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListItemBlock | TableBlock | FigureBlock | FootnoteBlock | PageBreakBlock,
    Field(discriminator="kind"),
]


TEXTUAL_KINDS = {
    BlockKind.HEADING,
    BlockKind.PARAGRAPH,
    BlockKind.LIST_ITEM,
    BlockKind.FOOTNOTE,
}


def block_text(block) -> str:
    """Texto plano de qualquer bloco — base para as métricas de fidelidade."""
    kind = getattr(block, "kind", None)
    if kind in TEXTUAL_KINDS:
        return getattr(block, "text", "")
    if kind == BlockKind.TABLE:
        return " ".join(c.text for row in block.rows for c in row)
    if kind == BlockKind.FIGURE:
        return block.caption or ""
    return ""
