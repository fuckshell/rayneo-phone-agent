"""Request errors shared by the image service and its background jobs."""
from __future__ import annotations


class ImageChatError(Exception):
    status_code = 400
    code = "invalid_request"
    message = "图片请求无效"

    def __init__(self, code: str | None = None, message: str | None = None,
                 status_code: int | None = None):
        super().__init__(code or self.code)
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message
        if status_code is not None:
            self.status_code = status_code
