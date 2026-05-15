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
        user_status: str | None = None,
        only_followups: bool = False,
        review_status: str | None = None,
        wp_status: str | None = None,
        contact_status: str | None = None,
        only_new_today: bool = False,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update_result_status(self, dedupe_key: str, user_status: str | None) -> dict[str, Any] | None:
        raise NotImplementedError

    def save_ai_normalization(self, dedupe_key: str, ai_json: str, prompt_version: str) -> None:
        """Persiste el JSON normalizado por IA. Implementación opcional."""
        return None

    def set_review_status(
        self, dedupe_key: str, review_status: str, reviewer_id: str | None = None
    ) -> dict[str, Any] | None:
        """Marca una oferta como pending|approved|rejected. Implementación opcional."""
        return None

    def mark_published(
        self, dedupe_key: str, post_id: int, wp_url: str | None = None
    ) -> dict[str, Any] | None:
        """Marca una oferta como publicada en WP. Implementación opcional."""
        return None

    def mark_wp_synced(
        self, dedupe_key: str, post_id: int, wp_url: str | None = None
    ) -> dict[str, Any] | None:
        """Marca técnicamente sincronizada con WP (wp_status=synced). Por defecto
        delega a mark_published para no duplicar lógica en backends simples."""
        return self.mark_published(dedupe_key, post_id, wp_url)

    def mark_wp_failed(self, dedupe_key: str, error: str) -> dict[str, Any] | None:
        """Marca un fallo al intentar publicar a WP. Opcional."""
        return None

    def update_wp_status(
        self,
        dedupe_key: str,
        wp_status: str,
        wp_post_id: int | None = None,
        wp_last_error: str | None = None,
    ) -> dict[str, Any] | None:
        """Cambio genérico de wp_status desde la UI. Implementación opcional."""
        return None

    # --- Reviewers (auth) ---
    def get_reviewer_by_email(self, email: str) -> dict[str, Any] | None:
        return None

    def get_reviewer(self, reviewer_id: str) -> dict[str, Any] | None:
        return None

    def create_reviewer(self, email: str, password_hash: str, name: str, role: str = "reviewer") -> dict[str, Any]:
        raise NotImplementedError(f"{type(self).__name__} no soporta reviewers")

    def touch_reviewer_login(self, reviewer_id: str) -> None:
        return None

    @property
    @abstractmethod
    def backend_name(self) -> str:
        raise NotImplementedError
