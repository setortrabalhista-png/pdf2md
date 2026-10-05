"""Testes de unidade dos componentes que carregam a maior parte do risco."""

from __future__ import annotations

import pytest

from app.extractors.rulings import find_grids, merge_segments
from app.model.blocks import Alignment, ListItemBlock, TableBlock, TableCell
from app.model.geometry import BBox
from app.pipeline.s07_semantics import (
    detect_list_marker,
    document_uses_hyphenation,
    join_lines,
)
from app.render.markdown import _list_item_line, escape_text
from app.render.tables_md import escape_cell, render_table

# ── Geometria ─────────────────────────────────────────────────────────────


def test_bbox_operacoes():
    a = BBox(x0=0, y0=0, x1=10, y1=10)
    b = BBox(x0=5, y0=5, x1=15, y1=15)
    assert a.area == 100
    assert a.intersection_area(b) == 25
    assert a.union(b).as_tuple() == (0, 0, 15, 15)
    assert 0.14 < a.iou(b) < 0.15
    assert BBox(x0=1, y0=1, x1=9, y1=9).contained_in(a)


def test_bbox_sem_intersecao():
    a = BBox(x0=0, y0=0, x1=10, y1=10)
    b = BBox(x0=20, y0=20, x1=30, y1=30)
    assert a.intersection_area(b) == 0
    assert a.iou(b) == 0


# ── Réguas e malhas ───────────────────────────────────────────────────────


def test_segmentos_picotados_sao_fundidos():
    """Borda de tabela costuma vir em vários traços; sem fusão a malha não fecha."""
    fundidos = merge_segments([(100, 0, 50), (100, 50, 120), (100, 121, 200)], snap=3.0, min_length=5)
    assert len(fundidos) == 1
    assert fundidos[0].start == 0 and fundidos[0].end == 200


def test_malha_fechada_detectada():
    horizontais = [(100, 50, 350), (130, 50, 350), (160, 50, 350)]
    verticais = [(50, 100, 160), (200, 100, 160), (350, 100, 160)]
    grades = find_grids(horizontais, verticais)
    assert len(grades) == 1
    assert grades[0].n_rows == 2 and grades[0].n_cols == 2
    assert grades[0].closed_ratio == pytest.approx(1.0)


def test_reguas_insuficientes_nao_viram_tabela():
    assert find_grids([(100, 0, 100)], [(0, 0, 100)]) == []


# ── Marcadores de lista ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto,marcador,ordenada",
    [
        ("a) saldo de salário", "a)", True),
        ("1. aviso prévio", "1.", True),
        ("- férias proporcionais", "-", False),
        ("• décimo terceiro", "•", False),
        ("IV) multa do FGTS", "IV)", True),
    ],
)
def test_detecta_marcador(texto, marcador, ordenada):
    resultado = detect_list_marker(texto)
    assert resultado is not None
    assert resultado[0] == marcador
    assert resultado[1] is ordenada


def test_texto_comum_nao_e_lista():
    assert detect_list_marker("O Reclamante foi admitido em 03/02/2020") is None


# ── Marcador preservado no Markdown ───────────────────────────────────────


def _item(marker: str, ordered: bool, texto: str) -> ListItemBlock:
    return ListItemBlock(
        id="l1", page=1, bbox=BBox(x0=0, y0=0, x1=1, y1=1),
        text=texto, ordered=ordered, marker=marker,
    )


def test_numeracao_original_e_preservada():
    """Renumerar seria alterar conteúdo — a alínea é citada em outros pontos."""
    assert _list_item_line(_item("3.", True, "DOS PEDIDOS"), "DOS PEDIDOS") == "3. DOS PEDIDOS"


def test_alinea_nao_vira_numero():
    linha = _list_item_line(_item("c)", True, "férias"), "férias")
    assert "c)" in linha
    assert not linha.startswith("1.")


def test_bullet_simples():
    assert _list_item_line(_item("-", False, "item"), "item") == "- item"


# ── Hifenização ───────────────────────────────────────────────────────────


def test_junta_sem_dehifenizar_por_padrao():
    """Sem evidência de hifenização, o hífen do composto tem de sobreviver."""
    assert join_lines(["sócio-", "administrador da empresa"], False) == "sócio-administrador da empresa"


def test_dehifeniza_quando_o_documento_hifeniza():
    assert join_lines(["trabalha-", "dor rural"], True) == "trabalhador rural"


def test_junta_linhas_normais_com_espaco():
    assert join_lines(["primeira linha", "segunda linha"], False) == "primeira linha segunda linha"


