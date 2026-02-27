from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRepository(ABC):
    @abstractmethod
    def upsert_results(self, records: list[dict[str, Any]], keyword: str, search_mode: str) -> dict[str, int]:
        raise NotImplementedError

    @abstractmethod
    def list_results(
        self,
        limit: int = 200,
        source_type: str | None = None,
        search_text: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def backend_name(self) -> str:
        raise NotImplementedError
