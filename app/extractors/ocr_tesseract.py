"""OCR com Tesseract, produzindo o mesmo formato de layout da rota nativa.

Duas decisões importantes:

1. Usamos a saída TSV (`image_to_data`), não texto plano. Ela traz caixa e
   confiança **por palavra** — sem isso o relatório de qualidade seria um
   palpite, e não haveria como marcar no Markdown o que precisa de conferência.

2. As coordenadas voltam ao espaço de pontos da página do PDF. Isso mantém
   válidos todos os estágios seguintes (tabelas, cabeçalho/rodapé, ordem de
   leitura) sem nenhum caminho especial para página digitalizada.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from app.core.errors import OcrUnavailableError
from app.extractors import ocr_preprocess as pre
from app.model.geometry import BBox
from app.model.layout import RawBlock, TextLine, TextSpan, Word
from app.model.provenance import Provenance, Source

log = logging.getLogger("pdf2md.ocr")

_configured = False

ROTATIONS = {
    90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_CLOCKWISE,
}


def configure(tesseract_cmd: str, tessdata_prefix: Path | None) -> None:
    """Aponta o pytesseract para o binário e para o diretório de idiomas."""
    global _configured
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    if tessdata_prefix and Path(tessdata_prefix).exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata_prefix)
    _configured = True


def is_available(tesseract_cmd: str) -> bool:
    return bool(shutil.which(tesseract_cmd) or Path(tesseract_cmd).exists())


def available_languages(tesseract_cmd: str, tessdata_prefix: Path | None) -> list[str]:
    import pytesseract

    if not _configured:
        configure(tesseract_cmd, tessdata_prefix)
    try:
        return sorted(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001
        return []


@dataclass
class OcrWord:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    block_num: int
    par_num: int
    line_num: int
    word_num: int


def render_page(page: pymupdf.Page, dpi: int) -> tuple[np.ndarray, float]:
    """Rasteriza a página em tons de cinza. Devolve (imagem, zoom aplicado)."""
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return img.copy(), zoom


def detect_orientation(img: np.ndarray) -> int:
    """Rotação detectada pelo OSD (0/90/180/270). Zero quando incerto.

    Anexo digitalizado de cabeça para baixo é rotina em processo físico; sem
    esta correção o OCR devolve ruído com aparência de texto.
    """
    import pytesseract

    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0))
        conf = float(osd.get("orientation_conf", 0.0))
        if conf >= 2.0 and rotate in ROTATIONS:
            return rotate
    except Exception as exc:  # noqa: BLE001 — OSD é best-effort
        log.debug("OSD indisponível: %s", exc)
    return 0


def run_tesseract(
    img: np.ndarray,
    lang: str,
    psm: int,
    oem: int,
    min_conf: float,
) -> list[OcrWord]:
    import pytesseract

    config = f"--oem {oem} --psm {psm} -c preserve_interword_spaces=1"
    data = pytesseract.image_to_data(
        img, lang=lang, config=config, output_type=pytesseract.Output.DICT
    )

    words: list[OcrWord] = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf:
            continue
        words.append(
            OcrWord(
                text=text,
                conf=conf,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                block_num=int(data["block_num"][i]),
                par_num=int(data["par_num"][i]),
                line_num=int(data["line_num"][i]),
                word_num=int(data["word_num"][i]),
            )
        )
    return words


def _pdf_bbox(word: OcrWord, inv: np.ndarray, zoom: float) -> BBox:
    """Caixa da palavra convertida para o espaço de pontos do PDF."""
    corners = [
        (word.left, word.top),
        (word.left + word.width, word.top),
        (word.left, word.top + word.height),
        (word.left + word.width, word.top + word.height),
    ]
    mapped = [pre.map_point(inv, x, y) for x, y in corners]
    xs = [p[0] / zoom for p in mapped]
    ys = [p[1] / zoom for p in mapped]
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def words_to_blocks(
    words: list[OcrWord],
    inv: np.ndarray,
    zoom: float,
    page_number: int,
) -> list[RawBlock]:
    """Reconstrói blocos e linhas a partir da hierarquia devolvida pelo Tesseract."""
    grouped: dict[tuple[int, int], dict[int, list[OcrWord]]] = {}
    for w in words:
        key = (w.block_num, w.par_num)
        grouped.setdefault(key, {}).setdefault(w.line_num, []).append(w)

    blocks: list[RawBlock] = []
    for index, (_key, lines_map) in enumerate(sorted(grouped.items())):
        lines: list[TextLine] = []

        for line_num in sorted(lines_map):
            line_words = sorted(lines_map[line_num], key=lambda w: w.word_num)
            spans: list[TextSpan] = []
            for pos, w in enumerate(line_words):
                box = _pdf_bbox(w, inv, zoom)
                trailing = " " if pos < len(line_words) - 1 else ""
                spans.append(
                    TextSpan(
                        text=w.text + trailing,
                        bbox=box,
                        font="OCR",
                        # A altura da caixa é a melhor estimativa de corpo de
                        # fonte disponível numa página sem metadados de fonte.
                        size=round(box.height * 0.92, 2),
                        flags=0,
                        confidence=max(0.0, min(1.0, w.conf / 100.0)),
                    )
                )
            if not spans:
                continue
            line_box = spans[0].bbox
            for s in spans[1:]:
                line_box = line_box.union(s.bbox)
            lines.append(TextLine(spans=spans, bbox=line_box))

        if not lines:
            continue

        block_box = lines[0].bbox
        for ln in lines[1:]:
            block_box = block_box.union(ln.bbox)

        confidences = [s.confidence for ln in lines for s in ln.spans if s.text.strip()]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

        blocks.append(
            RawBlock(
                page=page_number,
                number=index,
                bbox=block_box,
                lines=lines,
                provenance=Provenance(
                    source=Source.OCR,
                    engine="tesseract",
                    confidence=round(mean_conf, 4),
                ),
            )
        )

    return blocks



def words_to_layout_words(
    words: list[OcrWord], inv: np.ndarray, zoom: float
) -> list[Word]:
    """Converte palavras do OCR para o formato de palavra do layout."""
    return [
        Word(
            text=w.text,
            bbox=_pdf_bbox(w, inv, zoom),
            confidence=max(0.0, min(1.0, w.conf / 100.0)),
            from_ocr=True,
        )
        for w in words
    ]


def ocr_page(
    page: pymupdf.Page,
    page_number: int,
    *,
    lang: str,
    dpi: int,
    psm: int,
    oem: int,
    min_word_conf: float,
    do_deskew: bool,
    do_binarize: bool,
    tesseract_cmd: str,
    tessdata_prefix: Path | None,
    detect_rotation: bool = True,
) -> tuple[list[RawBlock], float, list[Word]]:
    """OCR de uma página. Devolve (blocos, confiança média 0-100, palavras)."""
    if not is_available(tesseract_cmd):
        raise OcrUnavailableError(
            f"Tesseract não encontrado em {tesseract_cmd!r}. "
            "Instale-o ou ajuste PDF2MD_TESSERACT_CMD."
        )
    if not _configured:
        configure(tesseract_cmd, tessdata_prefix)

    img, zoom = render_page(page, dpi)

    rotation = detect_orientation(img) if detect_rotation else 0
    if rotation:
        img = cv2.rotate(img, ROTATIONS[rotation])
        log.info("página %s: orientação corrigida em %s graus", page_number, rotation)

    prepared, matrix = pre.prepare(img, do_deskew=do_deskew, do_binarize=do_binarize)
    inv = pre.inverse_map(matrix)

    words = run_tesseract(prepared, lang, psm, oem, min_word_conf)
    if not words:
        return [], 0.0, []

    blocks = words_to_blocks(words, inv, zoom, page_number)
    mean_conf = sum(w.conf for w in words) / len(words)

    if rotation:
        # A página foi girada antes do OCR: as coordenadas não são comparáveis
        # com a geometria nativa, então sinalizamos para os estágios seguintes.
        for b in blocks:
            b.provenance.notes.append(f"pagina girada em {rotation} graus antes do OCR")

    return blocks, mean_conf, words_to_layout_words(words, inv, zoom)
