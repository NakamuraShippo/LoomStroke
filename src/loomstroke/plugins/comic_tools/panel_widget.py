"""Comic Tools dock panel: Panels / Balloon / Dialogue tabs.

Thin UI over :class:`ComicToolsPlugin` — every mutation goes through the
tool's command builders (single undo units); this widget never touches the
canvas or the document directly.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QItemSelectionModel, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import layout_tree as lt
from .dialogue import build_dialogue_rows
from .dialogue_batch_actions import edit_selected_dialogues
from .dialogue_delegate import DialogueDelegate, editing_hint
from .dialogue_list_refresh import DialogueListRefresh
from .dialogue_selection import select_dialogue_row
from .dialogue_tags import update_dialogue_tags
from .frame_controls import ts as panel_ts
from .frame_panel_labels import panel_button, panel_label, retranslate_panel
from .model import BALLOON_KINDS, is_raster_rig
from .panel_fidelity import FidelityControl, handles_label, refresh_contour_controls
from .panel_formatting import ComicFormattingWidget
from .panel_order_refresh import PanelOrderRefresh
from .panel_speaker_styles import SpeakerStylesWidget, speaker_styles_title
from .panel_workflow import (
    BalloonWorkflow,
    contour_mode_label,
    contour_preview_hint,
    contour_reason,
    fold_rows,
    selection_hint,
)
from .raster_rig.contour_detail import normalized_fidelity
from .speaker_delegate import SpeakerDelegate

#: compositing modes of the raster rig: (stable value, display text)
RIG_MODES = (
    ("contour_rebuild", "Continuous contour (standard)"),
    ("open_ink_fill", "付け根を開いて塗る (標準)"),
    ("open_forced", "付け根を開く (透明)"),
    ("registration_default", "登録時の設定のまま"),
)


class _LayerCombo(QComboBox):
    """QComboBox that refreshes its items right before the popup opens
    (layers added / renamed after the panel was built show up)."""

    def __init__(self, reload: Callable[[], None]) -> None:
        super().__init__()
        self._reload = reload

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        with contextlib.suppress(Exception):  # never break the popup
            self._reload()
        super().showPopup()


if TYPE_CHECKING:
    from .tool import ComicToolsPlugin

_KIND_LABELS = {
    "speech_round": "楕円",
    "speech_rect": "角丸矩形",
    "speech_cloud": "雲形 (思考)",
    "spiky_balloon": "ギザギザ (叫び)",
}

_TEMPLATE_LABELS = {
    "single": "Single panel",
    "2v": "Two columns",
    "2h": "Two rows",
    "3": "Three panels",
    "4": "Four panels (grid)",
    "yonkoma": "Four-panel strip",
    "6": "Six panels",
}


class ComicToolsPanelWidget(QWidget):
    def __init__(self, tool: ComicToolsPlugin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool = tool
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        panels_scroll = QScrollArea()
        panels_scroll.setWidgetResizable(True)
        panels_scroll.setWidget(self._build_panels_tab())
        self.tabs.addTab(panels_scroll, "コマ割り")
        self.tabs.addTab(self._build_balloon_tab(), "フキダシ")
        self.tabs.addTab(self._build_dialogue_tab(), "セリフ一覧")
        self.speaker_styles = SpeakerStylesWidget(tool, self)
        speaker_scroll = QScrollArea()
        speaker_scroll.setWidgetResizable(True)
        speaker_scroll.setWidget(self.speaker_styles)
        self.tabs.addTab(speaker_scroll, speaker_styles_title())
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.formatting = ComicFormattingWidget(tool, self)
        layout.addWidget(self.formatting)
        self.formatting.setVisible(False)
        self.dialogue_table.currentItemChanged.connect(
            lambda current, previous: select_dialogue_row(self, current, previous)
        )
        self._dialogue_refresh = DialogueListRefresh(self)
        self._order_refresh = PanelOrderRefresh(self)
        self.refresh_from_tool()
        retranslate_panel(self, _TEMPLATE_LABELS)

    # ── Panels tab ─────────────────────────────────────────────────────

    def _build_panels_tab(self) -> QWidget:  # noqa: PLR0915 - linear widget construction
        page = QWidget()
        form = QVBoxLayout(page)
        self._panel_sources = []

        from .frame_controls import FrameControls  # noqa: PLC0415

        self.frame_controls = FrameControls(self._tool.frame_workflow, page)

        template_row = QHBoxLayout()
        self.template_combo = QComboBox()
        for template_id in lt.TEMPLATE_IDS:
            self.template_combo.addItem(panel_ts(_TEMPLATE_LABELS[template_id]), template_id)
        self.template_combo.setMinimumContentsLength(8)
        self.template_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        apply_btn = panel_button(self, "Apply template")
        apply_btn.clicked.connect(self._apply_template)
        template_row.addWidget(self.template_combo, 1)
        template_row.addWidget(apply_btn)
        form.addLayout(template_row)

        split_row = QHBoxLayout()
        self.split_v_btn = panel_button(self, "Split vertically")
        self.split_h_btn = panel_button(self, "Split horizontally")
        self.split_v_btn.clicked.connect(lambda: self._tool.split_selected("vertical"))
        self.split_h_btn.clicked.connect(lambda: self._tool.split_selected("horizontal"))
        split_row.addWidget(self.split_v_btn)
        split_row.addWidget(self.split_h_btn)
        form.addLayout(split_row)

        ratio_row = QHBoxLayout()
        ratio_row.addWidget(panel_label(self, "Ratio"))
        self.ratio_spin = QDoubleSpinBox()
        self.ratio_spin.setRange(0.05, 0.95)
        self.ratio_spin.setSingleStep(0.05)
        self.ratio_spin.setValue(0.5)
        ratio_split_btn = panel_button(self, "Split by ratio")
        ratio_split_btn.clicked.connect(
            lambda: self._tool.split_selected("horizontal", ratio=self.ratio_spin.value())
        )
        ratio_apply_btn = panel_button(self, "Change ratio")
        ratio_apply_btn.clicked.connect(
            lambda: self._tool.set_selected_ratio(self.ratio_spin.value())
        )
        ratio_row.addWidget(self.ratio_spin)
        ratio_row.addWidget(ratio_split_btn)
        ratio_row.addWidget(ratio_apply_btn)
        form.addLayout(ratio_row)

        gutter_row = QHBoxLayout()
        gutter_row.addWidget(panel_label(self, "Gutter"))
        self.gutter_spin = QDoubleSpinBox()
        self.gutter_spin.setRange(0.0, 200.0)
        self.gutter_spin.setValue(lt.DEFAULT_GUTTER_PX)
        gutter_apply = panel_button(self, "Apply")
        gutter_apply.clicked.connect(lambda: self._tool.set_gutter(self.gutter_spin.value()))
        gutter_row.addWidget(self.gutter_spin)
        gutter_row.addWidget(gutter_apply)
        form.addLayout(gutter_row)

        from .frame_numeric_inputs import bind_panel_numeric_inputs  # noqa: PLC0415

        bind_panel_numeric_inputs(self)

        action_row = QHBoxLayout()
        self.merge_btn = panel_button(self, "Merge with next")
        self.merge_btn.clicked.connect(self._merge_with_next)
        self.delete_panel_btn = panel_button(self, "Delete panel")
        self.delete_panel_btn.clicked.connect(self._tool.delete_selected_panel)
        action_row.addWidget(self.merge_btn)
        action_row.addWidget(self.delete_panel_btn)
        form.addLayout(action_row)

        form.addWidget(panel_label(self, "Reading order (top right to bottom left)"))
        self.order_list = QListWidget()
        self.order_list.setMinimumHeight(72)
        self.order_list.setMaximumHeight(128)
        self.order_list.currentItemChanged.connect(self._on_order_selected)
        form.addWidget(self.order_list, 1)
        order_btns = QHBoxLayout()
        up_btn = panel_button(self, "Move up")
        down_btn = panel_button(self, "Move down")
        up_btn.clicked.connect(lambda: self._move_order(-1))
        down_btn.clicked.connect(lambda: self._move_order(1))
        order_btns.addWidget(up_btn)
        order_btns.addWidget(down_btn)
        form.addLayout(order_btns)
        form.addWidget(self.frame_controls)
        return page

    def _apply_template(self) -> None:
        template_id = str(self.template_combo.currentData() or "single")
        self._tool.apply_template(template_id, gutter_px=self.gutter_spin.value())

    def _merge_with_next(self) -> None:
        state = self._tool.read_state()
        if state is None or not self._tool.selected_panel_id:
            return
        order = list(state.reading_order)
        selected = self._tool.selected_panel_id
        if selected not in order:
            return
        index = order.index(selected)
        for candidate in order[index + 1 :] + order[:index]:
            if self._tool.merge_selected_with(candidate):
                return

    def _on_order_selected(self, current: QListWidgetItem | None, _prev: Any = None) -> None:
        if self._updating or current is None:
            return
        self._tool.selected_panel_id = str(current.data(Qt.ItemDataRole.UserRole))
        self._tool._refresh_canvas()  # noqa: SLF001 - same-plugin collaboration
        self.frame_controls.refresh_edges()

    def _move_order(self, delta: int) -> None:
        item = self.order_list.currentItem()
        if item is None:
            return
        panel_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._tool.move_reading_order(panel_id, delta)

    # ── Balloon tab ────────────────────────────────────────────────────

    def _build_balloon_tab(self) -> QWidget:  # noqa: PLR0915 - linear widget construction
        page = QWidget()
        form = QFormLayout(page)

        create_row = QHBoxLayout()
        self.kind_combo = QComboBox()
        for kind in BALLOON_KINDS:
            self.kind_combo.addItem(_KIND_LABELS[kind], kind)
        create_btn = QPushButton("フキダシ作成")
        create_btn.clicked.connect(self._create_balloon)
        create_wrap = QWidget()
        create_row.addWidget(self.kind_combo, 1)
        create_row.addWidget(create_btn)
        create_wrap.setLayout(create_row)
        form.addRow(create_wrap)

        self.vertical_check = QCheckBox("縦書き")
        self.vertical_check.setChecked(True)
        form.addRow("", self.vertical_check)

        self.auto_size_check = QCheckBox("テキストに合わせて自動サイズ調整")
        self.auto_size_check.setChecked(True)
        self.auto_size_check.toggled.connect(self._on_auto_size_toggled)
        form.addRow("", self.auto_size_check)

        self.body_open_check = QCheckBox("しっぽの付け根でバルーンを開く")
        self.body_open_check.setToolTip(
            "ON: 輪郭をしっぽの付け根で切り欠き、しっぽを動かすと開口部も追従します。"
            " OFF: 輪郭は常に閉じます (開いて描いた場合は隙間を線で埋めます)。"
        )
        self.body_open_check.setChecked(False)
        self.body_open_check.toggled.connect(self._on_body_open_toggled)
        form.addRow("", self.body_open_check)

        self.tail_follow_check = QCheckBox("付け根を先端に追従させる")
        self.tail_follow_check.setToolTip(
            "ON: 先端を動かすと付け根 (と開口) がバルーンの外周に沿って追従します。"
            " OFF: 付け根は今の位置に固定され、先端だけが動きます。"
        )
        self.tail_follow_check.setChecked(True)
        self.tail_follow_check.toggled.connect(self._on_tail_follow_toggled)
        form.addRow("", self.tail_follow_check)

        self.tail_join_check = QCheckBox("しっぽとバルーンの線を自動でつなぐ")
        self.tail_join_check.setToolTip(
            "しっぽの線の端とバルーンの輪郭を、ピクセルから推定した接線・太さ・色で"
            " 滑らかにつなぎます (離れすぎている場合はつなぎません)。"
        )
        self.tail_join_check.setChecked(True)
        self.tail_join_check.toggled.connect(self._on_tail_join_toggled)
        form.addRow("", self.tail_join_check)

        self.tail_gap_spin = QDoubleSpinBox()
        self.tail_gap_spin.setRange(-100.0, 400.0)
        self.tail_gap_spin.setDecimals(1)
        self.tail_gap_spin.setSingleStep(1.0)
        self.tail_gap_spin.setSuffix(" px")
        self.tail_gap_spin.setToolTip(
            "しっぽの付け根とバルーン外周の距離。変換時は描いたままの距離になります。"
            " 0 で密着、負の値で食い込み。"
        )
        self.tail_gap_spin.valueChanged.connect(self._on_tail_gap_changed)
        form.addRow("しっぽの距離", self.tail_gap_spin)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("セリフを入力...")
        self.text_edit.setMaximumHeight(120)
        form.addRow("セリフ", self.text_edit)
        apply_text_btn = QPushButton("セリフを反映")
        apply_text_btn.clicked.connect(self._apply_text)
        form.addRow("", apply_text_btn)

        self.speaker_id_edit = QLineEdit()
        self.speaker_id_edit.setPlaceholderText("speaker_id (例: char_a)")
        self.speaker_name_edit = QLineEdit()
        self.speaker_name_edit.setPlaceholderText("表示名 (例: 主人公)")
        form.addRow("話者ID", self.speaker_id_edit)
        form.addRow("話者名", self.speaker_name_edit)
        speaker_btn = QPushButton("話者を設定")
        speaker_btn.clicked.connect(self._apply_speaker)
        form.addRow("", speaker_btn)

        delete_btn = QPushButton("フキダシ削除")
        delete_btn.clicked.connect(self._delete_balloon)
        form.addRow("", delete_btn)

        # ── 吹き出し化 (半自動変換、確認ステップ付き) ──
        balloonize_btn = QPushButton("手描きフキダシを変換 (選択範囲/レイヤー)")
        balloonize_btn.clicked.connect(self._start_balloonize)
        form.addRow(balloonize_btn)

        self.balloonize_group = QWidget()
        bz_form = QFormLayout(self.balloonize_group)
        bz_form.setContentsMargins(8, 0, 0, 0)
        self.bz_status_label = QLabel("-")
        self.bz_status_label.setWordWrap(True)
        bz_form.addRow("候補", self.bz_status_label)
        self.bz_kind_combo = QComboBox()
        for kind in BALLOON_KINDS:
            self.bz_kind_combo.addItem(_KIND_LABELS[kind], kind)
        bz_form.addRow("種類", self.bz_kind_combo)
        self.bz_tail_check = QCheckBox("tail を付ける")
        bz_form.addRow("", self.bz_tail_check)
        tip_row = QHBoxLayout()
        self.bz_tip_x = QDoubleSpinBox()
        self.bz_tip_y = QDoubleSpinBox()
        for spin in (self.bz_tip_x, self.bz_tip_y):
            spin.setRange(-100000.0, 100000.0)
            tip_row.addWidget(spin)
        tip_wrap = QWidget()
        tip_wrap.setLayout(tip_row)
        bz_form.addRow("tail 先端", tip_wrap)
        size_row = QHBoxLayout()
        self.bz_body_w = QDoubleSpinBox()
        self.bz_body_h = QDoubleSpinBox()
        for spin in (self.bz_body_w, self.bz_body_h):
            spin.setRange(8.0, 100000.0)
            size_row.addWidget(spin)
        size_wrap = QWidget()
        size_wrap.setLayout(size_row)
        bz_form.addRow("body サイズ", size_wrap)
        self.bz_hide_source_check = QCheckBox("元レイヤーを非表示にする (削除はしない)")
        self.bz_hide_source_check.setChecked(True)
        bz_form.addRow("", self.bz_hide_source_check)
        confirm_row = QHBoxLayout()
        bz_confirm_btn = QPushButton("確定して変換")
        bz_confirm_btn.clicked.connect(self._confirm_balloonize)
        bz_cancel_btn = QPushButton("キャンセル")
        bz_cancel_btn.clicked.connect(self._cancel_balloonize)
        confirm_row.addWidget(bz_confirm_btn)
        confirm_row.addWidget(bz_cancel_btn)
        confirm_wrap = QWidget()
        confirm_wrap.setLayout(confirm_row)
        bz_form.addRow(confirm_wrap)
        self.balloonize_group.setVisible(False)
        form.addRow(self.balloonize_group)

        # -- Body/Tail レイヤーからフキダシ化 (raster rig) --
        rig_btn = QPushButton("Body/Tail レイヤーからフキダシ化")
        rig_btn.clicked.connect(self._start_rig)
        form.addRow(rig_btn)
        self._build_rig_group(form)

        self.balloon_info_label = QLabel("-")
        self.balloon_info_label.setWordWrap(True)
        form.addRow("選択中", self.balloon_info_label)
        asset_btn = QPushButton("選択中のフキダシを素材パネルに登録")
        asset_btn.setToolTip("Comic Assets パネルの「フキダシ」タブに素材として保存します")
        asset_btn.clicked.connect(self._register_asset)
        form.addRow(asset_btn)
        self._balloon_selection_actions = (apply_text_btn, speaker_btn, delete_btn, asset_btn)
        self.balloon_workflow = BalloonWorkflow(
            page,
            [
                ("selection", "Selected balloon", False, [self.balloon_info_label]),
                (
                    "create",
                    "Create a balloon",
                    False,
                    [create_wrap, rig_btn, balloonize_btn, self.balloonize_group],
                ),
                ("rig", "Hand-drawn connection / Preview", False, [self.rig_group]),
                (
                    "text",
                    "Dialogue",
                    False,
                    [self.text_edit, self.vertical_check, self.auto_size_check, apply_text_btn],
                ),
                (
                    "speaker",
                    "Speaker",
                    True,
                    [self.speaker_name_edit, self.speaker_id_edit, speaker_btn],
                ),
                (
                    "connection",
                    "Advanced connection settings",
                    True,
                    [
                        self.body_open_check,
                        self.tail_follow_check,
                        self.tail_join_check,
                        self.tail_gap_spin,
                    ],
                ),
                ("manage", "Asset registration / Delete", True, [asset_btn, delete_btn]),
            ],
        )
        return self.balloon_workflow.scroll

    def _create_balloon(self) -> None:
        kind = str(self.kind_combo.currentData() or "speech_round")
        balloon_id = self._tool.create_balloon(
            kind=kind,
            text=self.text_edit.toPlainText(),
            vertical=self.vertical_check.isChecked(),
        )
        if balloon_id:
            self.refresh_from_tool()

    def _apply_text(self) -> None:
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_balloon_text(balloon_id, self.text_edit.toPlainText())

    def _apply_speaker(self) -> None:
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_balloon_speaker(
                balloon_id,
                self.speaker_id_edit.text().strip(),
                self.speaker_name_edit.text().strip(),
            )

    def _on_auto_size_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_balloon_auto_size(balloon_id, checked)

    def _on_body_open_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_balloon_body_open(balloon_id, checked)

    def _on_tail_follow_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_tail_follow_tip(balloon_id, checked)

    def _on_tail_join_toggled(self, checked: bool) -> None:
        if self._updating:
            return
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_tail_auto_join(balloon_id, checked)

    def _on_tail_gap_changed(self, value: float) -> None:
        if self._updating:
            return
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.set_tail_base_gap(balloon_id, float(value))

    def _delete_balloon(self) -> None:
        balloon_id = self._tool.selected_balloon_id
        if balloon_id:
            self._tool.delete_balloon(balloon_id)

    def _start_balloonize(self) -> None:
        context = self._tool.propose_balloonize()
        if context is None:
            self.bz_status_label.setText(
                "フキダシを認識できませんでした (選択範囲かレイヤーを確認)"
            )
            self.balloonize_group.setVisible(False)
            return
        proposal = context.proposal
        notes = {
            "tail_not_detected": "tail 未検出 (手動で付けられます)",
            "tail_ambiguous": "tail 候補が曖昧です (要確認)",
        }
        joined = " / ".join(notes[n] for n in proposal.notes if n in notes)
        note_text = joined or "候補を確認してください"
        self.bz_status_label.setText(note_text)
        index = self.bz_kind_combo.findData(proposal.kind)
        if index >= 0:
            self.bz_kind_combo.setCurrentIndex(index)
        self.bz_tail_check.setChecked(proposal.tail.confidence != "none")
        self.bz_tip_x.setValue(proposal.tail.tip[0])
        self.bz_tip_y.setValue(proposal.tail.tip[1])
        self.bz_body_w.setValue(proposal.body_rect[2])
        self.bz_body_h.setValue(proposal.body_rect[3])
        self.balloonize_group.setVisible(True)

    def _confirm_balloonize(self) -> None:
        context = self._tool.pending_balloonize
        if context is None:
            self.balloonize_group.setVisible(False)
            return
        x, y, w, h = context.proposal.body_rect
        cx, cy = x + w * 0.5, y + h * 0.5
        new_w = self.bz_body_w.value()
        new_h = self.bz_body_h.value()
        body_rect = (cx - new_w * 0.5, cy - new_h * 0.5, new_w, new_h)
        balloon_id = self._tool.confirm_balloonize(
            kind=str(self.bz_kind_combo.currentData() or context.proposal.kind),
            body_rect=body_rect,
            tail_enabled=self.bz_tail_check.isChecked(),
            tail_tip=(self.bz_tip_x.value(), self.bz_tip_y.value()),
            text=self.text_edit.toPlainText(),
            vertical=self.vertical_check.isChecked(),
            hide_source_layer=self.bz_hide_source_check.isChecked(),
        )
        self.balloonize_group.setVisible(False)
        if balloon_id:
            self.refresh_from_tool()
            self.refresh_dialogue()

    def _cancel_balloonize(self) -> None:
        self._tool.cancel_balloonize()
        self.balloonize_group.setVisible(False)

    def _register_asset(self) -> None:
        asset = self._tool.register_selected_balloon_as_asset()
        self.balloon_info_label.setText(
            f"素材に登録しました: {asset.name}" if asset else "素材登録: フキダシを選択してください"
        )

    # ── raster rig (Body/Tail レイヤーからフキダシ化) ──────────────────
    def _build_rig_group(self, form: QFormLayout) -> None:  # noqa: PLR0915 - widget tree
        self._rig_syncing = False
        self.rig_group = QWidget()
        rig_form = QFormLayout(self.rig_group)
        rig_form.setContentsMargins(8, 0, 0, 0)

        # 1) layer pickers + register
        self.rig_pick_widget = QWidget()
        pick_form = QFormLayout(self.rig_pick_widget)
        pick_form.setContentsMargins(0, 0, 0, 0)
        self.rig_body_combo = _LayerCombo(self._populate_rig_layer_combos)
        self.rig_tail_combo = _LayerCombo(self._populate_rig_layer_combos)
        pick_form.addRow("Body レイヤー", self.rig_body_combo)
        pick_form.addRow("Tail レイヤー", self.rig_tail_combo)
        register_row = QHBoxLayout()
        rig_register_btn = QPushButton("登録してプレビュー")
        rig_register_btn.clicked.connect(self._register_rig)
        register_row.addWidget(rig_register_btn)
        # The legacy clean converter is still internal; frame availability
        # does not expose this separate experimental conversion mode.
        clean_btn = QPushButton("クリーン変換 (開発)")
        clean_btn.setToolTip("マスクを結合して整形ベクターに変換する旧モード (開発用)")
        clean_btn.clicked.connect(self._start_clean_dev)
        register_row.addWidget(clean_btn)
        clean_btn.hide()
        register_wrap = QWidget()
        register_wrap.setLayout(register_row)
        pick_form.addRow(register_wrap)
        self.rig_status_label = QLabel("-")
        self.rig_status_label.setWordWrap(True)
        pick_form.addRow("状態", self.rig_status_label)
        rig_form.addRow(self.rig_pick_widget)

        # 2) compositing parameters (live session or selected rig balloon)
        self.rig_param_widget = QWidget()
        param_form = QFormLayout(self.rig_param_widget)
        param_form.setContentsMargins(0, 0, 0, 0)
        self.rig_scale_spin = QDoubleSpinBox()
        self.rig_scale_spin.setRange(0.05, 8.0)
        self.rig_scale_spin.setSingleStep(0.05)
        self.rig_scale_spin.setValue(1.0)
        self.rig_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.rig_scale_slider.setRange(5, 800)
        self.rig_scale_slider.setValue(100)
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.rig_scale_spin)
        scale_row.addWidget(self.rig_scale_slider, 1)
        scale_wrap = QWidget()
        scale_wrap.setLayout(scale_row)
        param_form.addRow("しっぽの大きさ", scale_wrap)
        self.rig_fidelity_control = FidelityControl()
        param_form.addRow(self.rig_fidelity_control)
        self.rig_fidelity_control.valueChanged.connect(lambda _value: self._rig_param_changed())
        self.rig_tip_fidelity_control = FidelityControl(tip=True)
        param_form.addRow(self.rig_tip_fidelity_control)
        self.rig_tip_fidelity_control.valueChanged.connect(lambda _value: self._rig_param_changed())
        self.rig_mode_combo = QComboBox()
        for value, label in RIG_MODES:
            self.rig_mode_combo.addItem(
                contour_mode_label() if value == "contour_rebuild" else label, value
            )
        param_form.addRow("接続モード", self.rig_mode_combo)
        self.rig_inset_spin = QDoubleSpinBox()
        self.rig_inset_spin.setRange(0.0, 48.0)
        self.rig_inset_spin.setSingleStep(0.5)
        self.rig_inset_spin.setSuffix(" px")
        self.rig_inset_spin.setToolTip("接続部で本体側へ塗り込む幅")
        param_form.addRow("塗り込み", self.rig_inset_spin)
        self.rig_overlap_spin = QDoubleSpinBox()
        self.rig_overlap_spin.setRange(-24.0, 48.0)
        self.rig_overlap_spin.setSingleStep(0.5)
        self.rig_overlap_spin.setSuffix(" px")
        self.rig_overlap_spin.setToolTip("しっぽの付け根を本体側へ押し込む量 (負 = 輪郭の外)")
        param_form.addRow("付け根の深さ", self.rig_overlap_spin)
        self.rig_handles_check = QCheckBox(handles_label(True))
        self.rig_handles_check.setChecked(True)
        param_form.addRow("", self.rig_handles_check)
        self.rig_diag_check = QCheckBox("診断を表示 (開発)")
        self.rig_diag_label = QLabel("")
        self.rig_diag_label.setWordWrap(True)
        self.rig_diag_label.setVisible(False)
        param_form.addRow("", self.rig_diag_check)
        param_form.addRow(self.rig_diag_label)
        self.rig_advanced_section = fold_rows(
            param_form,
            "Fine tuning / Diagnostics",
            [
                self.rig_mode_combo,
                self.rig_inset_spin,
                self.rig_overlap_spin,
                self.rig_diag_check,
                self.rig_diag_label,
            ],
        )
        rig_form.addRow(self.rig_param_widget)

        # 3) confirm / cancel
        self.rig_confirm_widget = QWidget()
        confirm_form = QFormLayout(self.rig_confirm_widget)
        confirm_form.setContentsMargins(0, 0, 0, 0)
        self.rig_hide_source_check = QCheckBox("元レイヤーを非表示にする (削除はしない)")
        self.rig_hide_source_check.setChecked(True)
        confirm_form.addRow("", self.rig_hide_source_check)
        confirm_row = QHBoxLayout()
        rig_confirm_btn = QPushButton("確定して変換")
        rig_confirm_btn.clicked.connect(self._confirm_rig)
        rig_cancel_btn = QPushButton("キャンセル")
        rig_cancel_btn.clicked.connect(self._cancel_rig)
        confirm_row.addWidget(rig_confirm_btn)
        confirm_row.addWidget(rig_cancel_btn)
        confirm_wrap = QWidget()
        confirm_wrap.setLayout(confirm_row)
        confirm_form.addRow(confirm_wrap)
        rig_form.addRow(self.rig_confirm_widget)

        self.rig_scale_spin.valueChanged.connect(self._rig_scale_from_spin)
        self.rig_scale_slider.valueChanged.connect(self._rig_scale_from_slider)
        self.rig_mode_combo.currentIndexChanged.connect(self._rig_mode_changed)
        self.rig_inset_spin.valueChanged.connect(self._rig_spin_changed)
        self.rig_overlap_spin.valueChanged.connect(self._rig_spin_changed)
        self.rig_handles_check.toggled.connect(self._rig_handles_toggled)
        self.rig_diag_check.toggled.connect(self._rig_diag_toggled)
        self.rig_group.setVisible(False)
        self._rig_pick_requested = False
        form.addRow(self.rig_group)

    def _populate_rig_layer_combos(self) -> None:
        layers = self._tool.list_canvas_layers()
        for combo in (self.rig_body_combo, self.rig_tail_combo):
            current = combo.currentData()
            with QSignalBlocker(combo):
                combo.clear()
                for layer_id, name in layers:
                    combo.addItem(f"{name} ({layer_id})", layer_id)
                if current is not None:
                    index = combo.findData(current)
                    if index >= 0:
                        combo.setCurrentIndex(index)

    def _start_rig(self) -> None:
        self._populate_rig_layer_combos()
        self._rig_pick_requested = True
        self.rig_status_label.setText("Body / Tail レイヤーを選んで「登録してプレビュー」")
        self._refresh_rig_controls()

    def _register_rig(self) -> None:
        body_layer_id = str(self.rig_body_combo.currentData() or "")
        tail_layer_id = str(self.rig_tail_combo.currentData() or "")
        if not body_layer_id or not tail_layer_id:
            self.rig_status_label.setText("Body と Tail のレイヤーを両方選択してください")
            return
        if body_layer_id == tail_layer_id:
            self.rig_status_label.setText("Body と Tail は別のレイヤーを選んでください")
            return
        session = self._tool.start_rig_registration(body_layer_id, tail_layer_id)
        if session is None:
            reasons = "; ".join(
                contour_reason(r) for r in (getattr(self._tool, "last_rig_reasons", []) or [])
            )
            self.rig_status_label.setText(f"登録できませんでした: {reasons or '不明'}")
            self._refresh_rig_controls()
            return
        info = session.proposal_summary()
        warnings = info.get("warnings") or []
        note = f" / 注意: {'; '.join(str(w) for w in warnings)}" if warnings else ""
        self._rig_preview_note = note
        self.rig_status_label.setText(contour_preview_hint(note))
        self._refresh_rig_controls()

    def _confirm_rig(self) -> None:
        if not self._tool.rig_registration_pending:
            self.rig_status_label.setText("先に「登録してプレビュー」を実行してください")
            return
        balloon_id = self._tool.confirm_rig(
            text=self.text_edit.toPlainText(),
            vertical=self.vertical_check.isChecked(),
            hide_source_layers=self.rig_hide_source_check.isChecked(),
        )
        if balloon_id:
            self._rig_pick_requested = False
            self.rig_status_label.setText("-")
            self.refresh_from_tool()
            self.refresh_dialogue()

    def _cancel_rig(self) -> None:
        self._tool.cancel_rig_registration()
        self._rig_pick_requested = False
        self.rig_status_label.setText("-")
        self._refresh_rig_controls()

    def _start_clean_dev(self) -> None:
        body_layer_id = str(self.rig_body_combo.currentData() or "")
        tail_layer_id = str(self.rig_tail_combo.currentData() or "") or None
        if not body_layer_id:
            self.rig_status_label.setText("Body レイヤーを選択してください")
            return
        context = self._tool.propose_balloonize_clean_from_layers(
            body_layer_id=body_layer_id, tail_layer_id=tail_layer_id
        )
        if context is None:
            self.rig_status_label.setText("認識できませんでした")
            return
        # Clean reuses the existing Phase BZ confirmation surface
        self._rig_pick_requested = False
        self._refresh_rig_controls()
        self._start_balloonize_from_context(context)

    def _rig_target(self) -> tuple[Any | None, str | None]:
        """(live session, committed rig balloon id) the parameter widgets
        act on: a pending registration session, else the selected rig
        balloon (its record is the authority; an edit session may or may
        not be attached yet)."""

        session = self._tool.rig_session
        if session is not None and self._tool.rig_session_balloon_id is None:
            return session, None
        state = self._tool.read_state()
        selected = state.balloons.get(self._tool.selected_balloon_id or "") if state else None
        if selected is not None and is_raster_rig(selected):
            return session, selected.balloon_id
        return None, None

    def _rig_scale_from_spin(self, value: float) -> None:
        if self._rig_syncing:
            return
        with QSignalBlocker(self.rig_scale_slider):
            self.rig_scale_slider.setValue(round(value * 100))
        self._rig_param_changed()

    def _rig_scale_from_slider(self, steps: int) -> None:
        if self._rig_syncing:
            return
        self.rig_scale_spin.setValue(steps / 100.0)  # routes through the spin box

    def _rig_param_changed(self) -> None:
        if self._rig_syncing:
            return
        session, balloon_id = self._rig_target()
        scale = float(self.rig_scale_spin.value())
        mode = str(self.rig_mode_combo.currentData() or "open_ink_fill")
        inset = float(self.rig_inset_spin.value())
        overlap = float(self.rig_overlap_spin.value())
        fidelity = self.rig_fidelity_control.value()
        tip_fidelity = self.rig_tip_fidelity_control.value()
        if balloon_id is not None:
            self._tool.set_rig_params(
                balloon_id,
                scale=scale,
                mode=mode,
                inset_px=inset,
                overlap_px=overlap,
                fidelity=fidelity if mode == "contour_rebuild" else None,
                tip_fidelity=tip_fidelity if mode == "contour_rebuild" else None,
            )
            return
        if session is None:
            return
        if abs(session.scale - scale) > 1e-9:
            session.set_scale(scale)
        if session.mode != mode:
            session.set_mode(mode)
        if mode == "contour_rebuild":
            session.set_fidelity(fidelity)
            session.set_tip_fidelity(tip_fidelity)
        if abs(session.inset_px - inset) > 1e-9:
            session.set_inset(inset)
        if session.overlap_px is None or abs(session.overlap_px - overlap) > 1e-9:
            session.set_overlap(overlap)
        self._tool.refresh_rig_proto()

    def _rig_mode_changed(self, _index: int) -> None:
        self._rig_param_changed()

    def _rig_spin_changed(self, _value: float) -> None:
        self._rig_param_changed()

    def _rig_diag_toggled(self, checked: bool) -> None:
        self.rig_diag_label.setVisible(bool(checked))
        self.refresh_rig_perf()

    def _rig_handles_toggled(self, checked: bool) -> None:
        self._tool._rig_show_handles = bool(checked)  # noqa: SLF001
        self._tool.refresh_rig_proto()

    def _refresh_rig_controls(self) -> None:
        """Show the rig group for a pending registration / a selected rig
        balloon and mirror the session or record values into the widgets."""

        session, balloon_id = self._rig_target()
        pending = session is not None and balloon_id is None
        record = None
        if balloon_id is not None:
            state = self._tool.read_state()
            record = state.balloons.get(balloon_id) if state else None
            if session is not None and self._tool.rig_session_balloon_id != balloon_id:
                session = None
        show_pick = pending or self._rig_pick_requested
        show_param = pending or record is not None
        self.rig_group.setVisible(show_pick or show_param)
        if hasattr(self, "balloon_workflow"):
            self.balloon_workflow.sections["rig"].setVisible(show_pick or show_param)
        self.rig_pick_widget.setVisible(show_pick)
        self.rig_param_widget.setVisible(show_param)
        self.rig_confirm_widget.setVisible(pending)
        if hasattr(self, "balloon_workflow"):
            self.balloon_workflow.sections["connection"].setVisible(
                not show_pick and record is None
            )
        if not show_param:
            self.rig_diag_label.setText("")
            return
        self._rig_syncing = True
        try:
            if pending and session is not None:
                scale = float(session.scale)
                mode = str(session.mode)
                fidelity = session.fidelity
                tip_fidelity = session.tip_fidelity
                inset = float(session.inset_px)
                overlap = session.effective_overlap_px()
            else:
                renderer = (record.renderer if record is not None else None) or {}
                scale = float(renderer.get("scale") or 1.0)
                mode = str(renderer.get("mode") or "open_ink_fill")
                fidelity = normalized_fidelity(renderer.get("fidelity", 0))
                tip_fidelity = normalized_fidelity(renderer.get("tip_fidelity", 0))
                inset = float(renderer.get("inset_px") or 0.0)
                overlap = renderer.get("overlap_px")
                if overlap is None and session is not None:
                    overlap = session.effective_overlap_px()
            self.rig_scale_spin.setValue(scale)
            self.rig_scale_slider.setValue(round(scale * 100))
            refresh_contour_controls(self, mode, fidelity, tip_fidelity)
            self.rig_inset_spin.setValue(inset)
            if overlap is not None:
                self.rig_overlap_spin.setValue(float(overlap))
        finally:
            self._rig_syncing = False
        self.refresh_rig_perf()

    def refresh_rig_perf(self) -> None:
        if not self.rig_diag_check.isChecked():
            self.rig_diag_label.setText("")
            return
        session = self._tool.rig_session
        if session is None:
            self.rig_diag_label.setText("")
            return
        p = session.perf_summary()
        d = session.diagnostics()
        pv = getattr(self._tool, "_rig_preview", None)
        upload = ""
        if pv is not None:
            upload = (
                f"  body_region_px={pv.last_body_region_px}  tail_bytes={pv.last_tail_bytes}"
                f"  body_full_uploads={pv.body_full_uploads}  scene_rev={pv.revision}"
            )
        perf = (
            f"preview: median {p['median_ms']:.1f}ms  p95 {p['p95_ms']:.1f}ms  "
            f"max {p['max_ms']:.1f}ms ({p['frames']} frames)\n"
            if p.get("frames")
            else ""
        )
        self.rig_diag_label.setText(
            perf + f"root_u={d['root_u']}  scale={d['scale']}  fill_inset={d['fill_inset_px']}px"
            f"  overlap={d.get('overlap_px')}px  quality={d['preview_quality']}"
            f"  last={d['last_frame_ms']}ms  p95={d['p95_ms']}ms"
            f"  rev={d['revision']}/{d['frame_revision']}  composer={d['composer_kind']}"
            f"  preview={'compositor' if pv else 'overlay'}{upload}"
        )

    def _start_balloonize_from_context(self, context) -> None:  # noqa: ANN001
        proposal = context.proposal
        index = self.bz_kind_combo.findData(proposal.kind)
        if index >= 0:
            self.bz_kind_combo.setCurrentIndex(index)
        self.bz_tail_check.setChecked(proposal.tail.confidence != "none")
        self.bz_tip_x.setValue(proposal.tail.tip[0])
        self.bz_tip_y.setValue(proposal.tail.tip[1])
        self.bz_body_w.setValue(proposal.body_rect[2])
        self.bz_body_h.setValue(proposal.body_rect[3])
        self.bz_status_label.setText("Clean 変換候補を確認してください")
        self.balloonize_group.setVisible(True)

    # ── Dialogue tab ───────────────────────────────────────────────────

    def _build_dialogue_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        refresh_btn = QPushButton("一覧を更新")
        refresh_btn.clicked.connect(self.refresh_dialogue)
        column.addWidget(refresh_btn)
        from .panel_formatting import _ts  # noqa: PLC0415

        self.dialogue_batch_button = QPushButton(_ts("Edit selected dialogues"))
        self.dialogue_batch_button.clicked.connect(lambda: edit_selected_dialogues(self))
        column.addWidget(self.dialogue_batch_button)
        self.dialogue_table = QTableWidget(0, 5)
        self.dialogue_table.setHorizontalHeaderLabels(["ページ", "コマ", "話者", "セリフ", "状態"])
        self.dialogue_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.dialogue_table.setItemDelegateForColumn(
            3, DialogueDelegate(self._tool, self.refresh_dialogue, self.dialogue_table)
        )
        self.dialogue_table.setItemDelegateForColumn(
            2, SpeakerDelegate(self._tool, self.refresh_dialogue, self.dialogue_table)
        )
        self.dialogue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.dialogue_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.dialogue_table.itemDoubleClicked.connect(self._jump_to_row)
        column.addWidget(self.dialogue_table, 1)
        hint = QLabel(editing_hint())
        self.dialogue_hint = hint
        hint.setWordWrap(True)
        column.addWidget(hint)
        return page

    def refresh_dialogue(self) -> None:
        blocker = QSignalBlocker(self.dialogue_table)
        selected = {
            self.dialogue_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.dialogue_table.selectionModel().selectedRows()
        }
        pages = getattr(self._tool.host_api, "pages", None)
        rows = build_dialogue_rows(pages) if pages is not None else []
        self._dialogue_rows = rows
        self.dialogue_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            page_label = row.page_name or f"p.{row.page_index + 1}"
            values = (
                page_label,
                row.panel_id,
                row.speaker_name or row.speaker_id,
                row.text_preview,
                "入力済み" if row.status == "filled" else "未入力",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, (row.page_id, row.balloon_id))
                if col not in (2, 3):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setToolTip(row.text)
                self.dialogue_table.setItem(index, col, item)
        self.dialogue_table.clearSelection()
        for index, row in enumerate(rows):
            if (row.page_id, row.balloon_id) in selected:
                self.dialogue_table.selectionModel().select(
                    self.dialogue_table.model().index(index, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        update_dialogue_tags(self._tool, self.dialogue_table, rows)
        del blocker

    def _jump_to_row(self, item: QTableWidgetItem) -> None:
        if item.column() in (2, 3):
            return  # Dialogue double-click opens the multiline editor in-place.
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        page_id, balloon_id = data
        self._tool.jump_to_balloon(str(page_id), str(balloon_id))
        self.tabs.setCurrentIndex(1)
        self.refresh_from_tool()

    # ── shared refresh ─────────────────────────────────────────────────

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "speaker_styles"):
            retranslate_panel(self, _TEMPLATE_LABELS)
            self.tabs.setTabText(3, speaker_styles_title())
            from .panel_formatting import _ts  # noqa: PLC0415

            self.dialogue_batch_button.setText(_ts("Edit selected dialogues"))
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "dialogue_hint"):
            self.dialogue_hint.setText(editing_hint())
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "balloon_workflow"):
            self.balloon_workflow.retranslate()
            self.rig_advanced_section.retranslate()
            self.rig_fidelity_control.retranslate()
            self.rig_tip_fidelity_control.retranslate()
            self.rig_handles_check.setText(
                handles_label(self.rig_mode_combo.currentData() == "contour_rebuild")
            )
            index = self.rig_mode_combo.findData("contour_rebuild")
            if index >= 0:
                self.rig_mode_combo.setItemText(index, contour_mode_label())
            if self._tool.rig_registration_pending:
                self.rig_status_label.setText(
                    contour_preview_hint(getattr(self, "_rig_preview_note", ""))
                )
            if not self._tool.selected_balloon_id:
                self.balloon_info_label.setText(selection_hint())

    def _on_tab_changed(self, index: int) -> None:
        self.formatting.setVisible(index in (1, 2, 3))
        self.formatting.sync()
        if index == 2:
            self.refresh_dialogue()

    def refresh_from_tool(self) -> None:
        self._updating = True
        try:
            state = self._tool.read_state()
            self.formatting.sync(state)
            selected = state.balloons.get(self._tool.selected_balloon_id or "") if state else None
            for button in self._balloon_selection_actions:
                button.setEnabled(selected is not None)
            if selected is None:
                self.balloon_info_label.setText(selection_hint())
            self.order_list.clear()
            if state is not None:
                if state.layout is not None:
                    self.gutter_spin.setValue(float(state.layout.gutter_px))
                for position, panel_id in enumerate(state.reading_order):
                    item = QListWidgetItem(f"{position + 1}. {panel_id}")
                    item.setData(Qt.ItemDataRole.UserRole, panel_id)
                    self.order_list.addItem(item)
                    if panel_id == self._tool.selected_panel_id:
                        self.order_list.setCurrentItem(item)
                balloon_id = self._tool.selected_balloon_id
                record = state.balloons.get(balloon_id or "")
                if record is not None:
                    text_obj = state.text_objects.get(record.balloon_id)
                    from .service import text_props_from_object  # noqa: PLC0415

                    props = text_props_from_object(text_obj) if text_obj is not None else None
                    self.balloon_info_label.setText(
                        f"{record.balloon_id} / コマ: {record.panel_id or '-'} / "
                        f"話者: {record.speaker_name or record.speaker_id or '-'}"
                    )
                    self.auto_size_check.setChecked(record.auto_size)
                    rig = is_raster_rig(record)
                    from .raster_parts import is_raster_preserve  # noqa: PLC0415

                    # raster balloons: body_rect is the crop placement —
                    # text-based auto sizing would detach pixels from the
                    # hit rect (unselectable balloon)
                    self.auto_size_check.setEnabled(not is_raster_preserve(record))
                    self.body_open_check.setChecked(bool(record.body_open))
                    self.tail_follow_check.setChecked(bool(record.tail_follow_tip))
                    self.tail_join_check.setChecked(bool(record.tail_auto_join))
                    self.tail_gap_spin.setValue(float(record.tail_base_gap))
                    self.tail_gap_spin.setEnabled(record.tail_base_params is not None)
                    # legacy (Raster Preserve) tail options do nothing on a
                    # rig balloon: its connection is driven by the rig group
                    for widget in (
                        self.body_open_check,
                        self.tail_follow_check,
                        self.tail_join_check,
                    ):
                        widget.setEnabled(not rig)
                    self.speaker_id_edit.setText(record.speaker_id)
                    self.speaker_name_edit.setText(record.speaker_name)
                    if props is not None:
                        self.text_edit.setPlainText(props.text)
                        self.vertical_check.setChecked(props.vertical)
                    kind_index = self.kind_combo.findData(record.kind)
                    if kind_index >= 0:
                        self.kind_combo.setCurrentIndex(kind_index)
                else:
                    self.balloon_info_label.setText(selection_hint())
                    self.auto_size_check.setEnabled(True)
            self._refresh_rig_controls()
            self.frame_controls.refresh_edges(state)
        finally:
            self._updating = False


__all__ = ["ComicToolsPanelWidget"]
