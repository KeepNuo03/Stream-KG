"""Application exceptions and error codes."""


class StreamKgError(Exception):
    """Base application error with machine-readable code."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class DocumentNotFoundError(StreamKgError):
    def __init__(self, doc_id: str) -> None:
        super().__init__("DOCUMENT_NOT_FOUND", f"Document {doc_id} not found", {"doc_id": doc_id})


class EntityNotFoundError(StreamKgError):
    def __init__(self, entity_id: str) -> None:
        super().__init__(
            "ENTITY_NOT_FOUND", f"Entity {entity_id} not found", {"entity_id": entity_id}
        )


class SessionNotFoundError(StreamKgError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            "SESSION_NOT_FOUND", f"Session {session_id} not found", {"session_id": session_id}
        )


class DocumentProcessingError(StreamKgError):
    def __init__(self, doc_id: str) -> None:
        super().__init__(
            "DOCUMENT_PROCESSING",
            f"Document {doc_id} is currently processing",
            {"doc_id": doc_id},
        )
