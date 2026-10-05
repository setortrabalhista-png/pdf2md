"""Gera PDFs sintéticos que reproduzem os defeitos reais de documento jurídico.

Não substituem PDFs reais do escritório — servem para que a suíte de regressão
rode em qualquer máquina, e para exercitar cada caminho do pipeline:

    juridico.pdf     peça com títulos, listas, citação, rodapé recorrente,
                     nota de rodapé e tabela com bordas
    tabelas.pdf      tabela com bordas, tabela sem bordas e tabela com mescla
    escaneado.pdf    página sem camada de texto (imagem pura) ⇒ força OCR
    hibrido.pdf      página nativa + página digitalizada no mesmo arquivo

Execução:  python -m tests.make_fixtures
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURES = Path(__file__).parent / "fixtures"

A4 = pymupdf.paper_rect("a4")
MARGIN = 56.0
BODY = "helv"
BOLD = "hebo"


def _footer(page: pymupdf.Page, number: int, total: int) -> None:
    """Rodapé recorrente no estilo dos sistemas de tribunal."""
    page.insert_text(
        (MARGIN, A4.height - 38),
        "Documento assinado eletronicamente por VICTOR CERQUEIRA LIMA, "
        "Advogado, em 25/08/2026",
        fontsize=6.5,
        fontname=BODY,
        color=(0.4, 0.4, 0.4),
    )
    page.insert_text(
        (MARGIN, A4.height - 28),
        f"Processo 0001234-56.2026.5.21.0001 - fls. {number} de {total}",
        fontsize=6.5,
        fontname=BODY,
        color=(0.4, 0.4, 0.4),
    )


def _header(page: pymupdf.Page) -> None:
    page.insert_text(
        (MARGIN, 34),
        "PODER JUDICIARIO - JUSTICA DO TRABALHO",
        fontsize=7,
        fontname=BODY,
        color=(0.35, 0.35, 0.35),
    )


def _write_block(
    page: pymupdf.Page, y: float, text: str, *, size: float = 10.5,
    font: str = BODY, indent: float = 0.0, width: float | None = None,
    align: int = pymupdf.TEXT_ALIGN_JUSTIFY,
) -> float:
    """Insere um parágrafo e devolve a nova posição vertical."""
    x0 = MARGIN + indent
    x1 = A4.width - MARGIN if width is None else x0 + width
    rect = pymupdf.Rect(x0, y, x1, y + 400)
    leftover = page.insert_textbox(
        rect, text, fontsize=size, fontname=font, align=align, lineheight=1.35
    )
    # insert_textbox devolve o espaço vertical que sobrou dentro do retângulo.
    used = 400 - max(0.0, leftover)
    return y + used + size * 0.6


def _draw_table(
    page: pymupdf.Page, x: float, y: float, rows: list[list[str]],
    col_widths: list[float], row_height: float = 20.0,
    header: bool = True, merge_first_row: bool = False,
) -> float:
    """Desenha uma tabela com bordas de verdade (linhas vetoriais)."""
    total_width = sum(col_widths)
    n_rows = len(rows)

    # Réguas horizontais
    for i in range(n_rows + 1):
        yy = y + i * row_height
        page.draw_line(
            pymupdf.Point(x, yy), pymupdf.Point(x + total_width, yy), width=0.7
        )

    # Réguas verticais
    xx = x
    edges = [xx]
    for w in col_widths:
        xx += w
        edges.append(xx)

    for index, edge in enumerate(edges):
        start_row = 0
        # Mescla na primeira linha: a régua interna não é desenhada nela.
        if merge_first_row and 0 < index < len(edges) - 1:
            start_row = 1
        page.draw_line(
            pymupdf.Point(edge, y + start_row * row_height),
            pymupdf.Point(edge, y + n_rows * row_height),
            width=0.7,
        )

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if merge_first_row and i == 0 and j > 0:
                continue
            font = BOLD if (header and i == 0) else BODY
            page.insert_text(
                (edges[j] + 4, y + i * row_height + row_height * 0.68),
                cell,
                fontsize=9,
                fontname=font,
            )

    return y + n_rows * row_height + 14


def make_juridico(path: Path) -> None:
    doc = pymupdf.open()
    total = 2

    page = doc.new_page(width=A4.width, height=A4.height)
    _header(page)
    y = 78

    page.insert_textbox(
        pymupdf.Rect(MARGIN, y, A4.width - MARGIN, y + 40),
        "EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DA VARA DO TRABALHO",
        fontsize=11, fontname=BOLD, align=pymupdf.TEXT_ALIGN_CENTER,
    )
    y += 52

    y = _write_block(
        page, y,
        "JOÃO DA SILVA, brasileiro, solteiro, auxiliar de produção, portador da "
        "CTPS nº 12345, residente e domiciliado nesta capital, vem, por seu "
        "advogado que esta subscreve, propor a presente RECLAMAÇÃO TRABALHISTA "
        "em face de INDÚSTRIA MODELO LTDA., pelos fatos e fundamentos a seguir "
        "expostos.",
    )
    y += 8

    page.insert_text((MARGIN, y), "1. DOS FATOS", fontsize=12, fontname=BOLD)
    y += 22

    y = _write_block(
        page, y,
        "O Reclamante foi admitido em 03/02/2020 para exercer a função de "
        "auxiliar de produção, mediante remuneração mensal de R$ 2.400,00, "
        "tendo sido dispensado sem justa causa em 14/06/2026, sem o pagamento "
        "das verbas rescisórias devidas.",
    )

    y = _write_block(
        page, y,
        "Durante todo o período contratual, o Reclamante cumpriu jornada das "
        "07h00 às 19h00, de segunda a sexta-feira, com apenas trinta minutos de "
        "intervalo intrajornada, sem qualquer contraprestação a título de horas "
        "extraordinárias.",
    )
    y += 6

    page.insert_text((MARGIN, y), "1.1 Das verbas não quitadas", fontsize=11, fontname=BOLD)
    y += 20

    for item in (
        "a) saldo de salário referente a quatorze dias trabalhados;",
        "b) aviso prévio indenizado, na forma da Lei nº 12.506/2011;",
        "c) férias proporcionais acrescidas do terço constitucional;",
        "d) décimo terceiro salário proporcional;",
        "e) multa de quarenta por cento sobre o saldo do FGTS.",
    ):
        y = _write_block(page, y, item, size=10.5, align=pymupdf.TEXT_ALIGN_LEFT)

    y += 6
    y = _write_block(
        page, y,
        "\"O intervalo intrajornada não usufruído deve ser remunerado como "
        "hora extraordinária, com o respectivo adicional, na forma da Súmula 437 "
        "do Colendo Tribunal Superior do Trabalho.\"",
        size=9.5, indent=48, width=A4.width - 2 * MARGIN - 96,
    )

    # Nota de rodapé com filete separador
    separator_y = A4.height - 92
    page.draw_line(
        pymupdf.Point(MARGIN, separator_y),
        pymupdf.Point(MARGIN + 150, separator_y),
        width=0.5,
    )
    page.insert_text(
        (MARGIN, separator_y + 12),
        "1  Súmula 437 do TST, item I, com redação dada pela Resolução 185/2012.",
        fontsize=7.5, fontname=BODY,
    )
    _footer(page, 1, total)

    # ── Página 2 ──────────────────────────────────────────────────────
    page = doc.new_page(width=A4.width, height=A4.height)
    _header(page)
    y = 78

    page.insert_text((MARGIN, y), "2. DO DEMONSTRATIVO DE VERBAS", fontsize=12, fontname=BOLD)
    y += 24

    y = _draw_table(
        page, MARGIN, y,
        rows=[
            ["Verba", "Base de cálculo", "Valor"],
            ["Saldo de salário", "R$ 2.400,00", "1.120,00"],
            ["Aviso prévio indenizado", "R$ 2.400,00", "2.960,00"],
            ["Férias + 1/3", "R$ 2.400,00", "1.866,67"],
            ["13º proporcional", "R$ 2.400,00", "1.200,00"],
            ["Multa de 40% do FGTS", "R$ 9.216,00", "3.686,40"],
        ],
        col_widths=[190, 150, 110],
    )

    y += 10
    y = _write_block(
        page, y,
        "3. DOS PEDIDOS. Ante o exposto, requer a Vossa Excelência a procedência "
        "dos pedidos formulados, com a condenação da Reclamada ao pagamento das "
        "verbas discriminadas no quadro acima, acrescidas de juros e correção "
        "monetária, além dos honorários advocatícios sucumbenciais.",
    )

    y += 20
    page.insert_textbox(
        pymupdf.Rect(MARGIN, y, A4.width - MARGIN, y + 46),
        "Natal/RN, 25 de agosto de 2026.\n\nVICTOR CERQUEIRA LIMA\nOAB/RN 12.345",
        fontsize=10, fontname=BODY, align=pymupdf.TEXT_ALIGN_CENTER,
    )

    _footer(page, 2, total)
    doc.save(str(path))
    doc.close()


def make_tabelas(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    y = 70

    page.insert_text((MARGIN, y), "Tabela com bordas", fontsize=12, fontname=BOLD)
    y += 22
    y = _draw_table(
        page, MARGIN, y,
        rows=[
            ["Competência", "Salário", "Descontos", "Líquido"],
            ["01/2026", "2.400,00", "264,00", "2.136,00"],
            ["02/2026", "2.400,00", "264,00", "2.136,00"],
            ["03/2026", "2.640,00", "290,40", "2.349,60"],
        ],
        col_widths=[120, 110, 110, 110],
    )

    y += 18
    page.insert_text((MARGIN, y), "Tabela com célula mesclada", fontsize=12, fontname=BOLD)
    y += 22
    y = _draw_table(
        page, MARGIN, y,
        rows=[
            ["RESUMO DO PERÍODO CONTRATUAL", "", ""],
            ["Admissão", "Dispensa", "Duração"],
            ["03/02/2020", "14/06/2026", "6 anos e 4 meses"],
        ],
        col_widths=[150, 150, 150],
        merge_first_row=True,
    )

    y += 18
    page.insert_text((MARGIN, y), "Tabela sem bordas", fontsize=12, fontname=BOLD)
    y += 24

    # Colunas por alinhamento puro, sem nenhuma linha desenhada.
    columns = [MARGIN, MARGIN + 190, MARGIN + 330]
    borderless = [
        ("Descrição", "Referência", "Valor"),
        ("Horas extras 50%", "120,00 horas", "3.272,72"),
        ("Adicional noturno", "40,00 horas", "610,90"),
        ("Reflexos em DSR", "—", "788,45"),
        ("Intervalo suprimido", "60,00 horas", "1.636,36"),
    ]
    for index, row in enumerate(borderless):
        font = BOLD if index == 0 else BODY
        for x, cell in zip(columns, row, strict=True):
            page.insert_text((x, y), cell, fontsize=9.5, fontname=font)
        y += 17

    doc.save(str(path))
    doc.close()


def _text_page_as_image(text_lines: list[str], dpi: int = 200) -> bytes:
    """Renderiza uma página de texto e devolve os pixels — sem camada de texto."""
    temp = pymupdf.open()
    page = temp.new_page(width=A4.width, height=A4.height)
    y = 90
    for line in text_lines:
        size = 13 if line.isupper() and len(line) < 60 else 10.5
        font = BOLD if line.isupper() and len(line) < 60 else BODY
        page.insert_text((MARGIN, y), line, fontsize=size, fontname=font)
        y += size * 1.9
    pix = page.get_pixmap(dpi=dpi)
    data = pix.tobytes("png")
    temp.close()
    return data


SCAN_LINES = [
    "TERMO DE RESCISAO DO CONTRATO DE TRABALHO",
    "",
    "Empregador: INDUSTRIA MODELO LTDA.",
    "CNPJ: 12.345.678/0001-90",
    "Empregado: JOAO DA SILVA",
    "CTPS: 12345 Serie: 001",
    "Admissao: 03/02/2020",
    "Afastamento: 14/06/2026",
    "Motivo: dispensa sem justa causa",
    "",
    "Declaro haver recebido a importancia liquida discriminada no verso",
    "deste termo, dando plena e geral quitacao pelo periodo trabalhado.",
]


def make_escaneado(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_image(pymupdf.Rect(0, 0, A4.width, A4.height), stream=_text_page_as_image(SCAN_LINES))
    doc.save(str(path))
    doc.close()


def make_hibrido(path: Path) -> None:
    """Peça nativa seguida de anexo digitalizado — o caso mais comum no PJe."""
    doc = pymupdf.open()

    page = doc.new_page(width=A4.width, height=A4.height)
    y = 80
    page.insert_text((MARGIN, y), "PETIÇÃO DE JUNTADA DE DOCUMENTO", fontsize=12, fontname=BOLD)
    y += 26
    _write_block(
        page, y,
        "O Reclamante vem, respeitosamente, à presença de Vossa Excelência, "
        "requerer a juntada do termo de rescisão do contrato de trabalho, "
        "documento indispensável à comprovação das alegações contidas na inicial, "
        "que segue anexo à presente petição.",
    )
    _footer(page, 1, 2)

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_image(pymupdf.Rect(0, 0, A4.width, A4.height), stream=_text_page_as_image(SCAN_LINES))

    doc.save(str(path))
    doc.close()


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    builders = {
        "juridico.pdf": make_juridico,
        "tabelas.pdf": make_tabelas,
        "escaneado.pdf": make_escaneado,
        "hibrido.pdf": make_hibrido,
    }
    for name, builder in builders.items():
        target = FIXTURES / name
        builder(target)
        print(f"  {target}  ({target.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
