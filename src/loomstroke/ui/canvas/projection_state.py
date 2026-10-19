"""Projection cache の構造変更検出ユーティリティ (Phase 2)。

GPU projection cache が保持すべきキャッシュを適切に無効化するため、
レイヤーツリー・各レイヤーの合成関連状態の変化を検出する。

このモジュールは純粋ロジックのみで GL 依存はない。テストしやすさのため
`ProjectionStateTracker` クラスにキャッシュ状態を集約している。

仕様: docs/dev/gpu_rendering_pipeline_specs.md (旧 gpu_projection_tile_spec.md 相当) §4.6, §4.12
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loomstroke.core.appearance.live_styles import style_signature

__all__ = [
    "ProjectionStateTracker",
    "StructuralChangeFlags",
    "clip_dirty_rect",
    "compute_active_flat_idx",
    "compute_layer_state",
    "flatten_all_layers_with_parents",
]


def clip_dirty_rect(
    dirty_x: int,
    dirty_y: int,
    dirty_w: int,
    dirty_h: int,
    canvas_w: int,
    canvas_h: int,
) -> tuple[int, int, int, int] | None:
    """Clip a top-origin dirty rect to the canvas bounds.

    Returns ``(x, y, w, h)`` of the clipped rect, or ``None`` when the
    result is empty.  Using plain ints (not QRect) keeps this function
    Qt-free and cheaply unit-testable.

    See §4.7.3 of ``docs/dev/gpu_rendering_pipeline_specs.md`` (formerly
    ``gpu_projection_tile_spec.md``).
    """
    if dirty_w <= 0 or dirty_h <= 0:
        return None
    x0 = max(0, dirty_x)
    y0 = max(0, dirty_y)
    x1 = min(canvas_w, dirty_x + dirty_w)
    y1 = min(canvas_h, dirty_y + dirty_h)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


@dataclass
class StructuralChangeFlags:
    """1 フレーム分の構造変更の検出結果。"""

    canvas_resized: bool = False
    background_changed: bool = False
    layer_list_changed: bool = False
    layer_parent_changed: bool = False
    active_changed: bool = False
    active_delta: int = 0
    active_crossed_group: bool = False
    active_state_changed: bool = False
    active_offset_only_changed: bool = False
    below_changed: bool = False
    above_changed: bool = False
    any_changed: bool = False


def _blend_mode_str(value: Any) -> str:
    """BlendMode enum / str を str に正規化。"""
    if value is None:
        return "normal"
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def compute_active_flat_idx(flat_layers: list[Any], active_layer_id: str | None) -> int:
    """flat_layers 内で active_layer_id のインデックスを返す。

    _active_index はトップレベル座標だが flat_layers は group 展開後の
    フラットリストなので、インデックスを混同しないよう必ず ID で引く。
    """
    if active_layer_id is None:
        return -1
    for i, layer in enumerate(flat_layers):
        if getattr(layer, "id", None) == active_layer_id:
            return i
    return -1


def compute_layer_state(layer: Any, effect_hash: int = 0) -> tuple[Any, ...]:
    """合成結果に影響する全パラメータを tuple 化。

    この tuple の変化が検出されると projection cache が無効化される。
    """
    return (
        int(getattr(layer, "texture_version", 0)),
        int(getattr(layer, "transform_version", 0)),
        int(getattr(layer, "_preview_transform_version", 0)),
        _blend_mode_str(getattr(layer, "blend_mode", "normal")),
        round(float(getattr(layer, "opacity", 1.0)), 4),
        bool(getattr(layer, "clipping", False)),
        bool(getattr(layer, "visible", True)),
        int(getattr(layer, "offset_x", 0)),
        int(getattr(layer, "offset_y", 0)),
        str(getattr(layer, "layer_type", "raster")),
        getattr(layer, "layer_mask", None) is not None,
        bool(getattr(layer, "mask_enabled", True)),
        int(effect_hash),
        style_signature(layer),
    )


def flatten_all_layers_with_parents(
    layers: list[Any],
    parent_id: str | None = None,
) -> tuple[list[Any], list[str | None]]:
    """レイヤーツリーを flatten しつつ各要素の親 ID を記録。

    group 境界をまたぐ active 移動判定に使用。
    CanvasWidget._flatten_all_layers と同じ走査順を保つ。
    """
    result: list[Any] = []
    parents: list[str | None] = []
    for layer in layers:
        if not getattr(layer, "visible", True):
            continue
        result.append(layer)
        parents.append(parent_id)
        children = getattr(layer, "children", None)
        if children:
            sub_r, sub_p = flatten_all_layers_with_parents(children, getattr(layer, "id", None))
            result.extend(sub_r)
            parents.extend(sub_p)
    return result, parents


class ProjectionStateTracker:
    """Projection cache の無効化判定に必要な直前状態を保持する。"""

    def __init__(self) -> None:
        self.layer_state_versions: dict[str, tuple[Any, ...]] = {}
        self.layer_effect_hashes: dict[str, int] = {}
        self.last_active_flat_idx: int = -1
        self.last_active_parent_id: str | None = None
        self.last_layer_id_list: tuple[str | None, ...] = ()
        self.last_layer_parent_ids: tuple[str | None, ...] = ()
        self.last_bg: Any = None
        self.last_w: int = 0
        self.last_h: int = 0
        self.initialized: bool = False
        # SSO (Screen-Space Overlay) で描画される eligible vector layer の ID 集合。
        # これらの layer は texture_version 変動を projection cache rebuild から除外する。
        # 仕様: 01_design_spec.md §4.5 / §6.2 (rev.6 R-7/R-9)
        self._skipped_layer_ids: set[str] = set()

    def reset(self) -> None:
        """全キャッシュ状態をクリア (GL context loss 時などに呼ぶ)。"""
        self.layer_state_versions.clear()
        self.layer_effect_hashes.clear()
        self.last_active_flat_idx = -1
        self.last_active_parent_id = None
        self.last_layer_id_list = ()
        self.last_layer_parent_ids = ()
        self.last_bg = None
        self.last_w = 0
        self.last_h = 0
        self.initialized = False
        self._skipped_layer_ids = set()

    def set_effect_hash(self, layer_id: str, hash_value: int) -> None:
        """Register an effect-chain hash calculated by the active GPU effect path."""
        self.layer_effect_hashes[layer_id] = int(hash_value)

    def set_skipped_layer_ids(self, ids: set[str]) -> None:
        """SSO 経路で描画される layer を projection cache 計算から除外する。

        eligibility 遷移時の texture_version bump で projection cache が
        無駄に rebuild されるのを防ぐ (R-7)。

        渡された set は防御的にコピーするので caller 側の変更は反映されない。
        """
        self._skipped_layer_ids = set(ids)

    def _compute_layer_state(self, layer: Any, effect_hash: int = 0) -> tuple[Any, ...]:
        """skip set 対応版の compute_layer_state (R-9)。

        ``layer.id`` が ``_skipped_layer_ids`` に含まれる場合は固定 tuple
        ``(lid, "SSO_SKIPPED")`` を返し、texture_version 等の変化を
        projection cache の invalidation トリガから除外する。

        含まれない場合はモジュール関数 :func:`compute_layer_state` に委譲する
        (既存挙動完全互換)。
        """
        lid = getattr(layer, "id", None)
        if lid in self._skipped_layer_ids:
            return (lid, "SSO_SKIPPED")
        return compute_layer_state(layer, effect_hash)

    def detect_changes(
        self,
        flat_layers: list[Any],
        parent_ids: list[str | None],
        active_flat_idx: int,
        canvas_w: int,
        canvas_h: int,
        background: Any,
    ) -> StructuralChangeFlags:
        """flat_layers と直前状態を比較して変更 flag を立てる。

        副作用: 内部キャッシュを最新状態に更新する。
        """
        flags = StructuralChangeFlags()

        new_ids: tuple[str | None, ...] = tuple(getattr(lay, "id", None) for lay in flat_layers)
        new_parents: tuple[str | None, ...] = tuple(parent_ids)
        new_active_parent = (
            new_parents[active_flat_idx] if 0 <= active_flat_idx < len(new_parents) else None
        )

        if not self.initialized:
            # 初回: 全項目が変更とみなす
            flags.canvas_resized = True
            flags.background_changed = True
            flags.layer_list_changed = True
            flags.layer_parent_changed = True
            flags.active_changed = True
            flags.active_delta = active_flat_idx - (-1)
            flags.active_crossed_group = True
            flags.below_changed = True
            flags.above_changed = True
            # state を初期化
            for layer in flat_layers:
                lid = getattr(layer, "id", None)
                if lid is not None:
                    self.layer_state_versions[lid] = self._compute_layer_state(
                        layer, self.layer_effect_hashes.get(lid, 0)
                    )
        else:
            # canvas size
            if (canvas_w, canvas_h) != (self.last_w, self.last_h):
                flags.canvas_resized = True
            # background
            if background != self.last_bg:
                flags.background_changed = True
            # layer list (ID の順序)
            if new_ids != self.last_layer_id_list:
                flags.layer_list_changed = True
            # parent (group 移動)
            if new_parents != self.last_layer_parent_ids:
                flags.layer_parent_changed = True
            # active index
            if active_flat_idx != self.last_active_flat_idx:
                flags.active_changed = True
                flags.active_delta = active_flat_idx - self.last_active_flat_idx
                flags.active_crossed_group = new_active_parent != self.last_active_parent_id
            # 各レイヤー state
            for i, layer in enumerate(flat_layers):
                lid = getattr(layer, "id", None)
                if lid is None:
                    continue
                new_state = self._compute_layer_state(layer, self.layer_effect_hashes.get(lid, 0))
                old_state = self.layer_state_versions.get(lid)
                if old_state != new_state:
                    self.layer_state_versions[lid] = new_state
                    if i == active_flat_idx:
                        # Only non-texture-content fields matter for
                        # active_state_changed (spec §6: "active state
                        # 変更" = blend_mode/opacity/etc, NOT texture
                        # pixel changes).  texture_version is state[0];
                        # pixel changes are handled by the incremental
                        # scissor path via content_changed + dirty_rect.
                        if old_state is not None and old_state[1:] != new_state[1:]:
                            # state tuple: (tex_ver, transform_ver, preview_ver,
                            #               blend, opacity, clip, vis, offset_x,
                            #               offset_y, ltype, has_mask, mask_en,
                            #               effect_hash)
                            # indices 7,8 = offset_x/y.  If only those changed,
                            # use a cheaper incremental path (move preview).
                            non_offset_old = old_state[1:7] + old_state[9:]
                            non_offset_new = new_state[1:7] + new_state[9:]
                            if non_offset_old == non_offset_new:
                                flags.active_offset_only_changed = True
                            else:
                                flags.active_state_changed = True
                    elif i < active_flat_idx:
                        flags.below_changed = True
                    else:
                        flags.above_changed = True

            # 消えたレイヤーの state を掃除
            current_ids = {lid for lid in new_ids if lid is not None}
            stale = set(self.layer_state_versions.keys()) - current_ids
            for lid in stale:
                self.layer_state_versions.pop(lid, None)
                self.layer_effect_hashes.pop(lid, None)

        flags.any_changed = any(
            (
                flags.canvas_resized,
                flags.background_changed,
                flags.layer_list_changed,
                flags.layer_parent_changed,
                flags.active_changed,
                flags.active_state_changed,
                flags.active_offset_only_changed,
                flags.below_changed,
                flags.above_changed,
            )
        )

        # snapshot
        self.last_layer_id_list = new_ids
        self.last_layer_parent_ids = new_parents
        self.last_active_flat_idx = active_flat_idx
        self.last_active_parent_id = new_active_parent
        self.last_w = canvas_w
        self.last_h = canvas_h
        self.last_bg = background
        self.initialized = True

        return flags
