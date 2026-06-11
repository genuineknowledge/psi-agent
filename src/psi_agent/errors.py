from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserFacingError(Exception):
    message: str
    hint: str = ""

    def __str__(self) -> str:
        if not self.hint:
            return self.message
        return f"{self.message}\nNext step: {self.hint}"
