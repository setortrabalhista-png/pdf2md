"""Nome do arquivo de saída.

O Markdown recebe o nome do PDF de origem: `Petição Inicial.pdf` vira
`Petição Inicial.md`. Numa conversão em lote isso é o que torna o resultado
utilizável — quarenta arquivos chamados `documento.md` não servem para nada.

Acentos e espaços são preservados de propósito: o nome existe para uma pessoa
reconhecer o documento, não para virar identificador de sistema. Só é removido
o que o sistema de arquivos de fato recusa.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Proibidos pelo Windows em nome de arquivo. O Linux só recusaria "/", mas
# manter a regra mais restritiva evita que um arquivo gerado numa máquina não
# possa ser aberto na outra — ou dentro de um .zip.
_ILEGAIS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Nomes reservados do Windows, com ou sem extensão.
_RESERVADOS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_ESPACOS = re.compile(r"\s+")

# Sistemas de arquivo aceitam 255, mas caminhos longos no Windows ainda
# incomodam; 120 sobra para qualquer nome real e deixa margem para o diretório.
MAX_STEM = 120


def safe_stem(nome: str, padrao: str = "documento") -> str:
    """Transforma um nome de arquivo qualquer num nome de arquivo utilizável."""
    if not nome:
        return padrao

    # Arrastar uma pasta faz alguns navegadores enviarem o caminho relativo.
    # Fica só o nome-base: queremos "doc.md", não "anexos-doc.md". As duas
    # barras são tratadas para que um caminho do Windows também funcione
    # quando o servidor roda em Linux.
    stem = Path(nome.replace("\\", "/")).name
    stem = Path(stem).stem

    # Normaliza a forma Unicode: acento composto e pré-composto viram o mesmo
    # nome, senão dois arquivos "Petição" pareceriam distintos.
    stem = unicodedata.normalize("NFC", stem)

    stem = _ILEGAIS.sub("", stem)
    stem = _ESPACOS.sub(" ", stem).strip()

    # Ponto e espaço no fim são silenciosamente removidos pelo Windows, o que
    # faria o nome gravado divergir do nome esperado.
    stem = stem.rstrip(". ")

    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip(". ")

    if not stem or stem.lower() in _RESERVADOS:
        return padrao

    return stem


def markdown_filename(origem: str, usar_nome_de_origem: bool = True) -> str:
    """Nome do `.md` a gerar para um PDF de entrada."""
    if not usar_nome_de_origem:
        return "documento.md"
    return f"{safe_stem(origem)}.md"
