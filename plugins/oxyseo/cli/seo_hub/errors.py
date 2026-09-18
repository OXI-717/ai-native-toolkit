from __future__ import annotations


class SeoHubError(Exception):
    code = "SEO_HUB_ERROR"
    exit_code = 2

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
