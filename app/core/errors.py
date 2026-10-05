"""Exceções do domínio. Tudo que chega à API vira uma dessas."""


class Pdf2MdError(Exception):
    """Base de todos os erros previsíveis do sistema."""

    code = "INTERNAL_ERROR"
    http_status = 500


class InvalidPdfError(Pdf2MdError):
    code = "INVALID_PDF"
    http_status = 400


class EncryptedPdfError(Pdf2MdError):
    code = "ENCRYPTED_PDF"
    http_status = 400


class FileTooLargeError(Pdf2MdError):
    code = "FILE_TOO_LARGE"
    http_status = 413


class JobNotFoundError(Pdf2MdError):
    code = "JOB_NOT_FOUND"
    http_status = 404


class OcrUnavailableError(Pdf2MdError):
    code = "OCR_UNAVAILABLE"
    http_status = 503


class AiRefusedError(Pdf2MdError):
    """A camada de IA devolveu operações que alterariam o conteúdo."""

    code = "AI_REFUSED"
    http_status = 422


class StageError(Pdf2MdError):
    code = "STAGE_FAILED"

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
