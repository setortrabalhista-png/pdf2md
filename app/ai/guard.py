"""Verificador anti-alucinação.

A promessa "a IA nunca inventa conteúdo" só vale se for verificável. Aqui ela é
verificada em três camadas independentes, calculadas antes e depois de cada
operação. Divergiu em qualquer uma delas, a operação é revertida.

1. **Caracteres** — multiconjunto dos caracteres de conteúdo. Pega qualquer
   inserção ou remoção, por menor que seja.

2. **Números** — multiconjunto das sequências numéricas ("2.400,00",
   "03/02/2020", "0001234-56.2026.5.21.0001"). Existe porque a camada 1 sozinha
   é cega a transposição: trocar `2.400,00` por `4.200,00` mantém exatamente os
   mesmos caracteres. Num documento jurídico, é o erro mais caro possível.

3. **Palavras** — multiconjunto dos vocábulos do documento. Pega
   transposição dentro da palavra ("Silva" → "Sliva"), igualmente invisível
   para a camada 1.

As três são insensíveis à ordem, porque reordenar blocos é uma operação
legítima. A única transformação que altera o vocabulário de forma legítima é a
dehifenização ("trabalha-" + "dor" → "trabalhador"); ela é reconhecida
explicitamente e só é aceita quando cada palavra nova é a concatenação exata de
duas palavras que desapareceram.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter

from app.model.blocks import BlockKind

log = logging.getLogger("pdf2md.ai.guard")

# Caracteres que operações legítimas inserem ou removem: união de linhas
# acrescenta espaço, dehifenização remove hífen.
_MUTABLE = set(" \t\r\n -‐‑‒–—­")

# Sequência numérica com os separadores que aparecem dentro dela.
_NUMBER = re.compile(r"\d[\d.,:/]*\d|\d")
# Vocábulo: qualquer sequência de letras. Contamos até as de uma letra —
# a dehifenização legítima produz fragmentos curtos ("trabalha-" + "dor"),
# e ignorá-los tornaria a fusão inexplicável e a operação seria rejeitada.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _content_fields(block) -> list[str]:
    """Todos os campos do bloco que carregam conteúdo do documento original."""
    kind = getattr(block, "kind", None)
    out: list[str] = []

    if kind == BlockKind.HEADING:
        out.append(block.text)
        if block.numbering:
            out.append(block.numbering)
    elif kind == BlockKind.PARAGRAPH:
        out.append(block.text)
    elif kind == BlockKind.LIST_ITEM or kind == BlockKind.FOOTNOTE:
        out.append(block.text)
        out.append(block.marker)
    elif kind == BlockKind.TABLE:
        out.extend(cell.text for row in block.rows for cell in row)
        if block.caption:
            out.append(block.caption)
    elif kind == BlockKind.FIGURE:
        if block.caption:
            out.append(block.caption)

    return [f for f in out if f]


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


class Fingerprint:
    """As três camadas de verificação, calculadas de uma vez só."""

    __slots__ = ("chars", "numbers", "words")

    def __init__(self, chars: Counter, numbers: Counter, words: Counter):
        self.chars = chars
        self.numbers = numbers
        self.words = words

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Fingerprint):
            return NotImplemented
        return (
            self.chars == other.chars
            and self.numbers == other.numbers
            and self.words == other.words
        )


def fingerprint(doc) -> Fingerprint:
    chars: Counter = Counter()
    numbers: Counter = Counter()
    words: Counter = Counter()

    for block in doc.blocks:
        for field in _content_fields(block):
            normalized = _normalize(field)
            chars.update(c for c in normalized if c not in _MUTABLE)
            # Extraídos por campo, nunca através da fronteira entre campos:
            # concatenar campos criaria números que não existem no documento.
            numbers.update(_NUMBER.findall(normalized))
            words.update(_WORD.findall(normalized))

    return Fingerprint(chars, numbers, words)


def _explainable_by_dehyphenation(before: Counter, after: Counter) -> bool:
    """As palavras novas são fusões exatas de palavras que sumiram?

    É a assinatura da dehifenização legítima e de mais nada. Qualquer palavra
    nova que não se explique assim reprova a operação.
    """
    added = after - before
    removed = before - after

    if not added and not removed:
        return True
    if not added or not removed:
        return False

    disponivel = +removed  # cópia
    for palavra, quantidade in added.items():
        for _ in range(quantidade):
            fusao = None
            for a in list(disponivel):
                if not palavra.startswith(a) or a == palavra:
                    continue
                b = palavra[len(a):]
                if disponivel.get(b, 0) > 0 and not (a == b and disponivel[a] < 2):
                    fusao = (a, b)
                    break
            if fusao is None:
                return False
            disponivel[fusao[0]] -= 1
            disponivel[fusao[1]] -= 1
            disponivel = +disponivel

    # Sobrou palavra que desapareceu sem explicação.
    return not disponivel


def content_preserved(before: Fingerprint, after: Fingerprint) -> bool:
    if before.chars != after.chars:
        return False
    if before.numbers != after.numbers:
        return False
    if before.words != after.words:
        return _explainable_by_dehyphenation(before.words, after.words)
    return True


def diff_summary(before: Fingerprint, after: Fingerprint, limit: int = 6) -> str:
    partes: list[str] = []

    for rotulo, antes, depois in (
        ("caractere(s)", before.chars, after.chars),
        ("número(s)", before.numbers, after.numbers),
        ("palavra(s)", before.words, after.words),
    ):
        added = depois - antes
        removed = antes - depois
        if added:
            amostra = ", ".join(f"{c!r}×{n}" for c, n in added.most_common(limit))
            partes.append(f"acrescentou {rotulo} [{amostra}]")
        if removed:
            amostra = ", ".join(f"{c!r}×{n}" for c, n in removed.most_common(limit))
            partes.append(f"removeu {rotulo} [{amostra}]")

    return "; ".join(partes) or "sem diferença detectável"


class GuardReport:
    """Resultado de uma sessão de refino: o que passou e o que foi barrado."""

    def __init__(self) -> None:
        self.applied: list[str] = []
        self.rejected: list[dict] = []
        self.failed: list[dict] = []

    @property
    def total_proposed(self) -> int:
        return len(self.applied) + len(self.rejected) + len(self.failed)

    def as_dict(self) -> dict:
        return {
            "proposed": self.total_proposed,
            "applied": len(self.applied),
            "rejected_for_content_change": len(self.rejected),
            "failed_to_apply": len(self.failed),
            "applied_detail": self.applied,
            "rejected_detail": self.rejected,
            "failed_detail": self.failed,
        }
