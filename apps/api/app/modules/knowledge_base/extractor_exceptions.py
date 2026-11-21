"""Custom exceptions for document extraction and processing."""


class ExtractionError(Exception):
    """Base class for all extraction-related exceptions."""

    def __init__(self, message: str, code: str = "EXTRACTION_FAILED") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PermanentExtractionError(ExtractionError):
    """Errors that should NOT be retried (unrecoverable document faults)."""


class TransientExtractionError(ExtractionError):
    """Errors that can be retried (temporary failures, timeouts, contention)."""


# Permanent error specializations
class CorruptedFileError(PermanentExtractionError):
    """Raised when the document file is malformed or unreadable by the parser."""

    def __init__(self, message: str = "Document is corrupted or unreadable.") -> None:
        super().__init__(message, code="CORRUPTED_FILE")


# Backward compatibility alias
CorruptFileError = CorruptedFileError


class EncryptedPdfError(PermanentExtractionError):
    """Raised when a PDF is password protected or encrypted."""

    def __init__(self, message: str = "PDF is password protected or encrypted.") -> None:
        super().__init__(message, code="ENCRYPTED_PDF")


class PageLimitExceededError(PermanentExtractionError):
    """Raised when a PDF exceeds the maximum physical page limit."""

    def __init__(self, message: str = "Document exceeds maximum allowed pages.") -> None:
        super().__init__(message, code="PAGE_LIMIT_EXCEEDED")


class PayloadTooLargeError(PermanentExtractionError):
    """Raised when cumulative extracted text exceeds the memory/payload limit."""

    def __init__(self, message: str = "Extracted text exceeds maximum payload limit.") -> None:
        super().__init__(message, code="PAYLOAD_TOO_LARGE")


class MalformedTextError(PermanentExtractionError):
    """Raised when a text file contains null bytes or invalid non-text control sequences."""

    def __init__(self, message: str = "File contains malformed or binary text.") -> None:
        super().__init__(message, code="MALFORMED_TEXT")


class InvalidMimeTypeError(PermanentExtractionError):
    """Raised when the file MIME type is unsupported."""

    def __init__(self, message: str = "Unsupported MIME type.") -> None:
        super().__init__(message, code="INVALID_MIME_TYPE")


class ChecksumMismatchError(PermanentExtractionError):
    """Raised when the downloaded file's SHA-256 does not match recorded checksum."""

    def __init__(self, message: str = "File SHA-256 checksum mismatch.") -> None:
        super().__init__(message, code="CHECKSUM_MISMATCH")


class FileSizeMismatchError(PermanentExtractionError):
    """Raised when the downloaded file's byte count does not match recorded size."""

    def __init__(self, message: str = "File size does not match metadata.") -> None:
        super().__init__(message, code="FILE_SIZE_MISMATCH")


# Transient error specializations
class ExtractionTimeoutError(TransientExtractionError):
    """Raised when parsing exceeds the allocated wall-clock deadline."""

    def __init__(self, message: str = "Document parsing exceeded execution deadline.") -> None:
        super().__init__(message, code="PARSER_TIMEOUT")


class StorageStreamError(TransientExtractionError):
    """Raised when streaming from storage encounters a network or transient error."""

    def __init__(self, message: str = "Failed to stream document from storage.") -> None:
        super().__init__(message, code="STORAGE_STREAM_FAILED")
