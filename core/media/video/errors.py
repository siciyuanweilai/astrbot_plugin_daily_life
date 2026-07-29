from __future__ import annotations


class VideoAPIError(RuntimeError):
    def __init__(self, message: str, status: int, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class VideoRequestTimeout(RuntimeError):
    pass


class VideoTaskError(RuntimeError):
    pass
