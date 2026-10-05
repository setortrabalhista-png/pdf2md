"""União de tabela partida pela quebra de página.

Regressão de um travamento real: unir a tabela da página 1 com a da página 2
esvaziava a lista da página 2, e a iteração seguinte chamava `max()` sobre ela.
O estágio de tabelas não é crítico, então a conversão sobrevivia — mas perdia a
união e registrava um erro no relatório.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.model.blocks import TableBlock, TableCell
from app.model.document import DocumentMeta, DocumentModel, PageInfo
from app.model.geometry import BBox
from app.pipeline.options import ConversionOptions
from app.pipeline.runner import PipelineContext
from app.pipeline.s05_tables import _merge_across_pages

ALTURA_A4 = 842.0


def _tabela(id_: str, pagina: int, y0: float, y1: float, linhas: int = 3,
            colunas: int = 2, header_rows: int = 0) -> TableBlock:
    return TableBlock(
        id=id_,
        page=pagina,
        bbox=BBox(x0=50, y0=y0, x1=500, y1=y1),
        rows=[
            [TableCell(text=f"{id_}{i}c{j}") for j in range(colunas)]
            for i in range(linhas)
        ],
        header_rows=header_rows,
        engine="lines",
    )


def _contexto(tabelas: list[TableBlock], paginas: int):
    doc = DocumentModel(
        meta=DocumentMeta(
            source_filename="x.pdf", source_sha256="x", size_bytes=1, page_count=paginas
        ),
        pages=[PageInfo(number=n, width=595, height=ALTURA_A4)
               for n in range(1, paginas + 1)],
    )
    doc.blocks = list(tabelas)
    ctx = PipelineContext(
        pdf_path=Path("x.pdf"), out_dir=Path("."), options=ConversionOptions()
    )
    ctx.doc = doc
    return ctx, doc


# ── O caso que travava ────────────────────────────────────────────────────


def test_tabela_em_tres_paginas_nao_trava():
    """Regressão: max() sobre a lista esvaziada pela união anterior."""
    tabelas = [
        _tabela("t1", 1, 600, 800, linhas=3),
        _tabela("t2", 2, 60, 800, linhas=4),
        _tabela("t3", 3, 60, 200, linhas=2),
    ]
    ctx, doc = _contexto(tabelas, 3)

    unidas = _merge_across_pages(ctx, tabelas)

    assert unidas == 2
    assert len(doc.blocks) == 1
    # Nenhuma linha se perde no caminho.
    assert doc.blocks[0].n_rows == 3 + 4 + 2


def test_tabela_em_quatro_paginas():
    """Cartão de ponto e demonstrativo de verbas costumam ocupar várias."""
    tabelas = [
        _tabela("t1", 1, 600, 800, linhas=2),
        _tabela("t2", 2, 60, 800, linhas=2),
        _tabela("t3", 3, 60, 800, linhas=2),
        _tabela("t4", 4, 60, 200, linhas=2),
    ]
    ctx, doc = _contexto(tabelas, 4)

    assert _merge_across_pages(ctx, tabelas) == 3
    assert len(doc.blocks) == 1
    assert doc.blocks[0].n_rows == 8


def test_conteudo_fica_na_ordem_certa():
    tabelas = [
        _tabela("A", 1, 600, 800, linhas=2),
        _tabela("B", 2, 60, 200, linhas=2),
    ]
    ctx, doc = _contexto(tabelas, 2)
    _merge_across_pages(ctx, tabelas)

    textos = [linha[0].text for linha in doc.blocks[0].rows]
    assert textos == ["A0c0", "A1c0", "B0c0", "B1c0"]


# ── O que não deve ser unido ──────────────────────────────────────────────


def test_tabelas_longe_das_bordas_nao_se_unem():
    """Duas tabelas independentes no meio das páginas continuam separadas."""
    tabelas = [_tabela("a", 1, 100, 300), _tabela("b", 2, 400, 600)]
    ctx, doc = _contexto(tabelas, 2)

    assert _merge_across_pages(ctx, tabelas) == 0
    assert len(doc.blocks) == 2


def test_numero_de_colunas_diferente_nao_une():
    tabelas = [
        _tabela("a", 1, 600, 800, colunas=3),
        _tabela("b", 2, 60, 200, colunas=2),
    ]
    ctx, doc = _contexto(tabelas, 2)

    assert _merge_across_pages(ctx, tabelas) == 0
    assert len(doc.blocks) == 2


def test_paginas_nao_consecutivas_nao_se_unem():
    tabelas = [_tabela("a", 1, 600, 800), _tabela("b", 3, 60, 200)]
    ctx, doc = _contexto(tabelas, 3)

    assert _merge_across_pages(ctx, tabelas) == 0
    assert len(doc.blocks) == 2


def test_cabecalho_repetido_na_continuacao_e_descartado():
    """O cabeçalho reimpresso no topo da página seguinte viraria linha de dados."""
    primeira = _tabela("a", 1, 600, 800, linhas=3, header_rows=1)
    segunda = _tabela("b", 2, 60, 200, linhas=3, header_rows=1)
    # A continuação repete o cabeçalho da primeira.
    segunda.rows[0] = [TableCell(text=c.text) for c in primeira.rows[0]]

    ctx, doc = _contexto([primeira, segunda], 2)
    assert _merge_across_pages(ctx, [primeira, segunda]) == 1
    # 3 + 3 - 1 cabeçalho repetido
    assert doc.blocks[0].n_rows == 5


def test_uma_tabela_so_nao_quebra():
    tabelas = [_tabela("a", 1, 600, 800)]
    ctx, _ = _contexto(tabelas, 1)
    assert _merge_across_pages(ctx, tabelas) == 0


def test_sem_tabela_alguma():
    ctx, _ = _contexto([], 2)
    assert _merge_across_pages(ctx, []) == 0


@pytest.mark.parametrize("paginas", [2, 3, 5, 8])
def test_correntes_de_varios_tamanhos(paginas):
    """Uma tabela que ocupa N páginas vira uma só, com todas as linhas."""
    tabelas = []
    for n in range(1, paginas + 1):
        y0 = 600 if n == 1 else 60
        y1 = 200 if n == paginas else 800
        tabelas.append(_tabela(f"t{n}", n, y0, y1, linhas=2))

    ctx, doc = _contexto(tabelas, paginas)

    assert _merge_across_pages(ctx, tabelas) == paginas - 1
    assert len(doc.blocks) == 1
    assert doc.blocks[0].n_rows == 2 * paginas
