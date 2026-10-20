"""Stable public error shared by action-based collection domains."""
from __future__ import annotations


class CollectionError(RuntimeError):
    def __init__(self, code: str, status: int = 400, message: str = "") -> None:
        self.code = code
        self.status = status
        self.message = message or code
        super().__init__(code)
