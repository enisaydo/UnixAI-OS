"""Arayüze HTTP durum koduyla dönen hatalar."""


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
