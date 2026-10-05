"""Testes do verificador anti-alucinação.

Estes testes são a prova da afirmação "a IA não pode inventar conteúdo". Cada
caso simula uma operação maliciosa ou desastrada e confere que o guard a barra.
"""

from __future__ import annotations

from app.ai import guard
from app.ai.operations import (
    DemoteToParagraph,
    MarkAsQuote,
    MergeBlocks,
    OperationError,
    PromoteToHeading,
    PromoteToList,
    Reorder,
    SetHeadingLevel,
    apply_operation,
)
from app.model.blocks import HeadingBlock, ParagraphBlock
from app.model.document import DocumentMeta, DocumentModel, PageInfo
from app.model.geometry import BBox


def _documento() -> DocumentModel:
    doc = DocumentModel(
        meta=DocumentMeta(
            source_filename="teste.pdf", source_sha256="x", size_bytes=1, page_count=1
        ),
        pages=[PageInfo(number=1, width=595, height=842)],
    )
    doc.blocks = [
        HeadingBlock(
            id="h1", page=1, bbox=BBox(x0=0, y0=0, x1=100, y1=12), level=1,
            text="DOS FATOS",
        ),
        ParagraphBlock(
            id="p1", page=1, bbox=BBox(x0=0, y0=20, x1=100, y1=32),
            text="O Reclamante foi admitido em 03/02/2020 com salário de R$ 2.400,00",
        ),
        ParagraphBlock(
            id="p2", page=1, bbox=BBox(x0=0, y0=40, x1=100, y1=52),
            text="e dispensado sem justa causa em 14/06/2026.",
        ),
    ]
    doc.resequence()
    return doc


def _aplicar_com_guard(doc, operacao) -> tuple[bool, str]:
    """Devolve (aceita, motivo) reproduzindo o que o estágio 09 faz."""
    baseline = guard.fingerprint(doc)
    snapshot = [b.model_copy(deep=True) for b in doc.blocks]
    try:
        apply_operation(doc, operacao)
    except OperationError as exc:
        doc.blocks = snapshot
        return False, f"inválida: {exc}"

    depois = guard.fingerprint(doc)
    if not guard.content_preserved(baseline, depois):
        motivo = guard.diff_summary(baseline, depois)
        doc.blocks = snapshot
        return False, motivo
    return True, ""


# ── Operações legítimas passam ────────────────────────────────────────────


def test_uniao_de_paragrafos_preserva_conteudo():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, MergeBlocks(ids=["p1", "p2"]))
    assert aceita, motivo
    assert len(doc.blocks) == 2
    assert "14/06/2026" in doc.blocks[1].text
    assert "03/02/2020" in doc.blocks[1].text


def test_mudanca_de_nivel_de_titulo_passa():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, SetHeadingLevel(id="h1", level=3))
    assert aceita, motivo
    assert doc.blocks[0].level == 3


def test_reordenacao_preserva_conteudo():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, Reorder(ids=["p2", "p1"]))
    assert aceita, motivo
    assert [b.id for b in doc.blocks] == ["h1", "p2", "p1"]


def test_promocao_a_titulo_e_rebaixamento_sao_reversiveis():
    doc = _documento()
    assert _aplicar_com_guard(doc, PromoteToHeading(id="p1", level=2))[0]
    assert _aplicar_com_guard(doc, DemoteToParagraph(id="p1"))[0]
    assert "R$ 2.400,00" in next(b for b in doc.blocks if b.id == "p1").text


def test_promocao_a_lista_passa():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, PromoteToList(ids=["p1"], ordered=False))
    assert aceita, motivo


def test_marcar_como_citacao_passa():
    doc = _documento()
    assert _aplicar_com_guard(doc, MarkAsQuote(id="p1"))[0]


# ── Tentativas de alterar conteúdo são barradas ───────────────────────────


class _OperacaoMaliciosa:
    """Simula um provedor comprometido devolvendo algo fora do vocabulário."""

    def __init__(self, op_name: str, acao):
        self.op = type("K", (), {"value": op_name})()
        self.acao = acao
        self.reason = ""


