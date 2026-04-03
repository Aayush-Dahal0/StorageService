class StorageServiceError(Exception):
    """Base exception for storage service errors."""
    pass


class ClientNotFoundError(StorageServiceError):
    def __init__(self, client_id: str):
        self.client_id = client_id
        super().__init__(f"Client '{client_id}' not found.")


class ClientAlreadyExistsError(StorageServiceError):
    def __init__(self, client_id: str):
        self.client_id = client_id
        super().__init__(f"Client '{client_id}' already exists.")


class FileNotFoundError(StorageServiceError):
    def __init__(self, file_id: str):
        self.file_id = file_id
        super().__init__(f"File '{file_id}' not found.")


class FileNotOwnedByClientError(StorageServiceError):
    def __init__(self, file_id: str, client_id: str):
        super().__init__(
            f"File '{file_id}' does not belong to client '{client_id}'."
        )


class StorageQuotaExceededError(StorageServiceError):
    def __init__(self, client_id: str, required: int, available: int):
        self.client_id = client_id
        self.required = required
        self.available = available
        super().__init__(
            f"Client '{client_id}' has insufficient storage. "
            f"Required: {required} bytes, Available: {available} bytes."
        )


class InvalidStorageLimitError(StorageServiceError):
    def __init__(self, reason: str):
        super().__init__(f"Invalid storage limit: {reason}")
