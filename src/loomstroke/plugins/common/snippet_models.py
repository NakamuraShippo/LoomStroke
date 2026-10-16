"""データモデルおよびユーティリティ群。

TextEditor パネルで利用するスニペットや LLM 投稿用ペイロード、添付ファイル
の扱いを一か所にまとめ、テストしやすい構造に整える。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from loomstroke.plugins.sdk.base import JobSpec

logger = logging.getLogger(__name__)

TEXT_EDITOR_NAMESPACE = "text_editor"
SNIPPET_DB_NAME = "text_snippets.sqlite3"
RECENT_PROMPTS_FILE = "recent_prompts.jsonl"
MAX_PROMPT_CHARS = 5000
MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_MB = 8
PLACEHOLDER_PATTERN = r"\{[a-zA-Z0-9_]+\}"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 512
_MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024


SnippetKind = Literal["text", "list", "prompt", "system_prompt", "user_prompt"]


@dataclass(slots=True)
class Snippet:
    """SQLite に保存されるスニペットデータ。"""

    id: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    category: str = "general"
    provider_override: str | None = None
    system_override: str | None = None
    favorite: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: SnippetKind = "text"
    list_items: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    def __post_init__(self) -> None:
        normalized: tuple[str, ...]
        if isinstance(self.list_items, (list, tuple)):
            normalized = tuple(str(item).strip() for item in self.list_items if str(item).strip())
        elif isinstance(self.list_items, str):
            item = self.list_items.strip()
            normalized = (item,) if item else tuple()
        else:
            normalized = tuple()
        if self.kind == "list" and not normalized:
            normalized = tuple(line.strip() for line in self.body.splitlines() if line.strip())
        object.__setattr__(self, "list_items", normalized)

    @property
    def content(self) -> str:
        """後方互換のため body を content として公開する。"""

        if self.kind == "list" and self.list_items:
            return "\n".join(self.list_items)
        return self.body

    def with_timestamps(
        self,
        *,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> Snippet:
        """タイムスタンプを更新したコピーを返す。"""

        return Snippet(
            id=self.id,
            title=self.title,
            body=self.body,
            tags=list(self.tags),
            category=self.category,
            provider_override=self.provider_override,
            system_override=self.system_override,
            favorite=self.favorite,
            metadata=dict(self.metadata),
            kind=self.kind,
            list_items=tuple(self.list_items),
            created_at=self.created_at if created_at is None else created_at,
            updated_at=time.time() if updated_at is None else updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize snippet in the public schema used by common models."""

        created_iso = datetime.fromtimestamp(self.created_at).isoformat()
        updated_iso = datetime.fromtimestamp(self.updated_at).isoformat()
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "tags": list(self.tags),
            "created_at": created_iso,
            "updated_at": updated_iso,
            "version": self.metadata.get("version", 1),
            "metadata": dict(self.metadata),
            "favorite": self.favorite,
            "kind": self.kind,
            "list_items": list(self.list_items),
        }

    def to_storage_row(self) -> dict[str, Any]:
        """SQLite へ保存する際の辞書化。"""

        return {
            "id": self.id,
            "title": self.title,
            "body": self.content,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "category": self.category,
            "provider_override": self.provider_override,
            "system_override": self.system_override,
            "favorite": int(self.favorite),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
            "kind": self.kind,
            "list_items": json.dumps(list(self.list_items), ensure_ascii=False),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }


@dataclass(slots=True)
class Attachment:
    """LLM へ送る画像等の添付メタデータ。"""

    id: str
    source: str
    mime: str
    path: Path | None = None
    data: bytes | None = None
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    layer: Mapping[str, Any] | None = None

    @classmethod
    def from_file(cls, path: Path) -> Attachment:
        path = path.expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return cls(
            id=hash_prompt(f"{path}:{size}")[:12],
            source="file",
            mime=mime,
            path=path,
            data=None,
            size_bytes=size,
        )

    def ensure_loaded(self) -> bytes:
        """ファイル添付の場合に遅延読み込みを行う。"""

        if self.data is not None:
            return self.data
        if self.path is None:
            raise ValueError("attachment has no data source")
        binary = self.path.read_bytes()
        self.data = binary
        self.size_bytes = len(binary)
        return binary

    def as_payload(self) -> dict[str, Any]:
        """JobSpec へ埋め込むための辞書化。"""

        payload: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "mime": self.mime,
            "data": self.ensure_loaded(),
            "width": int(self.width),
            "height": int(self.height),
        }
        if self.layer:
            payload["layer"] = dict(self.layer)
        if self.path:
            payload["path"] = str(self.path)
        return payload


