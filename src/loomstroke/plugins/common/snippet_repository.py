"""永続化レイヤ：スニペットと送信履歴を管理する。"""  # noqa: RUF002

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loomstroke.host_api import SettingsService
from loomstroke.platform.user_dirs import default_user_root
from loomstroke.plugins.common.constants import DEFAULT_CATEGORIES

from .snippet_models import (
    RECENT_PROMPTS_FILE,
    SNIPPET_DB_NAME,
    RecentPrompt,
    Snippet,
    hash_prompt,
)

logger = logging.getLogger(__name__)


def _to_timestamp(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    getter = getattr(value, "timestamp", None)
    if callable(getter):
        try:
            return float(getter())
        except Exception:
            return time.time()
    return time.time()


class SnippetRepository:
    """SQLite backed repository used by the TextEditor panel."""

    def __init__(
        self,
        settings: SettingsService | None,
        *,
        db_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._path = self._resolve_path(db_path)
        self._ensure_schema()

    # ---- path / connection -------------------------------------------------
    def _resolve_path(self, db_path: Path | None) -> Path:
        if db_path:
            return db_path.expanduser().resolve()
        base_dir = self._settings_dir() or _default_text_editor_dir()
        return base_dir / SNIPPET_DB_NAME

    def _settings_dir(self) -> Path | None:
        settings = self._settings
        if not settings:
            return None
        getter = getattr(settings, "get", None)
        if callable(getter):
            try:
                value = getter("text_editor.data_directory", None)
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                try:
                    return Path(value).expanduser().resolve()
                except OSError:
                    logger.debug("Invalid data directory setting: %s", value)
        return None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snippets (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    category TEXT DEFAULT 'general',
                    provider_override TEXT,
                    system_override TEXT,
                    favorite INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    kind TEXT DEFAULT 'text',
                    list_items TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """)
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(snippets)").fetchall()
            }
            if "kind" not in columns:
                conn.execute("ALTER TABLE snippets ADD COLUMN kind TEXT DEFAULT 'text'")
            if "list_items" not in columns:
                conn.execute("ALTER TABLE snippets ADD COLUMN list_items TEXT DEFAULT '[]'")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_snippets_title
                ON snippets(title COLLATE NOCASE)
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    icon TEXT,
                    parent_id TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (parent_id) REFERENCES categories(id)
                )
                """)
            count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            if count == 0:
                now = time.time()
                conn.executemany(
                    """
                    INSERT INTO categories (id, name, display_name, icon, parent_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            cat["name"],
                            cat["name"],
                            cat.get("display_name", cat["name"]),
                            cat.get("icon", ""),
                            None,
                            now,
                        )
                        for cat in DEFAULT_CATEGORIES
                    ],
                )

    # ---- CRUD -------------------------------------------------------------
    def add_snippet(self, snippet: Any) -> Snippet:
        return self.save_snippet(snippet)

    def save_snippet(self, snippet: Any) -> Snippet:
        coerced = self._coerce_snippet(snippet)
        metadata = dict(coerced.metadata)
        metadata.setdefault("title", coerced.title)
        metadata["kind"] = getattr(coerced, "kind", "text")
        if getattr(coerced, "list_items", ()):
            metadata["list_items"] = list(coerced.list_items)
        else:
            metadata.pop("list_items", None)
        persisted = Snippet(
            id=coerced.id,
            title=metadata.get("title", coerced.title),
            body=coerced.body,
            tags=list(coerced.tags),
            category=coerced.category or "general",
            provider_override=coerced.provider_override,
            system_override=coerced.system_override,
            favorite=bool(coerced.favorite),
            metadata=metadata,
            kind=getattr(coerced, "kind", "text"),
            list_items=tuple(getattr(coerced, "list_items", ()) or ()),
            created_at=_to_timestamp(coerced.created_at),
            updated_at=time.time(),
        )
        row = persisted.to_storage_row()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snippets (
                    id, title, body, tags, category,
                    provider_override, system_override, favorite, metadata,
                    created_at, updated_at, kind, list_items
                ) VALUES (
                    :id, :title, :body, :tags, :category,
                    :provider_override, :system_override, :favorite, :metadata,
                    :created_at, :updated_at, :kind, :list_items
                )
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    tags = excluded.tags,
                    category = excluded.category,
                    provider_override = excluded.provider_override,
                    system_override = excluded.system_override,
                    favorite = excluded.favorite,
                    metadata = excluded.metadata,
                    kind = excluded.kind,
                    list_items = excluded.list_items,
                    updated_at = excluded.updated_at
                """,
                row,
            )
        return persisted

    def get_snippet(self, snippet_id: str) -> Snippet | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snippets WHERE id = ?",
                (snippet_id,),
            ).fetchone()
        return self._row_to_snippet(row) if row else None

    async def get_by_id(self, snippet_id: str) -> Snippet | None:
        return self.get_snippet(snippet_id)

    def delete_snippet(self, snippet_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))

    def list_snippets(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        tags: Sequence[str] | None = None,
        favorites_only: bool = False,
        kinds: Sequence[str] | None = None,
    ) -> list[Snippet]:
        sql = "SELECT * FROM snippets"
        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("(title LIKE ? OR body LIKE ?)")
            token = f"%{query}%"
            params.extend([token, token])
        if category:
            clauses.append("category = ?")
            params.append(category)
        if tags:
            for tag in tags:
                clauses.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
        if favorites_only:
            clauses.append("favorite = 1")
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend([str(kind) for kind in kinds])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY favorite DESC, updated_at DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_snippet(row) for row in rows]

    def search_titles(self, query: str) -> list[Snippet]:
        if not query:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snippets WHERE title LIKE ? ORDER BY updated_at DESC",
                (f"%{query}%",),
            ).fetchall()
        return [self._row_to_snippet(row) for row in rows]

    async def search_snippets(self, query: str, filters: Mapping[str, Any]) -> list[Snippet]:
        kinds_filter = filters.get("kind") if filters else None
        kinds: Sequence[str] | None
        if isinstance(kinds_filter, str):
            kinds = [kinds_filter]
        elif isinstance(kinds_filter, Iterable):
            kinds = [str(kind) for kind in kinds_filter]
        else:
            kinds = None
        favorites_only = bool(filters.get("favorite")) if filters else False
        category = filters.get("category") if filters else None
        tags_filter = filters.get("tags") if filters else None
        tags: Sequence[str] | None = None
        if isinstance(tags_filter, (list, tuple)):
            tags = [str(tag) for tag in tags_filter]
        results = self.list_snippets(
            query=query or None,
            category=str(category) if category else None,
            tags=tags,
            favorites_only=favorites_only,
            kinds=kinds,
        )
        limit = filters.get("limit") if filters else None
        if isinstance(limit, int) and limit > 0:
            return results[:limit]
        return results

    def toggle_favorite(self, snippet_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT favorite FROM snippets WHERE id = ?",
                (snippet_id,),
            ).fetchone()
            if not row:
                return False
            new_value = 0 if row["favorite"] else 1
            conn.execute(
                "UPDATE snippets SET favorite = ?, updated_at = ? WHERE id = ?",
                (new_value, time.time(), snippet_id),
            )
        return bool(new_value)

        async def list_categories(self) -> list[str]:
            with self._lock, self._connect() as conn:
                rows = conn.execute(  # noqa: F841
                    "SELECT DISTINCT category "
                    "FROM snippets "
                    "WHERE category IS NOT NULL "
                    "ORDER BY category",
                ).fetchall()

        return [row["category"] for row in rows]  # noqa: F821

    async def list_tags(self) -> list[str]:
        tags: set[str] = set()
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT tags FROM snippets").fetchall()
        for row in rows:
            raw_value = row["tags"] or "[]"
            try:
                parsed = json.loads(raw_value)
                if isinstance(parsed, Iterable):
                    tags.update(str(tag) for tag in parsed if tag)
            except json.JSONDecodeError:
                tokens = (token.strip() for token in raw_value.split(","))
                tags.update(token for token in tokens if token)
        return sorted(tags)

        def get_categories(self) -> list[dict[str, Any]]:
            with self._lock, self._connect() as conn:
                rows = conn.execute(  # noqa: F841
                    "SELECT id, name, display_name, icon, parent_id "
                    "FROM categories "
                    "ORDER BY display_name",
                ).fetchall()

        return [dict(row) for row in rows]

    def add_category(
        self,
        *,
        name: str,
        display_name: str,
        icon: str = "📁",
        parent_id: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO categories (id, name, display_name, icon, parent_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, name, display_name, icon, parent_id, time.time()),
            )

    def delete_category(self, category_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE snippets SET category = 'general' WHERE category = ?",
                (category_id,),
            )
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def cleanup(self) -> None:
        # sqlite3 closes connections automatically; nothing to do.
        pass

    def _coerce_snippet(self, snippet: Any) -> Snippet:
        if isinstance(snippet, Snippet):
            return snippet
        title = "Untitled"
        metadata = dict(getattr(snippet, "metadata", {}) or {})
        if "version" not in metadata and hasattr(snippet, "version"):
            metadata["version"] = snippet.version
        if "title" in metadata:
            title = str(metadata["title"])
        elif hasattr(snippet, "title") and snippet.title:
            title = str(snippet.title)
        content = getattr(snippet, "body", None)
        if content is None:
            content = getattr(snippet, "content", "")
        tags = list(getattr(snippet, "tags", []))
        raw_kind = getattr(snippet, "kind", None) or metadata.get("kind") or "text"
        kind = str(raw_kind).lower() if isinstance(raw_kind, str) else "text"
        list_items_attr = getattr(snippet, "list_items", None)
        if list_items_attr is None and kind == "list":
            body_source = str(content or "")
            list_items_attr = [line.strip() for line in body_source.splitlines() if line.strip()]
        elif isinstance(list_items_attr, str):
            list_items_attr = [list_items_attr]
        list_items = tuple(
            str(item).strip() for item in (list_items_attr or []) if str(item).strip()
        )
        metadata.setdefault("kind", kind)
        if list_items:
            metadata.setdefault("list_items", list(list_items))
        return Snippet(
            id=str(snippet.id),
            title=title,
            body=str(content),
            tags=[str(tag) for tag in tags],
            category=str(getattr(snippet, "category", "general") or "general"),
            provider_override=getattr(snippet, "provider_override", None),
            system_override=getattr(snippet, "system_override", None),
            favorite=bool(getattr(snippet, "favorite", False)),
            metadata=metadata,
            kind=kind,
            list_items=list_items,
            created_at=_to_timestamp(getattr(snippet, "created_at", time.time())),
            updated_at=_to_timestamp(getattr(snippet, "updated_at", time.time())),
        )

    def _row_to_snippet(self, row: sqlite3.Row) -> Snippet:
        raw_tags = row["tags"] or "[]"
        try:
            parsed = json.loads(raw_tags)
            if isinstance(parsed, Iterable):
                tags = [str(tag) for tag in parsed if isinstance(tag, str)]
            else:
                tags = []
        except json.JSONDecodeError:
            tags = [token.strip() for token in raw_tags.split(",") if token.strip()]
        raw_metadata = row["metadata"] or "{}"
        try:
            metadata = json.loads(raw_metadata)
            if not isinstance(metadata, dict):
                metadata = {}
        except json.JSONDecodeError:
            metadata = {}
        metadata.setdefault("title", row["title"])
        row_keys = {name for name in row}
        raw_kind = row["kind"] if "kind" in row_keys else metadata.get("kind", "text")
        kind = str(raw_kind) if raw_kind else "text"
        raw_list = (
            row["list_items"] if "list_items" in row_keys else metadata.get("list_items", "[]")
        )
        list_items: tuple[str, ...] = tuple()
        if isinstance(raw_list, str):
            try:
                parsed_list = json.loads(raw_list)
                if isinstance(parsed_list, Iterable):
                    list_items = tuple(
                        str(item).strip() for item in parsed_list if str(item).strip()
                    )
            except json.JSONDecodeError:
                list_items = tuple(line.strip() for line in raw_list.splitlines() if line.strip())
        elif isinstance(raw_list, Iterable):
            list_items = tuple(str(item).strip() for item in raw_list if str(item).strip())
        metadata["kind"] = kind
        if list_items:
            metadata["list_items"] = list(list_items)
        else:
            metadata.pop("list_items", None)
        return Snippet(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            tags=tags,
            category=row["category"] or "general",
            provider_override=row["provider_override"],
            system_override=row["system_override"],
            favorite=bool(row["favorite"]),
            metadata=metadata,
            kind=str(kind),
            list_items=list_items,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


class RecentPromptRepository:
    """Persist prompt submissions as JSON Lines."""

    def __init__(
        self,
        settings: SettingsService | None,
        *,
        path: Path | None = None,
        max_entries: int = 50,
    ) -> None:
        self._settings = settings
        self._path = self._resolve_path(path)
        self._lock = threading.RLock()
        self._max_entries = max(1, max_entries)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    def _resolve_path(self, path: Path | None) -> Path:
        if path:
            return path.expanduser().resolve()
        base_dir = self._settings_dir() or _default_text_editor_dir()
        return base_dir / RECENT_PROMPTS_FILE

    def _settings_dir(self) -> Path | None:
        settings = self._settings
        if not settings:
            return None
        getter = getattr(settings, "get", None)
        if callable(getter):
            try:
                value = getter("text_editor.recent_directory", None)
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                try:
                    return Path(value).expanduser().resolve()
                except OSError:
                    logger.debug("Invalid recent directory setting: %s", value)
        return None

    def record_submission(
        self,
        *,
        provider: str,
        model: str,
        prompt_text: str,
        summary: str,
    ) -> RecentPrompt:
        timestamp = time.time()
        entry = RecentPrompt(
            id=f"rp_{int(timestamp * 1000)}",
            provider=provider,
            model=model,
            prompt_hash=hash_prompt(prompt_text),
            timestamp=timestamp,
            summary=summary,
        )
        with self._lock:
            entries = self.load()
            entries.append(entry)
            entries = entries[-self._max_entries :]
            self._persist(entries)
        return entry

    def load(self) -> list[RecentPrompt]:
        entries: list[RecentPrompt] = []
        if not self._path.exists():
            return entries
        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                logger.warning("Failed to read recent prompts: %s", exc)
                return []
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                entries.append(RecentPrompt(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        entries.sort(key=lambda item: item.timestamp)
        return entries

    def clear(self) -> None:
        with self._lock:
            try:
                self._path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to clear recent prompts", exc_info=True)
            self._path.touch(exist_ok=True)

    def _persist(self, entries: Sequence[RecentPrompt]) -> None:
        payload = "\n".join(json.dumps(asdict(entry), ensure_ascii=False) for entry in entries)
        try:
            self._path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to persist recent prompts: %s", exc)


__all__ = ["RecentPromptRepository", "SnippetRepository"]


def _default_text_editor_dir() -> Path:
    """Return default data directory for Text Editor under repo-local user_data."""
    repo_root = Path(__file__).resolve().parents[4]
    candidate = repo_root / "user_data" / "text_editor"
    with suppress(OSError):
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    fallback = default_user_root(create=True) / "text_editor"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
