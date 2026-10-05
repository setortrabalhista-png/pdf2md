"""pdf2md — conversão local de PDF para Markdown estruturado."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _ler_versao() -> str:
    """A versão vive num lugar só: o pyproject.toml.

    A interface, a API e o nome do pacote de distribuição leem daqui. Duas
    cópias do número acabam divergindo na primeira atualização esquecida.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0"


__version__ = _ler_versao()
