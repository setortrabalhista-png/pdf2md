"""Conversão pela linha de comando — o mesmo pipeline da interface web.

    python -m app.cli entrada.pdf -o saida/
    python -m app.cli entrada.pdf --ocr force --paginas 1-10
    python -m app.cli entrada.pdf --ia claude_cli --consentir
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.core.errors import Pdf2MdError
from app.pipeline.options import ConversionOptions, OcrMode
from app.pipeline.runner import run_pipeline


def parse_pages(value: str) -> list[int]:
    """Aceita '1-10', '3', '1-3,7,9-11'."""
    pages: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(chunk))
    return sorted(p for p in pages if p > 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="Converte PDF em Markdown estruturado, com imagens e tabelas.",
    )
    parser.add_argument("pdf", type=Path, help="arquivo PDF de entrada")
    parser.add_argument(
        "-o", "--saida", type=Path, default=None,
        help="diretório de saída (padrão: ./saida/<nome-do-pdf>)",
    )
    parser.add_argument(
        "--ocr", choices=[m.value for m in OcrMode], default=OcrMode.AUTO.value,
        help="auto = só nas páginas sem texto (padrão); force = todas; never = nenhuma",
    )
    parser.add_argument("--idioma", default="por", help="idioma do OCR (padrão: por)")
    parser.add_argument("--paginas", default="", help="recorte de páginas, ex.: 1-10,15")
    parser.add_argument("--sem-imagens", action="store_true")
    parser.add_argument("--sem-tabelas", action="store_true")
    parser.add_argument("--sem-marcador-de-pagina", action="store_true")
    parser.add_argument(
        "--ia", default="", metavar="PROVEDOR",
        help="ativa o refino por IA (null | claude_cli)",
    )
    parser.add_argument(
        "--consentir", action="store_true",
        help="autoriza o envio de trechos do documento ao provedor de IA escolhido",
    )
    parser.add_argument("-v", "--verboso", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verboso else logging.INFO,
        format="%(levelname)-7s %(name)-22s %(message)s",
        stream=sys.stderr,
    )

    if not args.pdf.exists():
        print(f"arquivo não encontrado: {args.pdf}", file=sys.stderr)
        return 2

    out_dir = args.saida or (Path("saida") / args.pdf.stem)

    options = ConversionOptions(
        ocr_mode=OcrMode(args.ocr),
        ocr_lang=args.idioma,
        extract_images=not args.sem_imagens,
        extract_tables=not args.sem_tabelas,
        page_markers=not args.sem_marcador_de_pagina,
        page_range=parse_pages(args.paginas) if args.paginas else [],
        ai_enabled=bool(args.ia and args.ia != "null"),
        ai_consent=args.consentir,
        ai_provider=args.ia or None,
    )

    last_message = ""

    def progress(stage: str, fraction: float, message: str) -> None:
        nonlocal last_message
        if message != last_message:
            print(f"[{fraction * 100:5.1f}%] {message}", file=sys.stderr)
            last_message = message

    started = time.perf_counter()
    try:
        run_pipeline(args.pdf, out_dir, options, progress, get_settings())
    except Pdf2MdError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started

    import json

    report_path = out_dir / "resultado_processamento.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    resumo = report["resumo"]
    markdown = report["documento"].get("arquivo_markdown", "documento.md")

    print()
    print(f"  Markdown  : {out_dir / markdown}")
    print(f"  Relatório : {report_path}")
    print(f"  Imagens   : {report['images_extracted']} em {out_dir / 'assets'}")
    print(f"  Tabelas   : {report['tables_detected']}"
          f" ({report['tabelas']['para_revisar']} para conferir)")
    print(f"  Cobertura : {report['text_accuracy']}% do texto de origem")
    print(f"  Status    : {resumo['status'].upper()}"
          f"  ({resumo['erros']} erro(s), {resumo['avisos']} aviso(s))")
    for pendencia in resumo["pendencias"]:
        print(f"              - {pendencia}")
    print(f"  Tempo     : {elapsed:.1f}s")

    return 0 if resumo["status"] != "revisar" else 3


if __name__ == "__main__":
    raise SystemExit(main())
