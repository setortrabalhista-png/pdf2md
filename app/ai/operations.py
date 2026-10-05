"""O vocabulário fechado de operações que a IA pode pedir.

A IA nunca devolve texto. Ela devolve **operações** sobre blocos que já existem.
Não há operação de criar, escrever ou reescrever conteúdo — a ausência dela no
vocabulário é a garantia, não uma instrução de prompt.

Cada operação é aplicada isoladamente e passa pelo verificador de `guard.py`
antes de ser aceita.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.model.blocks import (
    BlockKind,
    FootnoteBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    ParagraphStyle,
)
from app.model.provenance import Source

log = logging.getLogger("pdf2md.ai.ops")


class OpKind(str, Enum):
    MERGE_BLOCKS = "merge_blocks"
    SET_HEADING_LEVEL = "set_heading_level"
    PROMOTE_TO_HEADING = "promote_to_heading"
    DEMOTE_TO_PARAGRAPH = "demote_to_paragraph"
    PROMOTE_TO_LIST = "promote_to_list"
    MARK_AS_FOOTNOTE = "mark_as_footnote"
    MARK_AS_QUOTE = "mark_as_quote"
    REORDER = "reorder"
    SET_TABLE_HEADER_ROWS = "set_table_header_rows"
    FLAG_REVIEW = "flag_review"


class BaseOp(BaseModel):
    reason: str = ""


class MergeBlocks(BaseOp):
    op: Literal[OpKind.MERGE_BLOCKS] = OpKind.MERGE_BLOCKS
    ids: list[str] = Field(min_length=2)


class SetHeadingLevel(BaseOp):
    op: Literal[OpKind.SET_HEADING_LEVEL] = OpKind.SET_HEADING_LEVEL
    id: str
    level: int = Field(ge=1, le=6)


class PromoteToHeading(BaseOp):
    op: Literal[OpKind.PROMOTE_TO_HEADING] = OpKind.PROMOTE_TO_HEADING
    id: str
    level: int = Field(ge=1, le=6)


class DemoteToParagraph(BaseOp):
    op: Literal[OpKind.DEMOTE_TO_PARAGRAPH] = OpKind.DEMOTE_TO_PARAGRAPH
    id: str


class PromoteToList(BaseOp):
    op: Literal[OpKind.PROMOTE_TO_LIST] = OpKind.PROMOTE_TO_LIST
    ids: list[str] = Field(min_length=1)
    ordered: bool = False


class MarkAsFootnote(BaseOp):
    op: Literal[OpKind.MARK_AS_FOOTNOTE] = OpKind.MARK_AS_FOOTNOTE
    id: str
    marker: str = "*"


class MarkAsQuote(BaseOp):
    op: Literal[OpKind.MARK_AS_QUOTE] = OpKind.MARK_AS_QUOTE
    id: str


class Reorder(BaseOp):
    op: Literal[OpKind.REORDER] = OpKind.REORDER
    ids: list[str] = Field(min_length=2)


class SetTableHeaderRows(BaseOp):
    op: Literal[OpKind.SET_TABLE_HEADER_ROWS] = OpKind.SET_TABLE_HEADER_ROWS
    id: str
    header_rows: int = Field(ge=0, le=3)


class FlagReview(BaseOp):
    op: Literal[OpKind.FLAG_REVIEW] = OpKind.FLAG_REVIEW
    id: str
    note: str


Operation = Annotated[
    MergeBlocks | SetHeadingLevel | PromoteToHeading | DemoteToParagraph | PromoteToList | MarkAsFootnote | MarkAsQuote | Reorder | SetTableHeaderRows | FlagReview,
    Field(discriminator="op"),
]


class OperationList(BaseModel):
    operations: list[Operation] = Field(default_factory=list)


class OperationError(Exception):
    """A operação não pôde ser aplicada (id inexistente, tipo incompatível…)."""


# ── Aplicação ─────────────────────────────────────────────────────────────


def _index(doc) -> dict[str, int]:
    return {b.id: i for i, b in enumerate(doc.blocks)}


def _require(doc, block_id: str):
    for b in doc.blocks:
        if b.id == block_id:
            return b
    raise OperationError(f"bloco inexistente: {block_id}")


def _mark_ai(block, reason: str) -> None:
    block.provenance.source = Source.AI
    if reason:
        block.provenance.notes.append(f"IA: {reason}")


def apply_operation(doc, op) -> str:
    """Aplica uma operação. Devolve uma descrição do efeito."""
    kind = op.op

    if kind == OpKind.MERGE_BLOCKS:
        blocks = [_require(doc, i) for i in op.ids]
        if any(b.kind not in (BlockKind.PARAGRAPH, BlockKind.LIST_ITEM) for b in blocks):
            raise OperationError("só parágrafos e itens de lista podem ser unidos")
        head = blocks[0]
        for other in blocks[1:]:
            joined = head.text.rstrip()
            tail = other.text.lstrip()
            if joined.endswith("-"):
                head.text = joined[:-1] + tail
            else:
                head.text = (joined + " " + tail).strip()
            head.bbox = head.bbox.union(other.bbox)
            doc.blocks.remove(other)
        _mark_ai(head, op.reason)
        return f"uniu {len(blocks)} blocos em {head.id}"

    if kind == OpKind.SET_HEADING_LEVEL:
        block = _require(doc, op.id)
        if block.kind != BlockKind.HEADING:
            raise OperationError(f"{op.id} não é um título")
        block.level = op.level
        _mark_ai(block, op.reason)
        return f"{op.id} passou a H{op.level}"

    if kind == OpKind.PROMOTE_TO_HEADING:
        block = _require(doc, op.id)
        if block.kind not in (BlockKind.PARAGRAPH, BlockKind.LIST_ITEM):
            raise OperationError(f"{op.id} não pode virar título")
        replacement = HeadingBlock(
            id=block.id,
            page=block.page,
            bbox=block.bbox,
            order=block.order,
            column=block.column,
            level=op.level,
            text=block.text,
            numbering=getattr(block, "marker", None) if block.kind == BlockKind.LIST_ITEM else None,
            provenance=block.provenance,
        )
        doc.blocks[_index(doc)[block.id]] = replacement
        _mark_ai(replacement, op.reason)
        return f"{op.id} promovido a H{op.level}"

    if kind == OpKind.DEMOTE_TO_PARAGRAPH:
        block = _require(doc, op.id)
        if block.kind != BlockKind.HEADING:
            raise OperationError(f"{op.id} não é um título")
        text = block.text
        if block.numbering:
            text = f"{block.numbering} {text}".strip()
        replacement = ParagraphBlock(
            id=block.id,
            page=block.page,
            bbox=block.bbox,
            order=block.order,
            column=block.column,
            text=text,
            provenance=block.provenance,
        )
        doc.blocks[_index(doc)[block.id]] = replacement
        _mark_ai(replacement, op.reason)
        return f"{op.id} rebaixado a parágrafo"

    if kind == OpKind.PROMOTE_TO_LIST:
        changed = []
        idx = _index(doc)
        for block_id in op.ids:
            block = _require(doc, block_id)
            if block.kind != BlockKind.PARAGRAPH:
                raise OperationError(f"{block_id} não é parágrafo")
            replacement = ListItemBlock(
                id=block.id,
                page=block.page,
                bbox=block.bbox,
                order=block.order,
                column=block.column,
                text=block.text,
                ordered=op.ordered,
                marker="1." if op.ordered else "-",
                provenance=block.provenance,
            )
            doc.blocks[idx[block.id]] = replacement
            _mark_ai(replacement, op.reason)
            changed.append(block_id)
        return f"{len(changed)} bloco(s) viraram itens de lista"

    if kind == OpKind.MARK_AS_FOOTNOTE:
        block = _require(doc, op.id)
        if block.kind != BlockKind.PARAGRAPH:
            raise OperationError(f"{op.id} não é parágrafo")
        replacement = FootnoteBlock(
            id=block.id,
            page=block.page,
            bbox=block.bbox,
            order=block.order,
            column=block.column,
            marker=op.marker,
            text=block.text,
            provenance=block.provenance,
        )
        doc.blocks[_index(doc)[block.id]] = replacement
        _mark_ai(replacement, op.reason)
        return f"{op.id} virou nota de rodapé"

    if kind == OpKind.MARK_AS_QUOTE:
        block = _require(doc, op.id)
        if block.kind != BlockKind.PARAGRAPH:
            raise OperationError(f"{op.id} não é parágrafo")
        block.style = ParagraphStyle.QUOTE
        _mark_ai(block, op.reason)
        return f"{op.id} marcado como citação"

    if kind == OpKind.REORDER:
        blocks = [_require(doc, i) for i in op.ids]
        positions = sorted(_index(doc)[b.id] for b in blocks)
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise OperationError("só é possível reordenar blocos contíguos")
        for position, block in zip(positions, blocks, strict=True):
            doc.blocks[position] = block
            _mark_ai(block, op.reason)
        return f"{len(blocks)} blocos reordenados"

    if kind == OpKind.SET_TABLE_HEADER_ROWS:
        block = _require(doc, op.id)
        if block.kind != BlockKind.TABLE:
            raise OperationError(f"{op.id} não é tabela")
        if op.header_rows >= block.n_rows:
            raise OperationError("cabeçalho não pode consumir a tabela inteira")
        block.header_rows = op.header_rows
        _mark_ai(block, op.reason)
        return f"{op.id} com {op.header_rows} linha(s) de cabeçalho"

    if kind == OpKind.FLAG_REVIEW:
        block = _require(doc, op.id)
        block.flag_review(f"apontado pela IA: {op.note}")
        return f"{op.id} marcado para revisão"

    raise OperationError(f"operação desconhecida: {kind}")