def test_guard_barra_alteracao_de_valor():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    snapshot = [b.model_copy(deep=True) for b in doc.blocks]

    # "Corrigir" um valor é exatamente o que não pode acontecer numa peça.
    doc.blocks[1].text = doc.blocks[1].text.replace("2.400,00", "4.200,00")

    assert not guard.content_preserved(baseline, guard.fingerprint(doc))
    doc.blocks = snapshot
    assert guard.content_preserved(baseline, guard.fingerprint(doc))


def test_guard_barra_acrescimo_de_texto():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text += " conforme documentos anexos"
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))


def test_guard_barra_remocao_de_bloco():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks.pop()
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))


def test_guard_ignora_espaco_e_hifen():
    """União de linhas insere espaço e dehifenização remove hífen: legítimos."""
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text = doc.blocks[1].text.replace(" ", "  ").replace("O", "O-")
    assert guard.content_preserved(baseline, guard.fingerprint(doc))


def test_guard_e_insensivel_a_caixa():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[0].text = doc.blocks[0].text.lower()
    assert guard.content_preserved(baseline, guard.fingerprint(doc))


def test_operacao_em_bloco_inexistente_e_rejeitada():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, SetHeadingLevel(id="nao-existe", level=2))
    assert not aceita
    assert "inválida" in motivo


def test_reordenacao_nao_contigua_e_rejeitada():
    doc = _documento()
    doc.blocks.append(
        ParagraphBlock(id="p3", page=1, bbox=BBox(x0=0, y0=60, x1=100, y1=72), text="terceiro")
    )
    doc.resequence()
    aceita, _ = _aplicar_com_guard(doc, Reorder(ids=["h1", "p3"]))
    assert not aceita


def test_uniao_de_tipos_incompativeis_e_rejeitada():
    doc = _documento()
    aceita, motivo = _aplicar_com_guard(doc, MergeBlocks(ids=["h1", "p1"]))
    assert not aceita
    assert "inválida" in motivo


def test_diff_descreve_a_alteracao():
    doc = _documento()
    antes = guard.fingerprint(doc)
    doc.blocks[1].text += "XYZ"
    resumo = guard.diff_summary(antes, guard.fingerprint(doc))
    assert "acrescentou" in resumo


# ── Provedor nulo ─────────────────────────────────────────────────────────


def test_provedor_nulo_nao_propoe_nada():
    from app.ai.providers.null import NullProvider

    provedor = NullProvider()
    assert provedor.sends_data_externally is False
    assert provedor.propose({"blocks": []}, 10).operations == []


# ── Transposição: o buraco que a contagem de caracteres não vê ────────────


def test_guard_barra_transposicao_de_valor_monetario():
    """2.400,00 -> 4.200,00 tem exatamente os mesmos caracteres.

    É a alteração mais perigosa possível numa peça, e uma verificação apenas
    por contagem de caracteres a deixaria passar.
    """
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text = doc.blocks[1].text.replace("2.400,00", "4.200,00")
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))
    assert "número" in guard.diff_summary(baseline, guard.fingerprint(doc))


def test_guard_barra_transposicao_de_data():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text = doc.blocks[1].text.replace("03/02/2020", "02/03/2020")
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))


def test_guard_barra_transposicao_dentro_da_palavra():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text = doc.blocks[1].text.replace("Reclamante", "Recalmante")
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))


def test_dehifenizacao_continua_aceita():
    """A única alteração legítima de vocabulário tem de seguir passando."""
    doc = _documento()
    doc.blocks[1].text = "o trabalha-"
    doc.blocks[2].text = "dor rural compareceu"

    aceita, motivo = _aplicar_com_guard(doc, MergeBlocks(ids=["p1", "p2"]))
    assert aceita, motivo
    assert doc.blocks[1].text == "o trabalhador rural compareceu"


def test_palavra_nova_sem_origem_e_barrada():
    doc = _documento()
    baseline = guard.fingerprint(doc)
    doc.blocks[1].text += " indevidamente"
    assert not guard.content_preserved(baseline, guard.fingerprint(doc))