def test_deteccao_de_hifenizacao_do_documento():
    from app.model.layout import TextLine, TextSpan

    def linha(texto: str) -> TextLine:
        caixa = BBox(x0=0, y0=0, x1=10, y1=10)
        return TextLine(spans=[TextSpan(text=texto, bbox=caixa)], bbox=caixa)

    sem = [linha("uma frase completa") for _ in range(50)]
    assert document_uses_hyphenation(sem) is False

    com = sem[:45] + [linha("palavra-") for _ in range(5)]
    assert document_uses_hyphenation(com) is True


# ── Escape de Markdown ────────────────────────────────────────────────────


def test_escapa_inicio_que_viraria_lista():
    assert escape_text("- não é lista") == r"\- não é lista"
    assert escape_text("1. tampouco") == r"\1. tampouco"


def test_nao_escapa_texto_comum():
    assert escape_text("Valor de R$ 2.400,00 em 03/02/2020") == "Valor de R$ 2.400,00 em 03/02/2020"


# ── Tabelas em Markdown ───────────────────────────────────────────────────


def _tabela(linhas, header=1, aligns=None) -> TableBlock:
    return TableBlock(
        id="t1", page=1, bbox=BBox(x0=0, y0=0, x1=1, y1=1),
        rows=[[TableCell(text=c) for c in linha] for linha in linhas],
        header_rows=header,
        alignments=aligns or [Alignment.LEFT] * len(linhas[0]),
    )


def test_tabela_com_cabecalho():
    saida = render_table(_tabela([["Verba", "Valor"], ["Férias", "1.866,67"]]))
    linhas = saida.splitlines()
    assert linhas[0] == "| Verba | Valor |"
    assert linhas[1] == "| :--- | :--- |"
    assert linhas[2] == "| Férias | 1.866,67 |"


def test_tabela_sem_cabecalho_mantem_colunas():
    saida = render_table(_tabela([["a", "b"], ["c", "d"]], header=0))
    assert saida.splitlines()[0] == "|  |  |"
    assert "| a | b |" in saida


def test_alinhamento_a_direita():
    saida = render_table(
        _tabela([["Verba", "Valor"], ["Férias", "1.866,67"]],
                aligns=[Alignment.LEFT, Alignment.RIGHT])
    )
    assert saida.splitlines()[1] == "| :--- | ---: |"


def test_pipe_na_celula_nao_quebra_a_tabela():
    assert escape_cell("A | B") == r"A \| B"


def test_quebra_de_linha_vira_br():
    assert escape_cell("linha1\nlinha2") == "linha1<br>linha2"


# ── Nome do arquivo de saída ──────────────────────────────────────────────


def test_markdown_herda_o_nome_do_pdf():
    from app.render.filenames import markdown_filename

    assert markdown_filename("peticao.pdf") == "peticao.md"


def test_acentos_e_espacos_sao_preservados():
    """O nome existe para uma pessoa reconhecer o documento."""
    from app.render.filenames import markdown_filename

    assert (
        markdown_filename("Petição Inicial - João da Silva.pdf")
        == "Petição Inicial - João da Silva.md"
    )


def test_numero_de_processo_sobrevive():
    from app.render.filenames import markdown_filename

    assert (
        markdown_filename("Processo 0001234-56.2026.5.21.0001.pdf")
        == "Processo 0001234-56.2026.5.21.0001.md"
    )


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("anexos/contrato.pdf", "contrato.md"),
        (r"C:\Users\doc\peticao.pdf", "peticao.md"),
        ("a/b/c/laudo pericial.pdf", "laudo pericial.md"),
    ],
)
def test_caminho_vira_nome_base(entrada, esperado):
    """Arrastar pasta faz alguns navegadores enviarem o caminho relativo."""
    from app.render.filenames import markdown_filename

    assert markdown_filename(entrada) == esperado


def test_caracteres_proibidos_pelo_windows_saem():
    from app.render.filenames import safe_stem

    assert safe_stem('rela"tor*io?') == "relatorio"


def test_nome_reservado_do_windows_vira_padrao():
    from app.render.filenames import markdown_filename

    assert markdown_filename("CON.pdf") == "documento.md"
    assert markdown_filename("nul.pdf") == "documento.md"


def test_nome_vazio_vira_padrao():
    from app.render.filenames import markdown_filename

    assert markdown_filename("   .pdf") == "documento.md"
    assert markdown_filename("") == "documento.md"


def test_nome_muito_longo_e_truncado():
    from app.render.filenames import MAX_STEM, markdown_filename

    nome = markdown_filename("a" * 400 + ".pdf")
    assert len(nome) == MAX_STEM + 3   # + ".md"


def test_ponto_e_espaco_finais_saem():
    """O Windows os remove em silêncio, o que faria o nome divergir."""
    from app.render.filenames import safe_stem

    assert safe_stem("relatorio final. ") == "relatorio final"


def test_modo_desligado_volta_ao_nome_fixo():
    from app.render.filenames import markdown_filename

    assert markdown_filename("peticao.pdf", usar_nome_de_origem=False) == "documento.md"
