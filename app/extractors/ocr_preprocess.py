"""Pré-processamento de imagem para OCR.

Documento digitalizado de escritório chega torto, com sombra de scanner e ruído
de fax. Cada operação aqui existe para um defeito concreto observado nesse tipo
de material — e todas devolvem também a transformação inversa, para que as
coordenadas do OCR possam voltar ao espaço da página do PDF.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("pdf2md.ocr.pre")

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def estimate_skew(gray: np.ndarray, max_angle: float = 12.0) -> float:
    """Ângulo de inclinação em graus, estimado pelas linhas de texto.

    Usa a envoltória mínima dos pixels escuros depois de uma dilatação
    horizontal — isso funde palavras em linhas, e a inclinação da linha é a
    inclinação da página.
    """
    inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    # Junta caracteres em linhas para que o retângulo mínimo siga o texto.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
    dilated = cv2.dilate(inverted, kernel, iterations=1)

    coords = cv2.findNonZero(dilated)
    if coords is None or len(coords) < 100:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]
    # OpenCV devolve o ângulo em (0, 90]; normaliza para perto de zero.
    if angle > 45:
        angle -= 90
    if abs(angle) > max_angle:
        return 0.0
    return float(angle)


def deskew(gray: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    """Rotaciona a imagem e devolve (imagem, matriz de rotação aplicada)."""
    if abs(angle) < 0.15:
        return gray, IDENTITY.copy()

    h, w = gray.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, matrix.astype(np.float32)


def denoise(gray: np.ndarray) -> np.ndarray:
    """Remove sal-e-pimenta sem borrar o traço da letra."""
    return cv2.medianBlur(gray, 3)


def binarize(gray: np.ndarray) -> np.ndarray:
    """Limiar adaptativo — tolera sombra de scanner e iluminação irregular.

    Otsu global falha em página com gradiente de sombra na dobra, que é o
    padrão em digitalização de processo físico.
    """
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=35,
        C=15,
    )


def prepare(
    img: np.ndarray,
    do_deskew: bool = True,
    do_binarize: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Pipeline completo. Devolve (imagem pronta, matriz aplicada à original)."""
    gray = to_gray(img)
    matrix = IDENTITY.copy()

    if do_deskew:
        angle = estimate_skew(gray)
        if abs(angle) >= 0.15:
            gray, matrix = deskew(gray, angle)
            log.debug("deskew aplicado: %.2f°", angle)

    gray = denoise(gray)

    if do_binarize:
        gray = binarize(gray)

    return gray, matrix


def inverse_map(matrix: np.ndarray) -> np.ndarray:
    """Matriz que leva coordenadas da imagem processada de volta à original."""
    if np.allclose(matrix, IDENTITY):
        return IDENTITY.copy()
    return cv2.invertAffineTransform(matrix).astype(np.float32)


def map_point(inv: np.ndarray, x: float, y: float) -> tuple[float, float]:
    if np.allclose(inv, IDENTITY):
        return x, y
    px = inv[0, 0] * x + inv[0, 1] * y + inv[0, 2]
    py = inv[1, 0] * x + inv[1, 1] * y + inv[1, 2]
    return float(px), float(py)
