"""Helpers for Qt translation / localization handling."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, Qt, QTranslator

from .shared_catalog_translator import SharedCatalogTranslator

TRANSLATION_PREFIX = "loomstroke_"
TRANSLATION_SUFFIX = ".qm"
DEFAULT_LANGUAGE = "en"
logger = logging.getLogger(__name__)

_LANGUAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("ja", "日本語 / Japanese"),
    ("zh_CN", "简体中文 / Chinese (Simplified)"),
    ("zh_Hant", "繁體中文 / Chinese (Traditional)"),
    ("zh_TW", "台灣華語 / Taiwan Mandarin"),
    ("ko", "한국어 / Korean"),
    ("es", "Español / Spanish"),
    ("ar", "العربية / Arabic"),
    ("hi", "हिन्दी / Hindi"),
    ("pt_BR", "Português (Brasil) / Portuguese (Brazil)"),
    ("bn", "বাংলা / Bengali"),
    ("ru", "Русский / Russian"),
)

_LANGUAGE_LABEL_MAP: dict[str, str] = dict(_LANGUAGE_LABELS)
_LANGUAGE_ALIASES: dict[str, str] = {
    "en_us": "en",
    "en_gb": "en",
    "ja_jp": "ja",
    "zh": "zh_CN",
    "zh_cn": "zh_CN",
    "zh_sg": "zh_CN",
    "zh_hans": "zh_CN",
    "zh_tw": "zh_TW",
    "zh_hk": "zh_Hant",
    "zh_mo": "zh_Hant",
    "zh_hant": "zh_Hant",
    "ko_kr": "ko",
    "es_es": "es",
    "es_mx": "es",
    "ar_sa": "ar",
    "hi_in": "hi",
    "pt": "pt_BR",
    "pt_pt": "pt_BR",
    "pt_br": "pt_BR",
    "bn_bd": "bn",
    "ru_ru": "ru",
    "nan": "zh_TW",
    "nan_tw": "zh_TW",
}

__all__ = [
    "DEFAULT_LANGUAGE",
    "TranslationDomainRegistry",
    "TranslationLoader",
    "available_language_options",
    "build_translator",
    "discover_translation_codes",
    "format_language_label",
    "layout_direction_for_language",
    "load_translator",
    "resolve_language",
    "translation_domains",
    "translation_path_for",
]


TranslationLoader = Callable[[str], QTranslator | None]


def _translation_roots() -> list[Path]:
    base = Path(__file__).resolve().parent
    return [
        base / "resources" / "i18n",
        base.parent.parent / "resources" / "i18n",
        Path.cwd() / "resources" / "i18n",
    ]


def _canonical_language_code(code: str) -> str:
    normalized = str(code or "").strip().replace("-", "_")
    if not normalized:
        return DEFAULT_LANGUAGE

    parts = [part for part in normalized.split("_") if part]
    if not parts:
        return DEFAULT_LANGUAGE

    language = parts[0].lower()
    if len(parts) == 1:
        return _LANGUAGE_ALIASES.get(language, language)

    territory = parts[1]
    if len(territory) == 2:
        territory = territory.upper()
    elif len(territory) == 4:
        territory = territory.title()

    canonical = f"{language}_{territory}"
    return _LANGUAGE_ALIASES.get(canonical.lower(), canonical)


def _translation_candidates(code: str) -> tuple[str, ...]:
    canonical = _canonical_language_code(code)
    if not canonical or canonical == "system":
        return tuple()

    candidates: list[str] = []

    def _append(candidate: str) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    _append(canonical)
    base = canonical.split("_", 1)[0]
    _append(base)
    _append(DEFAULT_LANGUAGE)
    return tuple(candidates)


def _discovered_translation_codes() -> tuple[str, ...]:
    codes: set[str] = set()
    for root in _translation_roots():
        if not root.is_dir():
            continue
        for file in root.glob(f"{TRANSLATION_PREFIX}*{TRANSLATION_SUFFIX}"):
            stem = file.stem
            suffix = stem.removeprefix(TRANSLATION_PREFIX)
            if suffix:
                codes.add(_canonical_language_code(suffix))
    if not codes:
        codes.add(DEFAULT_LANGUAGE)
    return tuple(sorted(codes))


@lru_cache(maxsize=1)
def discover_translation_codes() -> tuple[str, ...]:
    """Return configured + discovered translation codes shown in the language picker."""

    discovered = set(_discovered_translation_codes())
    codes: list[str] = []
    for code, _label in _LANGUAGE_LABELS:
        if code not in codes:
            codes.append(code)
    for code in sorted(discovered):
        if code not in codes:
            codes.append(code)
    return tuple(codes)


def format_language_label(code: str) -> str:
    """Return a human-friendly label for a locale code."""

    canonical = _canonical_language_code(code)
    label = _LANGUAGE_LABEL_MAP.get(canonical)
    if label:
        return label

    locale = QLocale(canonical)
    native = locale.nativeLanguageName().strip()
    english = QLocale.languageToString(locale.language()).strip()
    if native and english and native.lower() != english.lower():
        return f"{native} / {english} ({canonical})"
    if english:
        return f"{english} ({canonical})"
    return canonical


def available_language_options() -> list[tuple[str, str]]:
    """Return list of (code, label) pairs for UI pickers."""

    return [(code, format_language_label(code)) for code in discover_translation_codes()]


def resolve_language(code: str) -> str:
    """Resolve the system preference using OS display languages in priority order."""
    if code and code != "system":
        return code
    supported = set(discover_translation_codes())
    for language in QLocale.system().uiLanguages():
        parts = language.replace("-", "_").split("_")
        candidates = [language]
        # Qt may include both script and territory (e.g. zh-Hant-TW).
        # Prefer the territory-specific catalog before the script catalog.
        if len(parts) >= 3:
            candidates.insert(0, f"{parts[0]}_{parts[-1]}")
        candidates.append(parts[0])
        for candidate in candidates:
            canonical = _canonical_language_code(candidate)
            if canonical in supported:
                return canonical
    return DEFAULT_LANGUAGE


def layout_direction_for_language(code: str) -> Qt.LayoutDirection:
    """Return layout direction appropriate for a language code."""

    if not code or code == "system":
        locale = QLocale.system()
    else:
        locale = QLocale(_canonical_language_code(code))
    return locale.textDirection()


def translation_path_for(code: str) -> Path | None:
    """Return the best available .qm path for ``code`` using locale fallback."""

    for candidate_code in _translation_candidates(resolve_language(code)):
        filename = f"{TRANSLATION_PREFIX}{candidate_code}{TRANSLATION_SUFFIX}"
        for root in _translation_roots():
            candidate = root / filename
            if candidate.is_file():
                return candidate
    return None


def build_translator(code: str) -> QTranslator | None:
    """Build a QTranslator for ``code`` without installing it."""

    path = translation_path_for(code)
    if path is None:
        logger.info("No translation file found for language: %s", code)
        return None
    translator = SharedCatalogTranslator()
    if translator.load(str(path)):
        logger.info("Loaded translator: %s", path)
        return translator
    logger.warning("Failed to load translator from %s", path)
    return None


def load_translator(code: str) -> QTranslator | None:
    """Backward-compatible wrapper for :func:`build_translator`."""

    return build_translator(code)


class TranslationDomainRegistry:
    """Keep track of plugin translation domains and their translators."""

    def __init__(self) -> None:
        self._loaders: dict[str, TranslationLoader] = {}
        self._active: dict[str, QTranslator] = {}

    def register(self, name: str, loader: TranslationLoader) -> Callable[[], None]:
        if not name:
            raise ValueError("Translation domain name must be non-empty")
        self._loaders[name] = loader

        def _unregister() -> None:
            self.unregister(name)

        return _unregister

    def unregister(self, name: str) -> None:
        self._loaders.pop(name, None)
        translator = self._active.pop(name, None)
        if translator is not None:
            QCoreApplication.removeTranslator(translator)

    def clear(self) -> None:
        for translator in self._active.values():
            QCoreApplication.removeTranslator(translator)
        self._active.clear()
        self._loaders.clear()

    def reload(self, language: str) -> None:
        """Reload every registered domain for the given ``language``."""

        for translator in list(self._active.values()):
            QCoreApplication.removeTranslator(translator)
        self._active.clear()

        for name, loader in self._loaders.items():
            domain_translator: QTranslator | None = None
            try:
                domain_translator = loader(language)
            except Exception as exc:  # pragma: no cover - plugin supplied loader
                logger.warning("Translation loader for '%s' failed: %s", name, exc)
                continue
            if domain_translator is None:
                continue
            try:
                QCoreApplication.installTranslator(domain_translator)
            except Exception as exc:  # pragma: no cover - Qt install guard
                logger.warning("Failed to install translator for '%s': %s", name, exc)
                continue
            self._active[name] = domain_translator


translation_domains = TranslationDomainRegistry()
