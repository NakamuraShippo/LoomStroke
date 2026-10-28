"""Read-only, call-local candidates for the Comic Assets panel.

The public store's tolerant read API remains unchanged. Only its exact standard
capability uses checked filesystem enumeration; unknown stores keep their public
categories/list calls and can report only failures they explicitly raise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

from .comic_assets import (
    _COMIC_ASSET_LISTING_DEFAULTS,
    CATEGORY_LABELS,
    ComicAsset,
    ComicAssetStore,
    _safe_name,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetListingResult:
    categories: tuple[str, ...]
    assets: dict[str, tuple[ComicAsset, ...]]
    failed_categories: frozenset[str] = frozenset()
    root_failed: bool = False


def unavailable_listing() -> AssetListingResult:
    return AssetListingResult(tuple(CATEGORY_LABELS), {}, root_failed=True)


def read_assets_for_panel(store: Any) -> AssetListingResult:
    standard = type(store) is ComicAssetStore and all(
        getattr_static(store, name, None) is original
        for name, original in _COMIC_ASSET_LISTING_DEFAULTS
    )
    return _read_standard(store) if standard else _read_compatible(store)


def _read_standard(store: ComicAssetStore) -> AssetListingResult:
    categories = list(CATEGORY_LABELS)
    try:
        root = store.root
        try:
            children = sorted(root.iterdir())
        except FileNotFoundError:
            children = []
        for child in children:
            if child.is_dir() and child.name not in categories:
                categories.append(child.name)
    except Exception:
        logger.debug("Comic asset root could not be read")
        return unavailable_listing()

    assets: dict[str, tuple[ComicAsset, ...]] = {}
    failed: set[str] = set()
    for category in categories:
        try:
            folder = root / _safe_name(category)
            try:
                children = sorted(folder.iterdir())
            except FileNotFoundError:
                children = []
            candidates = []
            for child in children:
                if not child.is_dir():
                    continue
                # Match list()'s exact folder path and existing codec. Going
                # through load(category, child.name) could sanitize it again.
                asset = store._read(child, category)
                if asset is None:
                    raise ValueError("unreadable comic asset")
                candidates.append(asset)
            candidates.sort(key=lambda asset: (asset.created_at, asset.asset_id))
            assets[category] = tuple(candidates)
        except Exception:
            logger.debug("Comic asset category could not be read")
            failed.add(category)
    return AssetListingResult(tuple(categories), assets, frozenset(failed))


def _read_compatible(store: Any) -> AssetListingResult:
    categories = list(CATEGORY_LABELS)
    try:
        for category in store.categories():
            if category not in categories:
                categories.append(category)
    except Exception:
        logger.debug("Comic asset provider categories could not be read")
        return unavailable_listing()
    assets: dict[str, tuple[ComicAsset, ...]] = {}
    failed: set[str] = set()
    for category in categories:
        try:
            assets[category] = tuple(store.list(category))
        except Exception:
            logger.debug("Comic asset provider category could not be read")
            failed.add(category)
    return AssetListingResult(tuple(categories), assets, frozenset(failed))