@dataclass(slots=True)
class PromptPayload:
    """UI から集めた LLM 投稿内容を表す。"""

    provider: str
    model: str
    prompt: str | Sequence[Mapping[str, Any]]
    system_prompt: str | None = None
    expect_json: bool = False
    stream: bool = True
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int | None = DEFAULT_MAX_TOKENS
    attachments: tuple[Attachment, ...] = ()
    json_schema_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if isinstance(self.prompt, str):
            text_len = len(self.prompt)
        else:
            text_len = sum(
                len(part.get("content", "")) for part in self._coerce_messages(self.prompt)
            )
        if text_len == 0:
            warnings.append("Prompt is empty.")
        elif text_len > MAX_PROMPT_CHARS:
            warnings.append(f"Prompt is too long ({text_len} chars)")
        if len(self.attachments) > MAX_ATTACHMENTS:
            warnings.append(f"Too many attachments (max {MAX_ATTACHMENTS})")
        total_bytes = 0
        for attachment in self.attachments:
            try:
                size = attachment.size_bytes or len(attachment.ensure_loaded())
            except Exception as exc:  # pragma: no cover - 予防的処理
                warnings.append(f"Failed to load attachment {attachment.id}: {exc}")
                continue
            total_bytes += size
            if size > _MAX_ATTACHMENT_BYTES:
                warnings.append(f"Attachment {attachment.id} exceeds {MAX_ATTACHMENT_MB} MB.")
        if total_bytes > MAX_ATTACHMENTS * _MAX_ATTACHMENT_BYTES:
            warnings.append("Combined attachments exceed recommended total size.")
        if self.expect_json and not self.json_schema_path:
            warnings.append("JSON mode enabled without schema.")
        return warnings

    def to_job_spec(self) -> JobSpec:
        messages = list(self._coerce_messages(self.prompt))
        schema_payload: dict[str, Any] | None = None
        if self.expect_json and self.json_schema_path:
            schema_payload = load_json_schema(self.json_schema_path)
        params: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "prompt": messages,
            "stream": bool(self.stream),
            "response_format": "json" if self.expect_json else "text",
            "temperature": float(self.temperature),
            "max_tokens": self.max_tokens,
            "images": [attachment.as_payload() for attachment in self.attachments],
        }
        if self.system_prompt:
            params["system"] = self.system_prompt
        if schema_payload:
            params["json_schema"] = schema_payload
        if self.metadata:
            params["metadata"] = dict(self.metadata)
        return JobSpec(op="prompt_completion", params=params)

    @staticmethod
    def _coerce_messages(prompt: str | Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        if isinstance(prompt, str):
            return ({"role": "user", "content": prompt},)
        return prompt


@dataclass(slots=True, frozen=True)
class RecentPrompt:
    """RecentPromptRepository で保持する履歴エントリ。"""

    id: str
    provider: str
    model: str
    prompt_hash: str
    timestamp: float
    summary: str


def load_json_schema(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # pragma: no cover - ロギングのみ
        logger.warning("Failed to load schema %s: %s", path, exc)
        return {}


def hash_prompt(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:16]


def format_prompt_for_log(payload: PromptPayload, *, limit: int = 200) -> str:
    if isinstance(payload.prompt, str):
        text = payload.prompt
    else:
        text = json.dumps(list(payload._coerce_messages(payload.prompt)), ensure_ascii=False)
    trimmed = text.strip().replace("\n", " ")
    if len(trimmed) > limit:
        trimmed = trimmed[: limit - 3] + "..."
    parts = [
        f"provider={payload.provider}",
        f"model={payload.model}",
        f"len={len(text)}",
        f"stream={payload.stream}",
        f"json={payload.expect_json}",
        f'prompt="{trimmed}"',
    ]
    return " ".join(parts)


def sanitize_filename(title: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", title).strip("._")
    return safe or "snippet"


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "MAX_ATTACHMENTS",
    "MAX_ATTACHMENT_MB",
    "MAX_PROMPT_CHARS",
    "PLACEHOLDER_PATTERN",
    "RECENT_PROMPTS_FILE",
    "SNIPPET_DB_NAME",
    "TEXT_EDITOR_NAMESPACE",
    "Attachment",
    "PromptPayload",
    "RecentPrompt",
    "Snippet",
    "SnippetKind",
    "format_prompt_for_log",
    "hash_prompt",
    "load_json_schema",
    "sanitize_filename",
]
