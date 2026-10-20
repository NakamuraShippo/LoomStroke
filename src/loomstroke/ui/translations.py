"""Helpers for runtime UI retranslation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication

from .channels_translations import CHANNELS_JA
from .comic_frame_translations import FRAME_JA
from .filter_pen_translations import FILTER_PEN_JA
from .filter_translations import (
    DIRECT_FILTER_JA,
    FILTER_APPEARANCE_JA,
    FILTER_EXPORT_JA,
    FILTER_LAYER_JA,
    FILTER_PANEL_JA,
    FILTER_PANEL_PLUGIN_JA,
)
from .icc_profile_download_translations import ICC_PROFILE_DOWNLOAD_JA
from .native_cmyk_translations import NATIVE_CMYK_JA
from .native_comfy_translations import NATIVE_COMFY_JA
from .new_document_color_translations import NEW_DOCUMENT_COLOR_JA
from .painting_tool_translations import PAINTING_TOOL_JA
from .panel_locale_fallbacks import NON_JA_PANEL_FALLBACK_TRANSLATIONS
from .pen_preset_translations import PEN_PRESET_JA
from .shared_locale_fallbacks import NON_JA_SHARED_FALLBACK_TRANSLATIONS
from .text_typography_translations import TEXT_TYPOGRAPHY_JA
from .tool_locale_fallbacks import NON_JA_TOOL_FALLBACK_TRANSLATIONS

TranslatorFn = Callable[[str], str]

_JA_FALLBACK_TRANSLATIONS: dict[str, dict[str, str]] = {
    "DirectFilter": DIRECT_FILTER_JA,
    "ComicPanelFrames": FRAME_JA,
    "ChannelsPanel": CHANNELS_JA,
    "IccProfileDownloads": ICC_PROFILE_DOWNLOAD_JA,
    "NewDocumentColor": NEW_DOCUMENT_COLOR_JA,
    "NativeCmyk": NATIVE_CMYK_JA,
    "PenPresets": PEN_PRESET_JA,
    "FilterPen": FILTER_PEN_JA,
    "EffectPen": FILTER_PEN_JA,
    "NativeComfyUI": NATIVE_COMFY_JA,
    "TextTypography": TEXT_TYPOGRAPHY_JA,
    "ComicFormatting": {
        "Color tag": "色タグ",
        "Choose color": "色を選択",
        "Clear tag": "タグを解除",
        "Used by {count} text frames": "使用数: {count} テキスト枠",
        "Update style details": "書式の登録情報を更新",
        "Edit selected dialogues": "選択したセリフを一括編集",
        "Apply speaker to all rows": "全行に話者を適用",
        "Find text": "検索する文字列",
        "Replace with": "置換後の文字列",
        "Replace in all rows": "全行で置換",
        "Page": "ページ",
        "Speaker": "話者",
        "Dialogue": "セリフ",
        "Apply batch": "まとめて反映",
        "Cancel": "キャンセル",
        "The batch was not applied. A dialogue changed or a layer is locked. Reopen the editor.": "一括変更を反映できませんでした。元の内容が変更されたか、レイヤーがロックされています。編集画面を開き直してください。",
        "Could not open the selected dialogues. Refresh the list and try again.": "選択したセリフを開けませんでした。一覧を更新してやり直してください。",
        "Speaker styles": "話者書式",
        "Style name": "書式名",
        "Speaker ID": "話者ID",
        "Speaker name": "話者名",
        "Register from selected text": "選択テキストの書式を登録",
        "Update from selected text": "選択テキストの書式で更新",
        "Duplicate style": "書式を複製",
        "Delete style": "書式を削除",
        "Apply to selected balloon": "選択フキダシに適用",
        "Clear style overrides": "書式の上書きを解除",
        "Save user defaults": "ユーザー既定を保存",
        "Load user defaults": "ユーザー既定を読み込み",
        "Default for unregistered speakers": "未登録の話者に使う既定書式",
        "Edit the selected text in Font, then register or update its speaker style.": "Fontで選択テキストの書式を調整し、話者書式として登録・更新してください。",
        "Could not update speaker styles. Check the selection and locked layers.": "話者書式を更新できませんでした。選択状態とレイヤーのロックを確認してください。",
        "+ Selected text has style overrides": "+ 選択テキストに書式の上書きがあります",
        "Default font": "既定のフォント",
        "Default style": "標準",
        "Text formatting": "文字書式",
        "More formatting": "書式の詳細",
        "Font": "フォント",
        "Size (pt)": "サイズ (pt)",
        "Writing": "文字方向",
        "Vertical": "縦書き",
        "Horizontal": "横書き",
        "Line height": "行間",
        "Tracking": "字間",
        "Font style": "書体スタイル",
        "Alignment": "文字揃え",
        "Left": "左揃え",
        "Center": "中央揃え",
        "Right": "右揃え",
        "Justify": "両端揃え",
        "Column flow": "縦書きの列方向",
        "Rightward": "右方向",
        "Leftward": "左方向",
        "Wrap": "自動折り返し",
        "On": "有効",
        "Off": "無効",
        "Padding top (px)": "上余白 (px)",
        "Padding right (px)": "右余白 (px)",
        "Padding bottom (px)": "下余白 (px)",
        "Padding left (px)": "左余白 (px)",
        "Text color": "文字色・不透明度",
        "Balloon: {id}": "対象: {id}",
        "Select a balloon or a dialogue row.": "フキダシまたはセリフ一覧の行を選択してください。",
        "Could not apply formatting. Reselect the balloon and try again.": "書式を反映できませんでした。フキダシを選び直してやり直してください。",
    },
    "ComicPanelWorkflow": {
        "Body fidelity: {value}%": "本体の再現度: {value}%",
        "Tail tip fidelity: {value}%": "しっぽ先端の再現度: {value}%",
        "Retain the source tip contour only. Body and root connection are unchanged.": "元画像の先端付近の輪郭を残します。本体と付け根の接続は変更しません。",
        "Show handles (root / middle / tip)": "ハンドルを表示 (付け根 / 中間 / 先端)",
        "Show handles (root / tip)": "ハンドルを表示 (付け根 / 先端)",
        "Continuous contour preview: drag the root, middle and tip, then confirm.{note}": "輪郭を一体化してプレビュー中。付け根・中間・先端をドラッグし、「確定して変換」を押してください。{note}",
        "Source fidelity: {value}%": "元画像の再現度: {value}%",
        "0: smooth outline. 100: retain body shape and line-width variation. Joint stays smooth; texture is not preserved.": "0: 滑らかに整える / 100: 本体の形と線幅の変化を残す。接続部は滑らかさを維持。色むら・かすれは再現しません。",
        "Continuous contour (standard)": "輪郭を一体化 (標準)",
        "Continuous contour preview: drag the root and tip, then confirm.{note}": "輪郭を一体化してプレビュー中。付け根と先端をドラッグし、「確定して変換」を押してください。{note}",
        "A closed ink ring is required for contour reconstruction.": "輪郭を一体化できる、閉じた本体の線が必要です。",
        "The body ink ring is too ambiguous to reconstruct safely.": "本体の輪郭線と内部を判別できないため、一体化できません。",
        "Select a balloon on the canvas to edit it.": "編集するフキダシをキャンバスで選択してください。",
        "Selected balloon": "選択中のフキダシ",
        "Create a balloon": "フキダシを作成",
        "Hand-drawn connection / Preview": "手描きの接続・プレビュー",
        "Dialogue": "セリフと文字",
        "Speaker": "話者を設定",
        "Advanced connection settings": "接続の詳細設定",
        "Asset registration / Delete": "素材登録・削除",
        "Fine tuning / Diagnostics": "接続の微調整・診断",
    },
    "ComicDialogueEditor": {
        "Dialogue editing": "セリフ一覧の編集",
        "Could not open this dialogue. Refresh the list and try again.": "セリフを開けませんでした。一覧を更新してやり直してください。",
        "The dialogue was not updated. The page or text may have changed.": "セリフを更新できませんでした。ページや元の文章が変更された可能性があります。",
        "Double-click dialogue to edit. Enter: new line; Ctrl+Enter: apply; Esc: cancel. "
        "Double-click speaker to change its style. Double-click page or balloon to jump.": "セリフ欄をダブルクリックして編集。Enter: 改行、Ctrl+Enter: 確定、Esc: 取消。"
        "話者欄で話者書式を変更、ページ・フキダシ欄で該当箇所へ移動します。",
    },
    "MainWindow": {
        "LoomStroke v0.1.0": "LoomStroke v0.1.0",
        "&File": "ファイル(&F)",
        "&Edit": "編集(&E)",
        "&Image": "画像(&I)",
        "&View": "表示(&V)",
        "&Layer": "レイヤー(&L)",
        "&Filter": "フィルター(&F)",
        "&Tools": "ツール(&T)",
        "&Plugins": "プラグイン(&P)",
        "&Window": "ウィンドウ(&W)",
        "&Help": "ヘルプ(&H)",
        "&New…": "新規作成(&N)…",
        "&Open…": "開く(&O)…",
        "Open &Recent": "最近使ったファイルを開く(&R)",
        "&Close": "閉じる(&C)",
        "&Save": "保存(&S)",
        "Save &As…": "名前を付けて保存(&A)…",
        "&Export…": "書き出し(&E)…",
        "Export Pages…": "ページを一括書き出し…",
        "Export for Print…": "印刷用に書き出し…",
        "Export Webtoon…": "Webtoon を書き出し…",
        "Manga Preflight…": "漫画プリフライト…",
        "Font Diagnostics…": "フォント診断…",
        "Place Image…": "画像を配置…",
        "Package Project…": "プロジェクトをパッケージ…",
        "Document Mode…": "ドキュメントモード…",
        "Export Digital Comic Package…": "デジタル漫画パッケージを書き出し…",
        "E&xit": "終了(&X)",
        "&Undo": "元に戻す(&U)",
        "&Redo": "やり直し(&R)",
        # CT-05 H5c: candidate-aware Undo/Redo action presentation.
        # Whole-sentence templates (never concatenated fragments) so the
        # Japanese word order can differ from English.
        "&Undo {operation} — {page}": "{operation}を元に戻す(&U) — {page}",
        "&Redo {operation} — {page}": "{operation}をやり直す(&R) — {page}",
        "Undo {operation} on {page} ({scope})": "{page}の{operation}を元に戻す ({scope})",
        "Redo {operation} on {page} ({scope})": "{page}の{operation}をやり直す ({scope})",
        "Page {number}": "{number}ページ",
        "Page": "ページ",
        "Page Content": "ページ内容",
        "Page Structure": "ページ構成",
        "Page Operation": "ページ操作",
        "Add Page": "ページ追加",
        "Duplicate Page": "ページ複製",
        "Delete Page": "ページ削除",
        "Rename Page": "ページ名の変更",
        "Move Page": "ページ移動",
        "Book Layout": "ブック設定",
        "Page Attributes": "ページ属性",
        "Document": "ドキュメント",
        "Pen Stroke": "ペンストローク",
        "Eraser Stroke": "消しゴムストローク",
        "Paint Stroke": "ペイントストローク",
        "Selection": "選択範囲",
        "Fill": "塗りつぶし",
        "Pattern Fill": "パターン塗りつぶし",
        "Gradient": "グラデーション",
        "Move": "移動",
        "Transform": "変形",
        "Cut": "切り取り",
        "Paste": "貼り付け",
        "Merge Down": "下のレイヤーと結合",
        "Merge Visible": "表示レイヤーを結合",
        "Flatten All": "画像を統合",
        "Layer Visibility": "レイヤーの表示切替",
        "Text": "テキスト",
        "Shape": "シェイプ",
        "Cu&t": "切り取り(&T)",
        "&Copy": "コピー(&C)",
        "&Paste": "貼り付け(&P)",
        "Paste from &System Clipboard": "システムクリップボードから貼り付け(&S)",
        "Pasted from the system clipboard.": "システムクリップボードから貼り付けました。",
        "Pasted from the system clipboard as pixels (editable data was invalid).": "システムクリップボードからピクセルとして貼り付けました (編集可能データは無効でした)。",
        "The system clipboard has no image to paste.": "システムクリップボードに貼り付けられる画像がありません。",
        "The clipboard data from LoomStroke could not be read.": "LoomStroke のクリップボードデータを読めませんでした。",
        "The clipboard image is too large to paste.": "クリップボードの画像が大きすぎて貼り付けられません。",
        "The clipboard image could not be decoded.": "クリップボードの画像をデコードできませんでした。",
        "The canvas did not accept the paste.": "キャンバスが貼り付けを受け付けませんでした。",
        "No canvas is available to paste into.": "貼り付け先のキャンバスがありません。",
        "Select &All": "すべて選択(&A)",
        "Select &All Vectors": "すべてのベクターを選択",
        "&Deselect": "選択解除(&D)",
        "Canvas &Size...": "キャンバスサイズ...",
        "Preparing canvas...": "キャンバスを準備中...",
        "Flip Canvas &Horizontal": "キャンバスを左右反転(&H)",
        "Flip Canvas &Vertical": "キャンバスを上下反転(&V)",
        "Flip Canvas": "キャンバスを反転",
        "Canvas flipped horizontally": "キャンバスを左右反転しました",
        "Canvas flipped vertically": "キャンバスを上下反転しました",
        "Canvas flipped": "キャンバスを反転しました",
        "Failed to flip canvas.": "キャンバスの反転に失敗しました。",
        "Rotate Canvas 90° &CW": "キャンバスを90°回転(時計回り)(&C)",
        "Rotate Canvas 90° C&CW": "キャンバスを90°回転(反時計回り)(&W)",
        "Rotate Canvas &180°": "キャンバスを180°回転(&1)",
        "Rotate Canvas": "キャンバスを回転",
        "Canvas rotated 180°": "キャンバスを180°回転しました",
        "Canvas rotated 90° clockwise": "キャンバスを時計回りに90°回転しました",
        "Canvas rotated 90° counter-clockwise": "キャンバスを反時計回りに90°回転しました",
        "Failed to rotate canvas.": "キャンバスの回転に失敗しました。",
        "Image &Properties...": "画像の設定(&P)...",
        "Image Properties": "画像の設定",
        "Canvas service is not available.": "キャンバスサービスが利用できません。",
        "Failed to update DPI.": "DPI の更新に失敗しました。",
        "DPI updated to {dpi}": "DPI を {dpi} に更新しました",
        "Image Si&ze...": "画像解像度(&Z)...",
        "Image Size": "画像解像度",
        "New &Screentone Layer...": "新規スクリーントーンレイヤー(&S)...",
        "New Screentone Layer": "新規スクリーントーンレイヤー",
        "Screentone": "スクリーントーン",
        "Create Screentone": "スクリーントーンを作成",
        "No selection. Apply the screentone to the whole page?": (
            "選択範囲がありません。ページ全体にスクリーントーンを適用しますか?"
        ),
        "Screentone layer created": "スクリーントーンレイヤーを作成しました",
        "Failed to create the screentone layer.": "スクリーントーンレイヤーの作成に失敗しました。",
        "Failed to assign the colour profile.": "カラープロファイルの割り当てに失敗しました。",
        "Colour profile removed (treated as sRGB)": "カラープロファイルを解除しました (sRGB とみなす)",
        "Colour profile assigned: {name}": "カラープロファイルを割り当てました: {name}",
        "Export Image": "画像を書き出し",
        "PNG Image (*.png)": "PNG 画像 (*.png)",
        "JPEG Image (*.jpg *.jpeg)": "JPEG 画像 (*.jpg *.jpeg)",
        "TIFF Image (*.tif *.tiff)": "TIFF 画像 (*.tif *.tiff)",
        "WebP Image (*.webp)": "WebP 画像 (*.webp)",
        "BMP Image (*.bmp)": "BMP 画像 (*.bmp)",
        "PSD Image (*.psd)": "PSD 画像 (*.psd)",
        "SVG Image (*.svg)": "SVG 画像 (*.svg)",
        "Exported {name}": "{name} を書き出しました",
        "Export Failed": "書き出しに失敗しました",
        "Unable to export image: {path}": "画像を書き出せません: {path}",
        "Export Unavailable": "書き出しできません",
        "Canvas composite is not available.": "キャンバスの合成結果を取得できません。",
        "Failed to resize image.": "画像のリサイズに失敗しました。",
        "Image resized to {width}x{height} px at {dpi} dpi": (
            "画像を {width}x{height} px / {dpi} dpi にリサイズしました"
        ),
        "Resolution set to {dpi} dpi": "解像度を {dpi} dpi に変更しました",
        "&Preferences…": "環境設定(&P)…",
        "Zoom &In": "拡大(&I)",
        "Zoom &Out": "縮小(&O)",
        "&Reset Zoom": "ズームをリセット(&R)",
        "&Fit Spread": "見開きに合わせる(&F)",
        "Comic &Book Settings...": "漫画作品の設定(&B)...",
        "Comic Book Settings": "漫画作品の設定",
        "The book settings could not be stored in this document.": (
            "この文書には作品設定を保存できませんでした。"
        ),
        "Toggle &Grid": "グリッド表示/非表示(&G)",
        "&Panels": "パネル(&P)",
        "&Docs / Shortcuts": "ドキュメント / ショートカット(&D)",
        "Show &Log Folder": "ログフォルダーを表示(&L)",
        "Menu &Visibility Log...": "メニュー可視性ログ...",
        "&Check for Updates": "更新を確認(&C)",
        "Panel &Audit": "パネル監査(&A)",
        "&About LoomStroke": "LoomStroke について(&A)",
        "About LoomStroke": "LoomStroke について",
        "&Full Screen": "全画面表示(&F)",
        "Screen &Mode": "スクリーンモード(&M)",
        "&Reset Layout": "レイアウトをリセット(&R)",
        "Show/Hide Guides": "ガイド表示/非表示",
        "(No Recent Files)": "(最近使用したファイルはありません)",
        "(Panels are in Window menu)": "(パネルはウィンドウメニューにあります)",
        "Shortcut: {shortcut}": "ショートカット: {shortcut}",
        "Shortcut: (none)": "ショートカット: (なし)",
        "Action ID: {action_id}": "アクションID: {action_id}",
        "&Import…": "読み込み(&I)…",
        "Import": "読み込み",
        "Import Failed": "読み込みに失敗しました",
        "Could not import: {path}": "読み込めませんでした: {path}",
        "Imported {name}": "{name} を読み込みました",
        "Imported {name} ({count} layers)": "{name} を読み込みました ({count} レイヤー)",
        "&Soft Proof": "ソフトプルーフ(&S)",
        "Soft Proof": "ソフトプルーフ",
        "Choose Proof &Profile...": "プルーフ用プロファイルを選択(&P)...",
        "Choose Proof Profile": "プルーフ用プロファイルを選択",
        "ICC Profile (*.icc *.icm);;All Files (*.*)": (
            "ICC プロファイル (*.icc *.icm);;すべてのファイル (*.*)"
        ),
        "The file is not a usable ICC profile.": "このファイルは使用できる ICC プロファイルではありません。",
        "Soft proof could not be enabled for this profile.": (
            "このプロファイルではソフトプルーフを有効にできませんでした。"
        ),
        "&Flatten All": "画像を統合(&F)",
        "Selection to &Mask": "選択範囲をマスクに変換(&M)",
        "Installed AI Presets": "インストール済み AI プリセット",
        "AI Effect &Presets...": "AI エフェクトプリセット(&P)...",
        "(No presets installed)": "(インストール済みのプリセットはありません)",
        "Failed to add AI effect layer": "AI エフェクトレイヤーを追加できませんでした",
        "Layers / Pages": "レイヤー・ページ",
        "Drawing": "描画",
        "Comic": "コミック",
        "AI": "AI",
        "Other": "その他",
        "S&wap Foreground/Background Colors": "描画色と背景色を入れ替え(&W)",
        "Default Foreground/Background C&olors": "描画色と背景色を初期設定に戻す(&O)",
    },
    "BasicPen": {
        "Recontour": "Recontour",
        "RecontourSmoothness (next stroke)": "RecontourSmoothness (次のストロークから適用)",
        "Recontour could not edit this stroke. The raster stroke was preserved.": "Recontourでこの線を編集できませんでした。元のラスターストロークは保持されています。",
        "Basic Pen": "ベーシックペン",
        "Activate Basic Pen": "ベーシックペンを有効化",
        "Alternate brush binding": "代替ブラシバインド",
        "Brushes": "ブラシ",
        "Basic Pen Tool": "ベーシックペンツール",
        "Basic pen tool with pressure sensitivity": "筆圧感知に対応したベーシックペンツール",
    },
    "BasicFillBucket": {
        "Basic Fill Bucket": "ベーシック塗りつぶし",
        "Flood fill bucket tool": "塗りつぶしバケツツール",
        "Fill": "塗りつぶし",
    },
    "BasicMoveTool": {
        "Move": "移動",
        "Activate Move Tool": "移動ツールを有効化",
    },
    "BasicEraser": {
        "Basic Eraser": "ベーシック消しゴム",
        "Basic pressure-sensitive eraser tool": "筆圧感知に対応したベーシック消しゴムツール",
        "Eraser": "消しゴム",
        "Secondary eraser binding": "セカンダリ消しゴムバインド",
    },
    "NodeTabsWidget": {
        "Workflow Nodes": "ワークフローノード",
        "User Filters": "ユーザーフィルター",
        "Edit Filters": "フィルターを編集",
        'No user filters yet.\nUse "Edit Filters" to add.': (
            "ユーザーフィルターはまだありません。\n「フィルターを編集」から追加してください。"
        ),
        "User Presets": "ユーザープリセット",
        "No nodes match this category.": "このカテゴリに一致するノードがありません。",
    },
    "ShortcutsPage": {
        "Assigned only": "割り当て済みのみ",
        "Conflicts only": "競合のみ",
        "Reset filters": "フィルターをリセット",
        "Mouse Operations": "マウス操作",
        "All Shortcuts": "すべてのショートカット",
        "Clear": "クリア",
        "Host": "ホスト",
        "Tools": "ツール",
        "File": "ファイル",
        "Edit": "編集",
        "View": "表示",
        "Layer": "レイヤー",
        "Window": "ウィンドウ",
        "Plugins": "プラグイン",
        "Selection": "選択",
        "Transform": "変形",
        "Utility": "ユーティリティ",
        "Vector": "ベクター",
        "Mouse: Pan gesture": "マウス: パン操作",
        "Choose the mouse gesture used to pan the canvas.": (
            "キャンバスをパンするためのマウス操作を選択します。"
        ),
        "LoomStroke Core": "LoomStroke コア",
        "Right drag": "右ドラッグ",
        "Middle drag": "中ドラッグ",
        "Space + left drag": "Space + 左ドラッグ",
        "Alt + left drag": "Alt + 左ドラッグ",
        "Shift + left drag": "Shift + 左ドラッグ",
        "Ctrl + left drag": "Ctrl + 左ドラッグ",
        "Mouse: Context menu": "マウス: コンテキストメニュー",
        "Choose the mouse gesture used to open the canvas context menu.": (
            "キャンバスのコンテキストメニューを開くためのマウス操作を選択します。"
        ),
        "Right click": "右クリック",
        "Middle click": "中クリック",
        "Space + left click": "Space + 左クリック",
        "Alt + left click": "Alt + 左クリック",
        "Shift + left click": "Shift + 左クリック",
        "Ctrl + left click": "Ctrl + 左クリック",
        "Conflict": "競合",
        "Conflicts with: {labels}": "競合対象: {labels}",
    },
    "PreferencesDialog": {
        "OK": "OK",
        "Cancel": "キャンセル",
        "Apply": "適用",
        "SAM 3.1": "SAM 3.1",
        "SAM 3.1 runtime and one-click setup for AI Select": (
            "AI Select 用 SAM 3.1 ランタイムとワンクリックセットアップ"
        ),
    },
    "AIEffectPage": {
        "Use the OpenAI API key for GPT Image 2": ("GPT Image 2 で OpenAI API キーを使用"),
        (
            "When enabled, GPT Image 2 generation uses the OpenAI API key row "
            "instead of the dedicated GPT Image 2 key."
        ): (
            "有効にすると、GPT Image 2 の生成は専用の GPT Image 2 キーではなく、"
            "OpenAI API キー行を使用します。"
        ),
        "Using OpenAI key": "OpenAI キーを使用中",
        "OpenAI key not set": "OpenAI キー未設定",
    },
    "ImagePropertiesDialog": {
        "Image Properties": "画像の設定",
        "Canvas Size": "キャンバスサイズ",
        "Width:": "幅:",
        "Height:": "高さ:",
        "Resolution": "解像度",
        "DPI:": "DPI:",
        " dpi": " dpi",
        "Physical size:": "実寸:",
        "Colour Profile": "カラープロファイル",
        "Profile:": "プロファイル:",
        "Load ICC Profile...": "ICC プロファイルを読み込む...",
        "Load ICC Profile": "ICC プロファイルを読み込む",
        "ICC Profiles (*.icc *.icm);;All Files (*.*)": "ICC プロファイル (*.icc *.icm);;すべてのファイル (*.*)",
        "The file is not a valid ICC profile.": "有効な ICC プロファイルではありません。",
        "Untagged (treated as sRGB)": "プロファイルなし (sRGB とみなす)",
        "sRGB (built-in)": "sRGB (内蔵)",
        "{name} (document)": "{name} (ドキュメント)",
        "{name} (file)": "{name} (ファイル)",
        "OK": "OK",
        "Cancel": "キャンセル",
    },
    "ExportImageDialog": {
        "Export Image": "画像を書き出し",
        "Format": "形式",
        "Format:": "形式:",
        "Image:": "画像:",
        "Supports:": "対応:",
        "PNG": "PNG",
        "JPEG": "JPEG",
        "TIFF": "TIFF",
        "WebP": "WebP",
        "BMP": "BMP",
        "PSD (layered)": "PSD (レイヤー付き)",
        "SVG (vector)": "SVG (ベクター)",
        "{width} x {height} px, {dpi} dpi ({mm_w} x {mm_h} mm)": (
            "{width} x {height} px, {dpi} dpi ({mm_w} x {mm_h} mm)"
        ),
        "alpha: {alpha} / ICC: {icc} / DPI: {dpi}": "透明度: {alpha} / ICC: {icc} / DPI: {dpi}",
        "yes": "可",
        "no": "不可",
        "Colour Profile": "カラープロファイル",
        "Profile:": "プロファイル:",
        "Embed document profile ({name})": "ドキュメントのプロファイルを埋め込む ({name})",
        "Embed document profile (none - exported untagged)": (
            "ドキュメントのプロファイルを埋め込む (なし - プロファイルなしで書き出し)"
        ),
        "Untagged (no profile)": "プロファイルなし",
        "Convert to sRGB and embed": "sRGB に変換して埋め込む",
        "Transparency": "透明度",
        "Preserve transparency": "透明度を保持",
        "Flatten onto a colour": "色で塗りつぶして統合",
        "Matte colour": "背景色",
        "Format Options": "形式オプション",
        "Quality:": "品質:",
        "Progressive": "プログレッシブ",
        "Compression level:": "圧縮レベル:",
        "Compression:": "圧縮:",
        "LZW": "LZW",
        "Deflate (ZIP)": "Deflate (ZIP)",
        "PackBits": "PackBits",
        "None": "なし",
        "Lossless": "可逆",
        "No options for this format.": "この形式にオプションはありません。",
        "Included Content": "書き出し内容",
        "Exports the visible layer composite of the active page. Guides, the selection outline and tool overlays are never included.": (
            "アクティブページの表示レイヤー合成結果を書き出します。ガイド・選択範囲の枠線・ツールのオーバーレイは含まれません。"
        ),
        "Warnings": "警告",
        "No warnings.": "警告はありません。",
        "Transparency will be flattened onto the background.": "透明部分は背景色で塗りつぶされます。",
        "This format cannot embed an ICC profile.": "この形式は ICC プロファイルを埋め込めません。",
        "This format does not store DPI metadata.": "この形式は DPI メタデータを保存しません。",
        "The document has no ICC profile; the file is exported untagged (treated as sRGB).": (
            "ドキュメントに ICC プロファイルがないため、プロファイルなし (sRGB とみなす) で書き出します。"
        ),
        "The document has no ICC profile; pixels are assumed to be sRGB.": (
            "ドキュメントに ICC プロファイルがないため、ピクセルを sRGB とみなして変換します。"
        ),
        "Layered formats use their own exporter; colour and alpha options do not apply.": (
            "レイヤー付き形式は専用の書き出し経路を使うため、カラー/透明度オプションは適用されません。"
        ),
        "Export...": "書き出し...",
        "Cancel": "キャンセル",
    },
    "ImageSizeDialog": {
        "Image Size": "画像解像度",
        "Pixel Dimensions": "ピクセル寸法",
        "Physical Size": "実寸",
        "Resolution": "解像度",
        "Options": "オプション",
        "Width:": "幅:",
        "Height:": "高さ:",
        "Scale:": "拡大率:",
        "Unit:": "単位:",
        "DPI:": "DPI:",
        "Interpolation:": "補間方法:",
        " px": " px",
        " %": " %",
        " dpi": " dpi",
        "mm": "mm",
        "inch": "インチ",
        "Constrain proportions": "縦横比を固定",
        "Resample image": "画像を再サンプル",
        "Nearest Neighbor": "ニアレストネイバー",
        "Bilinear": "バイリニア",
        "Bicubic (recommended)": "バイキュービック (推奨)",
        "Lanczos (best for reduction)": "Lanczos (縮小に最適)",
        "Hamming": "ハミング",
        "Box (integer reduction)": "ボックス (整数倍縮小)",
        "Current: {width} x {height} px at {dpi} dpi": "現在: {width} x {height} px / {dpi} dpi",
        "Estimated undo memory: {megabytes} MB": "取り消し用メモリの見積もり: {megabytes} MB",
        "OK": "OK",
        "Cancel": "キャンセル",
    },
    "InterfacePage": {
        "Dark": "ダーク",
        "Light": "ライト",
        "System": "システム",
        "Allow external AI services (ComfyUI, LLM Gateway, NanoBanana)": (
            "外部AIサービス (ComfyUI, LLM Gateway, NanoBanana) を許可"
        ),
        "When disabled, network access to ComfyUI, LLM Gateway, and NanoBanana is blocked.": (
            "無効にすると、ComfyUI・LLM Gateway・NanoBanana へのネットワークアクセスを遮断します。"
        ),
    },
    "LoomStrokeMcpPage": {
        "Skip additional confirmation dialogs": "追加の確認ダイアログを省略する",
        (
            "Only active when Agent Approval is set to Allow all MCP agent actions. "
            "File roots, external-service permissions, and connection authentication still apply."
        ): (
            "Agent Approval を全許可にした場合のみ有効です。"
            "ファイルの許可フォルダー、外部サービスの許可、接続認証は引き続き適用されます。"
        ),
        "Agent Approval": "エージェント操作の承認",
        "Allow all MCP agent actions": "MCPエージェント操作をすべて許可",
        "Ask only before external generation queues": "外部生成の実行前のみ確認",
        "Ask before every MCP agent action": "MCPエージェント操作のたびに確認",
        "Refresh Session Token": "セッショントークンを更新",
        (
            "Refresh the MCP session token?\n\n"
            "After refreshing the token, any agents registered to this LoomStroke "
            "MCP server must update their connection settings before they can connect again."
        ): (
            "MCP セッショントークンを更新しますか?\n\n"
            "トークンを更新すると、この LoomStroke MCP サーバーを登録しているエージェント側で"
            "接続設定を更新するまで再接続できなくなります。"
        ),
    },
    "Sam3RuntimePage": {
        "SAM 3.1 root folder": "SAM 3.1 ルートフォルダー",
        "Select SAM 3.1 root folder": "SAM 3.1 ルートフォルダーを選択",
        "Start SAM 3.1 server automatically": "SAM 3.1 サーバーを自動起動",
        "Status": "状態",
        "Status error": "状態取得エラー",
        "Setup": "セットアップ",
        "Starting setup...": "セットアップを開始しています...",
        "Cancel": "キャンセル",
        "Start": "起動",
        "Stop": "停止",
        "Restart": "再起動",
        "Load Model": "モデルをロード",
        "Unload Model": "モデルをアンロード",
        "Runtime": "ランタイム",
        "Worker": "ワーカー",
        "Model": "モデル",
        "Checkpoint": "チェックポイント",
        "Point mode": "ポイントモード",
        "Ready": "準備完了",
        "SAM 3.1 Ready": "SAM 3.1 準備完了",
        "missing": "未検出",
        "Asset cache limit": "アセットキャッシュ上限",
        "Browse...": "参照...",
        " MB": " MB",
    },
    "TextFontPanel": {
        "Text Font Panel": "テキストフォントパネル",
        "Font and typography settings for Text Tool": "テキストツールのフォントと文字設定",
        "Font": "フォント",
        "Word Wrap": "折り返し",
        "Padding": "余白",
        "Vertical Writing": "縦書き",
        "Left": "左",
        "Center": "中央",
        "Right": "右",
        "Top": "上",
        "Bottom": "下",
        "Justify": "両端揃え",
        "Point Text": "ポイントテキスト",
        "Area Text": "エリアテキスト",
        "Leftward": "左方向",
        "Rightward": "右方向",
        "Text Mode": "テキストモード",
        "Font Family": "フォントファミリー",
        "Font Style": "フォントスタイル",
        "Font Size (pt)": "フォントサイズ (pt)",
        "Paragraph Flow": "段落フロー",
        "Alignment": "配置",
        "Line Height": "行間",
        "Char Spacing": "文字詰め",
        "Tracking": "文字間",
        "Color": "色",
        "Opacity": "不透明度",
        "Custom Font Folders": "カスタムフォントフォルダー",
        "Add Folder": "フォルダーを追加",
        "Remove": "削除",
        "Reload Fonts": "フォントを再読み込み",
        "Removed custom font folder.": "カスタムフォントフォルダーを削除しました。",
        "Loaded fonts: {loaded}, failed: {failed}": "読み込んだフォント: {loaded}, 失敗: {failed}",
        "No custom fonts loaded.": "カスタムフォントは読み込まれていません。",
        "Vertical Text tool keeps output in vertical writing.": (
            "縦書きツールは出力を縦書きに維持します。"
        ),
        "Text tool keeps output in horizontal writing.": (
            "テキストツールは出力を横書きに維持します。"
        ),
        "Paragraph Flow is available in Vertical Text tool.": (
            "段落フローは縦書きツールでのみ利用できます。"
        ),
        "Select Font Folder": "フォルダーを選択",
    },
    "TextTool": {
        "Text Tool": "テキストツール",
        "Word Wrap": "折り返し",
        "Padding Top": "上の余白",
        "Padding Right": "右の余白",
        "Padding Bottom": "下の余白",
        "Padding Left": "左の余白",
        "Rotate Latin/Digits": "英字・数字を回転",
        "Canvas text object placement and editing tool": (
            "テキストオブジェクトをキャンバス上で配置・編集するためのツール"
        ),
        "Text": "テキスト",
        "Activate Text Tool": "テキストツールを有効化",
        "Vertical Text": "縦書きテキスト",
    },
    "VectorPathTool": {
        "Path": "パス",
        "Vector": "ベクター",
    },
    "VectorPathEditTool": {
        "Path Edit": "パス編集",
        "Vector": "ベクター",
    },
    "VectorPreciseTool": {
        "Vector Pen (Precise)": "ベクターペン (高精度)",
        "Precise vector pen": "高精度ベクターペン",
        "Vector": "ベクター",
    },
    "BasicSelectionRectTool": {
        "Selection (Rect)": "選択 (矩形)",
    },
    "ShapeManagerPlugin": {
        "Shape Manager": "シェイプマネージャー",
        "Manage manga shapes, balloons, and frames.": "マンガの図形・フキダシ・コマを管理します。",
    },
    "ShortcutEditDialog": {
        "Change Shortcut - {label}": "ショートカットを変更 - {label}",
        "Press the new shortcut for <b>{label}</b>.": (
            "<b>{label}</b> の新しいショートカットを入力してください。"
        ),
    },
    "MouseBindingEditDialog": {
        "Change Binding - {label}": "バインドを変更 - {label}",
        "Select the new binding for <b>{label}</b>.": (
            "<b>{label}</b> の新しいバインドを選択してください。"
        ),
    },
    "ColorToolPlugin": {
        "Color": "色",
        "Eyedropper": "スポイト",
        "Pick color from canvas": "キャンバスから色を取得",
        "Alternate color binding": "代替カラーバインド",
        "Enable Eyedropper": "スポイトを有効化",
        "Match Reference": "リファレンスに合わせる",
        "Palette Only": "パレットのみ",
    },
    "MagicWand": {
        "Magic Wand": "マジックワンド",
        "Select contiguous colour regions": "隣接するカラー領域を選択",
        "Selection": "選択",
        "Tolerance": "許容量",
        "Contiguous": "隣接",
        "Operation": "操作",
        "Replace": "置換",
        "Add": "追加",
        "Subtract": "除去",
        "Intersect": "共通部分",
    },
    "BasicGradientPlugin": {
        "Basic Gradient": "ベーシックグラデーション",
        "Linear and radial gradient fill tool": "線形・放射状グラデーション塗りつぶしツール",
        "Gradient": "グラデーション",
        "Linear": "線形",
        "Radial": "放射状",
        "Type": "タイプ",
        "Start Color": "開始色",
        "End Color": "終了色",
        "Middle Stops": "中間ストップ",
        "Dither": "ディザー",
        "Fill": "塗りつぶし",
        "Gradient Stop Color": "グラデーションストップ色",
    },
    "EffectPanel": {
        "No effect selected": "エフェクトが選択されていません",
        "Could not auto-connect to ComfyUI.": "ComfyUI に自動接続できませんでした。",
        "Retry": "再試行",
        "Dismiss": "閉じる",
        "(Unnamed preset)": "(名称未設定のプリセット)",
        "Parameters": "パラメーター",
        "Input slots": "入力スロット",
        " (required)": " (必須)",
        "(unbound)": "(未接続)",
        "Clear binding": "バインドを解除",
        "Trigger": "トリガー",
        "Server": "サーバー",
        "Choose a ComfyUI server configured in Preferences.": (
            "Preferences で設定した ComfyUI サーバーを選択します。"
        ),
        "manual": "手動",
        "on_change": "変更時",
        "debounced": "遅延実行",
        "Output to this layer (override)": "このレイヤーへ出力 (上書き)",
        (
            "When checked, every generated output is written back into this "
            "AI Effect layer instead of creating a new layer above it. "
            "Input slot bindings are disabled while this is active."
        ): (
            "有効にすると、生成結果は上に新規レイヤーを作らず、この AI Effect "
            "レイヤーへ書き戻されます。有効中は入力スロットのバインドは無効になります。"
        ),
        "Gaussian Blur": "ガウスぼかし",
        "Radius": "半径",
        "Sharpen": "シャープ",
        "Amount": "量",
        "Threshold": "しきい値",
        "Levels": "レベル補正",
        "Input Black": "入力(黒)",
        "Input White": "入力(白)",
        "Gamma": "ガンマ",
        "Output Black": "出力(黒)",
        "Output White": "出力(白)",
        "Hue/Saturation": "色相/彩度",
        "Hue": "色相",
        "Saturation": "彩度",
        "Lightness": "明度",
        "Noise Reduction": "ノイズ除去",
        "Strength": "強度",
        "Color Strength": "カラー強度",
        "Invert": "階調の反転",
        "Mosaic": "モザイク",
        "Cell Size": "セルの大きさ",
        "Manga Screentone": "漫画スクリーントーン",
        "Pattern": "パターン",
        "Frequency (LPI)": "線数 (LPI)",
        "Density (%)": "濃度 (%)",
        "Angle": "角度",
        "Dot Aspect": "網点の縦横比",
        "Origin": "原点",
        "Seed": "シード",
        "Preview": "プレビュー",
        "Round": "丸",
        "Ellipse": "楕円",
        "Line": "線",
        "Cross": "クロス",
        "Stochastic": "ランダム (FM)",
        "Page": "ページ",
        "Local": "レイヤー",
        "Custom": "カスタム",
        "Accurate": "正確",
        "Moire Safe": "モアレ抑制",
        "Solid Fallback": "ベタ (簡易)",
    },
    "PresetManagerWindow": {
        "AI Effects Manager": "AI Effects Manager",
        "Search presets by name / tag / author...": "名前 / タグ / 作者でプリセットを検索...",
        "Import": "インポート",
        "New": "新規",
        "Grid view": "グリッド表示",
        "List view": "リスト表示",
        "Small cards": "小カード",
        "All Presets": "すべてのプリセット",
        "Sort:": "並び替え:",
        "Recently used": "最近使用",
        "Name (A-Z)": "名前 (A-Z)",
        "Category": "カテゴリ",
        "Created": "作成日",
        "%d items": "%d 件",
        "%(n)d of %(total)d": "%(total)d 件中 %(n)d 件",
        "Add category": "カテゴリを追加",
        "Category name:": "カテゴリ名:",
        "All": "すべて",
        "Gateway": "Gateway",
        "Source": "ソース",
        "Add source": "ソースを追加",
        "Source name:": "ソース名:",
        "Built-in": "組み込み",
        "User": "ユーザー",
        "Imported": "インポート済み",
        "Add AI Effect Layer": "AI Effect レイヤーを追加",
        "Edit...": "編集...",
        "Delete...": "削除...",
        "Remove from sidebar": "サイドバーから削除",
        "Change icon": "アイコンを変更",
        "Change Gateway icon": "Gateway アイコンを変更",
        "Gateway icon (empty resets to default):": "Gateway アイコン (空なら既定に戻す):",
        "Changed Gateway icon for '%s'.": "'%s' の Gateway アイコンを変更しました。",
        "Reset Gateway icon for '%s'.": "'%s' の Gateway アイコンを既定に戻しました。",
        "Could not save sidebar settings: %s": "サイドバー設定を保存できませんでした: %s",
        "Category '%s' already exists.": "カテゴリ '%s' はすでに存在します。",
        "Added category '%s'.": "カテゴリ '%s' を追加しました。",
        "Source '%s' already exists.": "ソース '%s' はすでに存在します。",
        "Added source '%s'.": "ソース '%s' を追加しました。",
        "Delete category": "カテゴリを削除",
        "Remove category '%s' from the sidebar? Presets are not deleted.": (
            "カテゴリ '%s' をサイドバーから削除しますか? プリセットは削除されません。"
        ),
        "Removed category '%s'.": "カテゴリ '%s' を削除しました。",
        "Delete source": "ソースを削除",
        "Remove source '%s' from the sidebar? Presets are not deleted.": (
            "ソース '%s' をサイドバーから削除しますか? プリセットは削除されません。"
        ),
        "Removed source '%s'.": "ソース '%s' を削除しました。",
        "Edit: Effect Editor is not wired in this session.": (
            "編集: このセッションでは Effect Editor が接続されていません。"
        ),
        "Delete preset": "プリセットを削除",
        "Delete '%s'? This cannot be undone.": "'%s' を削除しますか? この操作は元に戻せません。",
        "Delete failed: %s": "削除に失敗しました: %s",
        "Cannot delete '%s' (builtin or missing).": (
            "'%s' は削除できません (組み込み、または見つかりません)。"
        ),
        "Deleted '%s'": "'%s' を削除しました",
        "Import preset": "プリセットをインポート",
        "LoomStroke effect (*.lsfx);;All files (*)": (
            "LoomStroke effect (*.lsfx);;すべてのファイル (*)"
        ),
        "Import failed: %s": "インポートに失敗しました: %s",
    },
    "AIEffectManagerPanel": {
        "Manager": "マネージャー",
        "Editor": "エディター",
    },
    "AIEffectOnboarding": {
        "AI Effect — no gateway": "AI Effect - Gateway なし",
        "No AI backend is configured.": "AI バックエンドが設定されていません。",
        (
            "Open Preferences → AI Effect and register a backend (e.g. a running ComfyUI "
            "instance) before generating."
        ): (
            "生成前に Preferences → AI Effect を開き、バックエンド "
            "(例: 起動中の ComfyUI インスタンス) を登録してください。"
        ),
        "AI Effect — no presets": "AI Effect - プリセットなし",
        "No AI Effect presets are installed.": "AI Effect プリセットがインストールされていません。",
        "Drop a .lsfx file into the preset folder, then restart the panel to pick it up.": (
            ".lsfx ファイルをプリセットフォルダーに入れてから、"
            "パネルを再起動して読み込んでください。"
        ),
        "Open preset folder": "プリセットフォルダーを開く",
        "AI Effect": "AI Effect",
        "Your first AI Effect generation completed.": "初回の AI Effect 生成が完了しました。",
        (
            "Tip: the Preferences → AI Effect tab controls retries, cache size and the "
            "default trigger."
        ): (
            "ヒント: Preferences → AI Effect タブで、再試行、キャッシュサイズ、"
            "既定トリガーを設定できます。"
        ),
    },
    "EffectEditorWindow": {
        "AI Effect — Preset Editor": "AI Effect - プリセットエディター",
        "Gateway": "Gateway",
        "Server": "サーバー",
        "Choose which Preferences-configured ComfyUI server runs this preset.": (
            "このプリセットを実行する Preferences 登録済み ComfyUI サーバーを選択します。"
        ),
        "Register to Manager": "Manager に登録",
        "Preset library unavailable — use Export preset... instead": (
            "プリセットライブラリを利用できません - 代わりに Export preset... を使ってください"
        ),
        "Export preset...": "プリセットを書き出し...",
        "Workflow: (no workflow loaded)": "Workflow: (workflow 未読み込み)",
        "(no workflow loaded)": "(workflow 未読み込み)",
        "&File": "ファイル(&F)",
        "Load workflow...": "workflow を読み込み...",
        "Import preset...": "プリセットをインポート...",
        "Close": "閉じる",
        "Import failed: gateway %s is not registered": (
            "インポート失敗: gateway %s は登録されていません"
        ),
        "Import failed: detect candidates error": "インポート失敗: 候補検出エラー",
        "Preset: %s": "プリセット: %s",
        "Imported: %s (gateway: %s)": "インポートしました: %s (gateway: %s)",
        "Imported: %s": "インポートしました: %s",
        "Load workflow": "workflow を読み込み",
        "Workflow files (*.json);;All files (*)": "Workflow files (*.json);;すべてのファイル (*)",
        "Import preset": "プリセットをインポート",
        "LoomStroke effect (*.lsfx);;All files (*)": (
            "LoomStroke effect (*.lsfx);;すべてのファイル (*)"
        ),
        "Detect candidates failed (see log)": "候補検出に失敗しました (ログを確認してください)",
        (
            "Connect ComfyUI to apply backend metadata (parameter constraints will load "
            "on next workflow open)."
        ): (
            "backend metadata を適用するには ComfyUI に接続してください "
            "(parameter 制約は次回 workflow を開いたときに読み込まれます)。"
        ),
        "Fetching backend metadata...": "backend metadata を取得中...",
        "Backend metadata fetch failed": "backend metadata の取得に失敗しました",
        "Backend returned no metadata; using defaults": (
            "backend から metadata が返らなかったため、既定値を使います"
        ),
        "Backend metadata applied: %d parameter(s) constrained": (
            "backend metadata を適用しました: %d 個の parameter に制約を設定"
        ),
        "Export failed: no workflow loaded": "書き出し失敗: workflow が読み込まれていません",
        "Export preset": "プリセットを書き出し",
        "Exported: %s": "書き出しました: %s",
        "Register: preset library unavailable in this session": (
            "登録: このセッションではプリセットライブラリを利用できません"
        ),
        "Register failed: no workflow loaded": "登録失敗: workflow が読み込まれていません",
        "Register failed: %s": "登録失敗: %s",
        "Register failed: file missing after write (%s)": (
            "登録失敗: 書き込み後にファイルが見つかりません (%s)"
        ),
        "Registered: %s": "登録しました: %s",
        "Preset name is required.": "プリセット名は必須です。",
        "At least one output must be enabled.": "少なくとも 1 つの output を有効にしてください。",
        "Exported manifest failed validation (internal): %s": (
            "書き出した manifest の検証に失敗しました (内部): %s"
        ),
        "Export failed: %s": "書き出し失敗: %s",
        "Import failed: preset has no embedded workflow.": (
            "インポート失敗: プリセットに埋め込み workflow がありません。"
        ),
        "Import failed: manifest invalid (%s)": "インポート失敗: manifest が不正です (%s)",
        "Import failed: gateway file missing (%s)": (
            "インポート失敗: gateway ファイルが見つかりません (%s)"
        ),
        "Import failed: %s": "インポート失敗: %s",
        "select a Gateway": "Gateway を選択",
        "load a workflow": "workflow を読み込み",
        "enter a preset name": "プリセット名を入力",
        "tick at least one output": "少なくとも 1 つの output にチェック",
        "Export the configured preset as a .lsfx file.": (
            "設定したプリセットを .lsfx ファイルとして書き出します。"
        ),
        "Export is disabled: %s.": "書き出しは無効です: %s。",
        "Save this preset directly into the AI Effects Manager (no file dialog).": (
            "このプリセットを AI Effects Manager に直接保存します (ファイルダイアログなし)。"
        ),
        "Register is disabled: %s.": "登録は無効です: %s。",
        "Loaded: %s": "読み込みました: %s",
        "Workflow: %s": "Workflow: %s",
        "Load failed: %s": "読み込み失敗: %s",
    },
    "MetadataForm": {
        "Required": "必須",
        "Optional preset description": "任意のプリセット説明",
        "comma-separated": "カンマ区切り",
        "Choose icon...": "アイコンを選択...",
        "(none)": "(なし)",
        "Choose preview...": "プレビューを選択...",
        "Name": "名前",
        "Category": "カテゴリ",
        "Description": "説明",
        "Tags": "タグ",
        "Trigger": "トリガー",
        "Icon": "アイコン",
        "Preview": "プレビュー",
        "(icon set)": "(アイコン設定済み)",
        "(imported preview)": "(インポート済みプレビュー)",
        "(preview set)": "(プレビュー設定済み)",
        "Choose icon": "アイコンを選択",
        "Image files (*.png *.jpg *.jpeg);;All files (*)": (
            "画像ファイル (*.png *.jpg *.jpeg);;すべてのファイル (*)"
        ),
        "(read error)": "(読み込みエラー)",
        "(too large, max %d MiB)": "(大きすぎます、最大 %d MiB)",
        "Choose preview": "プレビューを選択",
    },
    "ChoiceInspector": {
        "Inspector": "インスペクター",
        "Select a candidate on the left.": "左側で候補を選択してください。",
        "Required": "必須",
        "Internal id": "内部 ID",
        "Display name": "表示名",
        "Order": "順序",
        "Required text": "必須テキスト",
        "comma-separated (enum only)": "カンマ区切り (enum のみ)",
        "Type": "種類",
        "Default": "既定値",
        "Group": "グループ",
        "Range min": "範囲 min",
        "Range max": "範囲 max",
        "Range step": "範囲 step",
        "Choices": "選択肢",
        "Overwrite on regenerate": "再生成時に上書き",
        "Mode": "モード",
    },
    "CandidatesPane": {
        "Input slots": "入力スロット",
        "Parameters": "パラメーター",
        "Outputs": "出力",
    },
    "TransformPanel": {
        "Transform": "変形",
        "Angle": "角度",
        "No selection": "選択なし",
        "Raster Layer": "ラスターレイヤー",
        "Text Object": "テキストオブジェクト",
        "Vector Object": "ベクターオブジェクト",
    },
    "LayerPanel": {
        "Pass Through": "パススルー",
        "Normal": "通常",
        "Dissolve": "ディザ合成",
        "Darken": "比較(暗)",
        "Multiply": "乗算",
        "Color Burn": "焼き込みカラー",
        "Linear Burn": "焼き込み(リニア)",
        "Darker Color": "カラー比較(暗)",
        "Lighten": "比較(明)",
        "Screen": "スクリーン",
        "Color Dodge": "覆い焼きカラー",
        "Linear Dodge (Add)": "覆い焼き(リニア)/加算",
        "Lighter Color": "カラー比較(明)",
        "Overlay": "オーバーレイ",
        "Soft Light": "ソフトライト",
        "Hard Light": "ハードライト",
        "Vivid Light": "ビビッドライト",
        "Linear Light": "リニアライト",
        "Pin Light": "ピンライト",
        "Hard Mix": "ハードミックス",
        "Difference": "差の絶対値",
        "Exclusion": "除外",
        "Subtract": "減算",
        "Divide": "除算",
        "Hue": "色相",
        "Saturation": "彩度",
        "Color": "カラー",
        "Luminosity": "輝度",
        "Blend Mode": "描画モード",
        "Opacity": "不透明度",
        "Toggle layer lock": "レイヤーのロック切替",
        "Toggle alpha lock": "透明度ロック切替",
        "Toggle layer visibility": "レイヤーの表示切替",
        "Layer blend mode": "レイヤー描画モード",
        "Layer opacity": "レイヤー不透明度",
        "Group": "グループ",
        "Rename": "名前変更",
        "Group Layers": "レイヤーをグループ化",
        "Ungroup": "グループ解除",
        "Add Layer Mask": "レイヤーマスクを追加",
        "Create SAM3 Mask from Layer": "レイヤーに SAM3 マスクを作成",
        "Replace Layer Mask with Candidate": "レイヤーマスクを候補で置換",
        "Add Candidate to Existing Mask": "既存マスクに候補を追加",
        "Toggle Candidate Overlay": "候補オーバーレイを切り替え",
        "Rename Layer": "レイヤー名の変更",
    },
}


def _panel_and_layer_menu_fallbacks(
    *,
    brush_manager: str,
    layers: str,
    preview: str,
    toolbar: str,
    pages: str,
    color: str,
    font: str,
    shape_manager: str,
    prompt_editor: str,
    nano_banana: str,
    comfyui: str,
    gallery: str,
    new_layer: str,
    duplicate: str,
    delete: str,
    merge_down: str,
    merge_visible: str,
    group_layers: str,
    arrange: str,
    bring_to_front: str,
    bring_forward: str,
    send_backward: str,
    send_to_back: str,
    rasterize: str,
    last_filter: str,
    not_implemented: str,
) -> dict[str, dict[str, str]]:
    return {
        "MainWindow": {
            "Brush Manager": brush_manager,
            "Layers": layers,
            "Preview": preview,
            "Toolbar": toolbar,
            "Pages": pages,
            "Color": color,
            "Font": font,
            "Shape Manager": shape_manager,
            "Prompt Editor": prompt_editor,
            "NanoBanana": nano_banana,
            "ComfyUI": comfyui,
            "Gallery": gallery,
            "&New Layer": new_layer,
            "&Duplicate": duplicate,
            "&Delete": delete,
            "&Merge Down": merge_down,
            "Merge &Visible": merge_visible,
            "&Group Layers": group_layers,
            "&Arrange": arrange,
            "Bring to &Front": bring_to_front,
            "Bring &Forward": bring_forward,
            "Send &Backward": send_backward,
            "Send to &Back": send_to_back,
            "&Rasterize": rasterize,
            "&Last Filter": last_filter,
            "Not implemented": not_implemented,
        },
        "BasicPen": {
            "Brush Manager": brush_manager,
        },
        "TextFontPanel": {
            "Font": font,
        },
        "ShapeManagerPlugin": {
            "Shape Manager": shape_manager,
        },
    }


_JA_FALLBACK_TRANSLATIONS["MainWindow"].update(
    _panel_and_layer_menu_fallbacks(
        brush_manager="ブラシマネージャー",
        layers="レイヤー",
        preview="プレビュー",
        toolbar="ツールバー",
        pages="ページ",
        color="色",
        font="フォント",
        shape_manager="シェイプマネージャー",
        prompt_editor="プロンプトエディター",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="ギャラリー",
        new_layer="新規レイヤー",
        duplicate="複製",
        delete="削除",
        merge_down="下のレイヤーと結合",
        merge_visible="表示レイヤーを結合",
        group_layers="レイヤーをグループ化",
        arrange="重ね順",
        bring_to_front="最前面へ",
        bring_forward="前面へ",
        send_backward="背面へ",
        send_to_back="最背面へ",
        rasterize="ラスタライズ",
        last_filter="最後のフィルター",
        not_implemented="未実装",
    )["MainWindow"]
)
_JA_FALLBACK_TRANSLATIONS["MainWindow"].update(
    {
        "Production Control": "Production Control",
        "Production Control(incomplete)": "Production Control(incomplete)",
        "Canvas Assistant(incomplete)": "Canvas Assistant(incomplete)",
    }
)
_JA_FALLBACK_TRANSLATIONS["MainWindow"].update(
    {
        "Saved previous document {name}": "以前のドキュメント {name} を保存しました",
        "Saved {name} — newer unsaved changes remain": (
            "{name} を保存しました — より新しい未保存の変更が残っています"
        ),
        "Saved {name} — newer unsaved changes remain, close cancelled": (
            "{name} を保存しました — より新しい未保存の変更が残っているため、終了を中止しました"
        ),
    }
)
_JA_FALLBACK_TRANSLATIONS["BasicPen"].update({"Brush Manager": "ブラシマネージャー"})
_JA_FALLBACK_TRANSLATIONS["SAM3AISelect"] = {
    "Release SAM Memory": "SAMメモリを解放",
    "Unload the SAM 3.1 model. It reloads automatically on next use.": "SAM 3.1のモデルをメモリから解放します。次回使用時に自動で再ロードします。",
    "Waiting for SAM, then releasing memory...": "SAMの処理終了を待ってメモリを解放しています…",
    "Model memory released. Reloads on next use.": "モデルのメモリを解放しました。次回使用時に再ロードします。",
    "Could not release model memory ({error}).": "モデルのメモリを解放できませんでした ({error})。",
    "Use Selection as Inpaint Mask": "選択範囲をインペイントマスクに",
    "Preview Selection Inpaint": "選択インペイントをプレビュー生成",
    "Replace": "置換",
    "Add": "追加",
    "Subtract": "減算",
    "Intersect": "交差",
    "Apply as Add": "追加で適用",
    "Apply as Subtract": "減算で適用",
    "Apply as Intersect": "交差で適用",
    "Protect": "保護",
    "Protect Subject (BG Mask)": "被写体を保護 (背景マスク)",
    "Preview submit": "プレビュー送信",
    "AI Refine...": "AIで再調整...",
    "Refine Mask...": "マスクを調整...",
    "Refine Mask": "マスクを調整",
    "Min Candidate Score": "候補スコアの下限",
    "Detection Threshold": "検出しきい値",
    "Split Disconnected Instances": "分離したオブジェクトを分割",
    "Show All Instances": "全候補を表示 (オフ = 最良 1 件)",
    "Auto Mask Cleanup": "マスクを自動クリーンアップ",
    "Combine Mode": "合成モード",
    "Combine Selected": "選択候補を合成",
    "Layer Group": "レイヤーグループ化",
    "Apply on Candidate Click": "候補クリックで選択範囲に適用",
    "AI Refine running...": "AI Refine 処理中...",
    "AI Refine finished": "AI Refine 完了",
    "AI Refine blocked": "AI Refine 中断",
    "AI Select running...": "AI Select 実行中...",
    "Preview generation running...": "プレビュー生成中...",
    "SAM3 operation finished": "SAM3 処理 完了",
    "SAM3 operation blocked": "SAM3 処理 中断",
    "SAM3: the active layer is not a raster layer — select a raster layer and run again": "SAM3: 選択中のレイヤーはラスターレイヤーではありません — ラスターレイヤーを選択して再実行してください",
    "SAM3: no active layer to run on": "SAM3: 実行対象のレイヤーがありません",
    "SAM3: run blocked": "SAM3: 実行できません",
    "Select this candidate as the target for Apply / AI Refine": "この候補を選択して Apply / AI Refine の対象にする",
    "Exclude this candidate (also removed from Next/Previous cycling)": "この候補を除外する (Next/Previous の巡回からも外れる)",
    "Re-run SAM3 on this candidate to improve the mask (AI Refine)": "SAM3 で再推論してマスク品質を改善する (AI Refine)",
    "Apply this mask as the active layer's layer mask": "このマスクをアクティブレイヤーのレイヤーマスクとして適用",
    "Use this mask as the AI Effect inpaint mask": "このマスクを AI Effect のインペイントマスクに接続",
    "Protect the subject: invert the mask and use it as the background generation mask": "被写体を保護: マスクを反転して背景生成マスクに接続",
    "Generate one inpaint preview (result goes to the Gallery; the canvas is unchanged)": "インペイントのプレビューを 1 回生成 (結果は Gallery へ、キャンバスは不変)",
    "Apply this mask as a clipping mask": "このマスクをクリッピングマスクとして適用",
    "Cut the masked area to a new layer": "マスク部分を切り取って新規レイヤーへ",
    "Copy the masked area (foreground) to a new layer": "マスク部分 (前景) を新規レイヤーに複製",
    "Copy everything except the mask (background) to a new layer": "マスク以外 (背景) を新規レイヤーに複製",
    "Save this mask to the generation history (Gallery) for reuse": "マスクを生成履歴 (Gallery) に保存して再利用",
    "Combine the multi-selected candidates (Ctrl+click rows) into one mask using the Mode": "複数選択した候補 (行を Ctrl+クリック) を Mode の方式で 1 つのマスクに合成",
    "Cut each multi-selected candidate to its own layer inside a new layer group": "複数選択した候補をそれぞれレイヤー化して新規レイヤーグループにまとめる",
    "Manually adjust the selected candidate's mask (feather / expand / fill holes — no AI inference)": "選択中候補のマスクを手動調整 (ぼかし / 拡張 / 穴埋め — AI 推論なし)",
    "Unavailable": "この候補では実行できません",
    "Export PNG...": "PNG 書き出し...",
    "Export All...": "全候補を書き出し...",
    "Export Sheet...": "シート書き出し...",
    "Export Selection...": "選択範囲を書き出し...",
    "Sheet Spacing (px)": "シート余白 (px)",
    "Sheet Power of Two": "シートを 2 の冪に",
    "Sheet Format": "シート形式",
    "Export PNG": "PNG 書き出し",
    "Export All Candidates": "全候補を書き出し",
    "Export Sprite Sheet": "スプライトシート書き出し",
    "Feather": "ぼかし",
    "Expand": "拡張",
    "Contract": "収縮",
    "Smooth": "平滑化",
    "Fill holes": "穴を埋める",
    "Remove small islands": "小さな飛び地を除去",
    "Invert": "反転",
    "Diff QA": "差分 QA",
}
_JA_FALLBACK_TRANSLATIONS["SAM3MangaMaskAssistant"] = {
    "Inpaint submit": "インペイント送信",
    "Diff QA": "差分 QA",
}
_JA_FALLBACK_TRANSLATIONS["BrushManagerPanel"] = {
    "Brushes": "ブラシ",
    "Pen": "ペン",
    "Brush Manager": "ブラシマネージャー",
    "Create Tip": "チップを作成",
    "Save Params": "パラメータを保存",
    "Import ABR": "ABR をインポート",
    "Rescan ABR": "ABR を再スキャン",
    "Import PNG": "PNG をインポート",
    "Delete": "削除",
}
_JA_FALLBACK_TRANSLATIONS["BasicPen"].update(
    {
        source: PEN_PRESET_JA[source]
        for source in (
            "Taper In Length",
            "Taper Out Length",
            "Taper Start Length",
            "Taper End Length",
            "Pen taper could not be completed. The stroke was preserved.",
        )
    }
)
_JA_FALLBACK_TRANSLATIONS["TextFontPanel"].update({"Font": "フォント"})
_JA_FALLBACK_TRANSLATIONS["ShapeManagerPlugin"].update(
    {
        "Shape Manager": "シェイプマネージャー",
        "Manage manga shapes, balloons, and frames.": (
            "マンガの図形・フキダシ・コマを管理します。"
        ),
        "Add Shape": "シェイプを追加",
        "Basic Shapes": "基本シェイプ",
        "Import": "インポート",
        "Delete": "削除",
        "Search...": "検索...",
        "Category:": "カテゴリ:",
        "Tag:": "タグ:",
        "Name:": "名前:",
        "Tags:": "タグ:",
        "Align Reference:": "整列基準:",
        "Select a shape": "シェイプを選択",
        "custom": "カスタム",
        "comma-separated": "カンマ区切り",
        "All": "すべて",
        "Canvas": "キャンバス",
        "Selection": "選択範囲",
        "Drag onto the canvas to add shapes. Right-click for conversion handles.": (
            "キャンバスへドラッグしてシェイプを追加します。右クリックで変換ハンドルを表示します。"
        ),
        "Fit Contain": "収める",
        "Fit Stretch": "引き伸ばす",
        "Center": "中央",
        "Top": "上",
        "V-Center": "垂直中央",
        "Bottom": "下",
        "Left": "左",
        "H-Center": "水平中央",
        "Right": "右",
        "H-Distribute": "水平分布",
        "V-Distribute": "垂直分布",
        "basic": "基本図形",
        "effect": "効果",
        "balloon": "ふきだし",
        "polygon": "多角形",
        "Ellipse": "楕円",
        "Rounded Rectangle": "角丸長方形",
        "Parallelogram": "平行四辺形",
        "Trapezoid": "台形",
        "Focus Lines": "集中線",
        "Speech Balloon (Round)": "丸ふきだし",
        "Speech Balloon (Rect)": "角丸ふきだし",
        "Speech Balloon (Cloud)": "雲形ふきだし",
        "Spiky Balloon": "トゲふきだし",
        "Triangle": "三角形",
        "Quadrilateral": "四角形",
        "Pentagon": "五角形",
        "Hexagon": "六角形",
        "Heptagon": "七角形",
        "Octagon": "八角形",
        "Nonagon": "九角形",
        "Decagon": "十角形",
    }
)
_JA_FALLBACK_TRANSLATIONS["ExportPrintDialog"] = {
    "Export for Print": "印刷用に書き出し",
    "Output:": "出力:",
    "Standard PDF": "標準 PDF",
    "PDF/X-1a": "PDF/X-1a",
    "PDF/X-4": "PDF/X-4",
    "CMYK TIFF sequence": "CMYK TIFF 連番",
    "Grayscale K TIFF sequence": "グレースケール K TIFF 連番",
    "Not available: no PDF/X backend is installed.": "利用できません: PDF/X バックエンドが導入されていません。",
    "Colour": "カラー",
    "Files": "ファイル",
    "Preview": "プレビュー",
    "CMYK profile:": "CMYK プロファイル:",
    "Rendering intent:": "レンダリングインテント:",
    "Relative Colorimetric": "相対的な色域を維持",
    "Perceptual": "知覚的",
    "Saturation": "彩度",
    "Absolute Colorimetric": "絶対的な色域を維持",
    "Black point compensation": "黒点の補正",
    "Source profile: {name}": "ソースプロファイル: {name}",
    "sRGB (assumed)": "sRGB (仮定)",
    "Load ICC Profile...": "ICC プロファイルを読み込む...",
    "Choose CMYK Profile": "CMYK プロファイルを選択",
    "ICC Profiles (*.icc *.icm);;All Files (*.*)": "ICC プロファイル (*.icc *.icm);;すべてのファイル (*.*)",
    "The file is not a usable ICC profile.": "このファイルは ICC プロファイルとして使用できません。",
    "The profile is not a CMYK profile.": "このプロファイルは CMYK ではありません。",
    "No CMYK profile loaded": "CMYK プロファイル未読込",
    "A CMYK profile is required for CMYK output.": "CMYK 出力には CMYK プロファイルが必要です。",
    "File names:": "ファイル名:",
    "Existing files:": "既存ファイル:",
    "Ask before replacing": "上書き前に確認",
    "Replace existing files": "既存ファイルを置き換える",
    "Skip existing files": "既存ファイルをスキップ",
    "Add a numeric suffix": "連番を付けて保存",
    "Invalid file name template: {error}": "ファイル名テンプレートが不正です: {error}",
    "Flattened CMYK TIFF, one file per page, with the profile embedded.": "透明を統合した CMYK TIFF を 1 ページ 1 ファイルで書き出します (プロファイル同梱)。",
    "Grayscale K TIFF: flattened lightness on the K channel (C=M=Y=0), one file per page.": "グレースケール K TIFF: 統合後の明度を K チャンネルに配置します (C=M=Y=0)。1 ページ 1 ファイルです。",
    "Trim: {width} x {height} mm": "仕上がり: {width} x {height} mm",
    "No physical layout: the whole canvas is the trim area (no bleed).": "物理レイアウトがないため、キャンバス全体を仕上がり領域として扱います (塗り足しなし)。",
    "Choose Output Folder": "出力フォルダーを選択",
    "The output profile could not be used: {error}": "出力プロファイルを使用できません: {error}",
    "The document profile is not a usable ICC profile.": "ドキュメントのプロファイルは ICC プロファイルとして使用できません。",
    "Exporting print pages...": "印刷用ページを書き出しています...",
    "{count} file(s) already exist and will be replaced. Continue?": "{count} 件のファイルが既に存在し、置き換えられます。続行しますか?",
    "Exported {done} of {total} page(s).": "{total} ページ中 {done} ページを書き出しました。",
    "Skipped: {count}": "スキップ: {count} 件",
    "Failed: {names}": "失敗: {names}",
    "No pages are available to export.": "書き出せるページがありません。",
    "PDF Document (*.pdf)": "PDF ドキュメント (*.pdf)",
    "All Pages": "すべてのページ",
    "Page Range": "ページ範囲",
    "Current Page": "現在のページ",
    "Pages:": "対象:",
    "Range:": "範囲:",
    "Draw crop marks": "トンボを描く",
    "Standard RGB PDF (not PDF/X). One logical page per PDF page, in reading order.": (
        "標準 RGB PDF です (PDF/X ではありません)。論理順で 1 ページ = "
        "PDF 1 ページとして出力します。"
    ),
    "Invalid page range: {error}": "ページ範囲が不正です: {error}",
    "No pages match this scope.": "対象になるページがありません。",
    "{count} page(s) will be exported.": "{count} ページを書き出します。",
    "Exporting print PDF...": "印刷用 PDF を書き出しています...",
    "Cancel": "キャンセル",
    "Exported {count} page(s) to {name}.": "{name} に {count} ページを書き出しました。",
    "Some pages have no bleed; the whole canvas is the trim area.": (
        "塗り足しのないページがあります (キャンバス全体を仕上がり領域として扱います)。"
    ),
    "Page {page} could not be rendered; no PDF was written.": (
        "ページ {page} を描画できなかったため、PDF は書き出されていません。"
    ),
    "The PDF could not be written.": "PDF を書き出せませんでした。",
}
_JA_FALLBACK_TRANSLATIONS["FontDiagnosticsDialog"] = {
    "Font Diagnostics": "フォント診断",
    "Font": "フォント",
    "Status": "状態",
    "Pages": "ページ",
    "Objects": "オブジェクト",
    "Characters": "文字数",
    "Current page": "現在のページ",
    "Whole document": "ドキュメント全体",
    "Substitute...": "置換...",
    "Convert to Outlines...": "アウトライン化...",
    "Substitute with:": "置換先:",
    "Scope:": "範囲:",
    "(default font)": "(既定フォント)",
    "exact": "利用可",
    "family_only": "利用可 (スタイル代替)",
    "fallback": "欠落 (代替表示)",
    "missing": "欠落",
    "unknown": "不明",
    "{fonts} font(s), {missing} missing, {objects} text object(s).": "フォント {fonts} 件、欠落 {missing} 件、テキストオブジェクト {objects} 件。",
    "Convert to Outlines": "アウトライン化",
    "Convert the text objects using this font on the current page to outlines? They stop being editable text (searchability is lost); the original text is kept for restore.": "現在のページでこのフォントを使うテキストオブジェクトをアウトライン化しますか? 編集可能なテキストではなくなります (検索性は失われます)。元のテキストは復元用に保持されます。",
    "No document is open.": "ドキュメントが開かれていません。",
    "Substitute Font": "フォント置換",
    "Substituted {count} text object(s) on the current page.": "現在のページのテキストオブジェクト {count} 件を置換しました。",
    "{count} object(s) on other pages were left unchanged.": "他のページの {count} 件は変更していません。",
    "Convert Text to Outlines": "テキストのアウトライン化",
    "Converted {count} text object(s) to outlines.": "テキストオブジェクト {count} 件をアウトライン化しました。",
    "{count} object(s) could not be outlined.": "{count} 件はアウトライン化できませんでした。",
}
_JA_FALLBACK_TRANSLATIONS["LinksPanel"] = {
    "Links Panel": "リンクパネル",
    "Linked and embedded resources: status, relink, reload, embed": "リンク / 埋め込み素材の状態・再リンク・再読込・埋め込み",
    "Links": "リンク",
    "Status": "状態",
    "Name": "名前",
    "Kind": "種類",
    "Used by": "使用レイヤー",
    "Modified": "更新日時",
    "Hash": "ハッシュ",
    "Refresh": "更新",
    "Verify Hashes": "ハッシュを検証",
    "Reveal": "場所を開く",
    "Relink...": "再リンク...",
    "Reload": "再読込",
    "Replace...": "差し替え...",
    "Embed": "埋め込む",
    "Go to Layer": "レイヤーへ移動",
    "ok": "OK",
    "modified": "変更あり",
    "missing": "見つからない",
    "unsupported": "未対応",
    "unsafe": "安全でない",
    "offline": "オフライン",
    "linked": "リンク",
    "embedded": "埋め込み",
    "not verified": "未検証",
    "match": "一致",
    "changed": "変更",
    "{count} resource(s), {attention} need attention": "素材 {count} 件、要確認 {attention} 件",
    "The file location is unknown.": "ファイルの場所が不明です。",
    "Reloaded {count} layer(s).": "{count} レイヤーを再読込しました。",
    "Relinked {count} layer(s).": "{count} レイヤーを再リンクしました。",
    "Embedded {count} layer(s).": "{count} レイヤーを埋め込みました。",
    "Nothing changed: {code}": "変更なし: {code}",
    "{name} has different content than the linked resource. Relink anyway?": "{name} の内容はリンク中の素材と異なります。それでも再リンクしますか?",
    "Choose the file": "ファイルを選択",
    "Place Image": "画像を配置",
    "How should {name} be placed?": "{name} をどのように配置しますか?",
    "Link to the file (stays external, can be reloaded)": "ファイルにリンク (外部のまま、再読込可能)",
    "Embed a copy in the document": "コピーをドキュメントに埋め込む",
    "{name} is not a supported image format.": "{name} は対応していない画像形式です。",
    "{name} is a symbolic link; place the real file instead.": "{name} はシンボリックリンクです。実ファイルを配置してください。",
    "Linked": "リンクしました",
    "Embedded": "埋め込みました",
}
_JA_FALLBACK_TRANSLATIONS["DocumentModeDialog"] = {
    "Document Mode": "ドキュメントモード",
    "Current mode: {mode} ({width} x {height}, {layers} layer(s))": "現在のモード: {mode} ({width} x {height}、{layers} レイヤー)",
    "Mode": "モード",
    "Bits": "ビット",
    "Channels": "チャンネル",
    "Storage": "保存",
    "Brush": "ブラシ",
    "Export": "書き出し",
    "yes": "可",
    "no": "未",
    "Convert to:": "変換先:",
    "Keep alpha": "アルファを保持",
    "Plates:": "版:",
    "Estimate Ink Coverage": "インキ被覆率を見積もる",
    "Close": "閉じる",
    "Conversion {src} -> {dst}: {state}": "変換 {src} -> {dst}: {state}",
    "ready (dry run only)": "実行可能 (ドライランのみ)",
    "blocked": "ブロック",
    "Estimated pixel memory: {mib:.1f} MiB": "推定ピクセルメモリ: {mib:.1f} MiB",
    "Blocked: {codes}": "ブロック理由: {codes}",
    "Warnings: {codes}": "警告: {codes}",
    "No composite is available.": "合成画像が取得できません。",
    "Ink coverage (estimate, no output profile): {coverage}": "インキ被覆率 (出力プロファイルなしの見積もり): {coverage}",
    "TAC max {max:.1f}% / mean {mean:.1f}% / over {limit:.0f}%: {over:.2f}% of pixels": "TAC 最大 {max:.1f}% / 平均 {mean:.1f}% / {limit:.0f}% 超過: 画素の {over:.2f}%",
    "Plates on: {plates}": "表示中の版: {plates}",
}
_JA_FALLBACK_TRANSLATIONS["ComicWorkflowPanel"] = {
    "Comic Workflow Panel": "コミックワークフローパネル",
    "Book plan, page plan and editable apply for AI comic production": "AI 漫画制作の book plan・page plan・編集可能な適用",
    "Comic Workflow": "コミックワークフロー",
    "Project:": "プロジェクト:",
    "Plan Book": "Book を計画",
    "Validate": "検証",
    "Page / Panel": "ページ / コマ",
    "Status": "状態",
    "Dialogues": "台詞",
    "Plan Page": "ページを計画",
    "Preview Apply": "適用をプレビュー",
    "Apply Page": "ページに適用",
    "Select a comic project first.": "先にコミックプロジェクトを選択してください。",
    "Planned {pages} page(s), {panels} panel(s); revision {revision}": "{pages} ページ / {panels} コマを計画しました (revision {revision})",
    "No issues.": "問題はありません。",
    "Page {number}": "ページ {number}",
    "[unlinked]": "[未リンク]",
    "planned": "計画済",
    "generating": "生成中",
    "review": "レビュー",
    "ready": "準備完了",
    "applied": "適用済",
    "blocked": "ブロック",
    "accepted": "採用",
    "rejected": "却下",
    "Page plan ready: {count} panel(s)": "ページ計画が完了: {count} コマ",
    "Page plan blocked: {codes}": "ページ計画がブロック: {codes}",
    "Ready to apply.": "適用できます。",
    "Apply is blocked.": "適用はブロックされています。",
    "Apply {count} operation(s) to the current page as one undo step?": "{count} 件の操作を現在のページに 1 回の Undo として適用しますか?",
    "Next step: {step}": "次のステップ: {step}",
}
_JA_FALLBACK_TRANSLATIONS["AppearancePanel"] = {
    "Adjustment Layer": "調整レイヤー",
    "Live Filter Layer": "ライブフィルターレイヤー",
    "Fill Layer": "塗りつぶしレイヤー",
    "No effects available": "利用できるエフェクトがありません",
    "Layer Styles...": "レイヤースタイル...",
    "Edit Fill...": "塗りつぶしを編集...",
    "Rasterize Appearance": "外観をラスタライズ",
    "Solid Color": "単色",
    "Gradient": "グラデーション",
    "Pattern": "パターン",
    "Drop Shadow": "ドロップシャドウ",
    "Outer Glow": "光彩 (外側)",
    "Color Overlay": "カラーオーバーレイ",
    "Stroke": "境界線",
    "Edit Fill": "塗りつぶしを編集",
    "Kind": "種類",
    "Layer Styles": "レイヤースタイル",
    "Effects": "効果",
    "Presets": "プリセット",
    "Blend mode": "描画モード",
    "Normal": "通常",
    "Darken": "比較 (暗)",
    "Multiply": "乗算",
    "Color Burn": "焼き込みカラー",
    "Linear Burn": "焼き込み (リニア)",
    "Darker Color": "カラー比較 (暗)",
    "Lighten": "比較 (明)",
    "Screen": "スクリーン",
    "Color Dodge": "覆い焼きカラー",
    "Linear Dodge": "覆い焼き (リニア)",
    "Lighter Color": "カラー比較 (明)",
    "Overlay": "オーバーレイ",
    "Soft Light": "ソフトライト",
    "Hard Light": "ハードライト",
    "Vivid Light": "ビビッドライト",
    "Linear Light": "リニアライト",
    "Pin Light": "ピンライト",
    "Hard Mix": "ハードミックス",
    "Difference": "差の絶対値",
    "Exclusion": "除外",
    "Subtract": "減算",
    "Divide": "除算",
    "Apply preset": "プリセットを適用",
    "Save new": "新規登録",
    "Update preset": "内容を更新",
    "Rename": "名前を変更",
    "Delete preset": "プリセットを削除",
    "Reload": "再読込",
    "Preset name": "プリセット名",
    "No presets yet. Save the current styles to create one.": "プリセットはまだありません。現在のスタイルを登録できます。",
    "Could not access presets. The previous library was kept. Check permissions and reload.": "プリセットを読み書きできません。元のライブラリは保持しました。権限を確認して再読込してください。",
    "Offset X": "横方向の距離",
    "Gaussian Blur": "ガウスぼかし",
    "Inner Shadow": "シャドウ (内側)",
    "Inner Glow": "光彩 (内側)",
    "Gradient Overlay": "グラデーションオーバーレイ",
    "Bevel / Emboss": "ベベル / エンボス",
    "3D": "3D",
    "Radius": "半径",
    "Depth": "深さ",
    "Soften": "ソフト化",
    "Light direction": "光の方向",
    "Elevation": "光の高さ",
    "Highlight color": "ハイライト色",
    "Shadow color": "シャドウ色",
    "Shape": "形状",
    "Start color": "開始色",
    "End color": "終了色",
    "Angle": "角度",
    "Scale": "スケール",
    "Center X": "中心 X",
    "Center Y": "中心 Y",
    "Type": "種類",
    "Invert": "反転",
    "Ambient": "環境光",
    "Diffuse": "拡散光",
    "Specular": "鏡面反射",
    "Shininess": "光沢",
    "Light sources": "光源",
    "Add light": "光源を追加",
    "Remove light": "光源を削除",
    "Light {number}": "光源 {number}",
    "Intensity": "強さ",
    "Profile": "プロファイル曲線",
    "Interpolation": "補間",
    "Linear": "線形",
    "Smooth": "滑らか",
    "Reset profile": "曲線をリセット",
    "Remove point": "制御点を削除",
    "Input": "入力",
    "Output": "出力",
    "Click to add a point. Drag to adjust. Right-click or Delete removes an interior point.": "クリックで制御点を追加、ドラッグで調整。右クリックまたはDeleteキーで途中の制御点を削除します。",
    "linear": "線形",
    "radial": "放射状",
    "inner": "内側ベベル",
    "outer": "外側ベベル",
    "emboss": "エンボス",
    "pillow": "ピローエンボス",
    "Offset Y": "縦方向の距離",
    "Blur": "ぼかし",
    "Color": "色",
    "Opacity": "不透明度",
    "Size": "サイズ",
    "Add": "追加",
    "Remove": "削除",
    "Up": "上へ",
    "Down": "下へ",
    "Enabled": "有効",
    "disabled": "無効",
    "Apply": "適用",
    "Close": "閉じる",
    "Applied.": "適用しました。",
    "Could not apply styles.": "スタイルを適用できませんでした。",
    "Rasterized into a new layer.": "新しいレイヤーにラスタライズしました。",
    "Nothing to rasterize.": "ラスタライズするものがありません。",
}
_JA_FALLBACK_TRANSLATIONS["FilterPanel"] = {
    **_JA_FALLBACK_TRANSLATIONS["EffectPanel"],
    **FILTER_PANEL_JA,
}
_JA_FALLBACK_TRANSLATIONS["FilterPanelPlugin"] = FILTER_PANEL_PLUGIN_JA
_JA_FALLBACK_TRANSLATIONS["EffectPanelPlugin"] = FILTER_PANEL_PLUGIN_JA
_JA_FALLBACK_TRANSLATIONS["LayerPanel"].update(FILTER_LAYER_JA)
_JA_FALLBACK_TRANSLATIONS["AppearancePanel"].update(FILTER_APPEARANCE_JA)
_JA_FALLBACK_TRANSLATIONS["MainWindow"].update(FILTER_EXPORT_JA)
_JA_FALLBACK_TRANSLATIONS["DrawingAssistPanel"] = {
    "Drawing Assist Panel": "作画補助パネル",
    "Symmetry, parallel / radial / concentric rulers and perspective": "対称描画・平行 / 放射 / 同心円定規・透視",
    "Drawing Assist": "作画補助",
    "mirror": "ミラー",
    "rotational": "回転対称",
    "parallel": "平行定規",
    "radial": "放射定規",
    "concentric": "同心円定規",
    "perspective": "透視定規",
    "Add": "追加",
    "Remove": "削除",
    "Set Active": "アクティブにする",
    "Enabled": "有効",
    "Show overlay": "オーバーレイ表示",
    "Apply": "適用",
    "Refresh": "更新",
    "active": "アクティブ",
    "disabled": "無効",
    "unknown type": "未知の種類",
    "{count} assist(s) on this page": "このページの補助: {count} 件",
    "Not applied: {codes}": "適用できません: {codes}",
}
_JA_FALLBACK_TRANSLATIONS["ActionsPanel"] = {
    "Actions Panel": "アクションパネル",
    "Record, play and batch declarative macros": "宣言的マクロの記録・再生・一括実行",
    "Actions": "アクション",
    "Macros": "マクロ",
    "Run Action...": "アクションを実行...",
    "Record": "記録",
    "Stop Recording": "記録を停止",
    "Play": "再生",
    "Batch...": "一括実行...",
    "Dry Run": "ドライラン",
    "Delete": "削除",
    "Import...": "読み込み...",
    "Export...": "書き出し...",
    "Recording: {count} step(s)": "記録中: {count} ステップ",
    "Recording started. Run actions from this panel to add steps.": "記録を開始しました。このパネルからアクションを実行するとステップに追加されます。",
    "Nothing recorded.": "何も記録されませんでした。",
    "Saved macro {name} ({count} step(s), {dropped} dropped)": "マクロ {name} を保存しました ({count} ステップ、{dropped} 件は記録不可)",
    "Run {title}? This cannot be previewed.": "{title} を実行しますか? プレビューはできません。",
    "Macro is not ready on this page; see the dry run.": "このページではマクロを実行できません。ドライランを確認してください。",
    "Run this macro?": "このマクロを実行しますか?",
    "No targets.": "対象がありません。",
    "Run {name} on {count} page(s) with policy {policy}?": "{name} を {count} ページに実行しますか? (失敗時: {policy})",
    "Not processed: {ids}": "未処理: {ids}",
    "Delete macro {name}?": "マクロ {name} を削除しますか?",
    "Import macro": "マクロを読み込み",
    "Imported {name}": "{name} を読み込みました",
    "The file is not a valid macro.": "有効なマクロファイルではありません。",
    "Export macro": "マクロを書き出し",
    "That file already exists; choose another name.": "同名のファイルがあります。別の名前を選んでください。",
    "Exported {name}": "{name} を書き出しました",
    "Save macro": "マクロを保存",
    "Macro name:": "マクロ名:",
    "Target": "対象",
    "Layer name": "レイヤー名",
    "layer name (for layer_by_name)": "レイヤー名 (layer_by_name 用)",
    "active_layer": "アクティブレイヤー",
    "all_layers": "すべてのレイヤー",
    "layer_by_name": "名前で指定したレイヤー",
    "Browse...": "参照...",
    "Choose a folder": "フォルダーを選択",
    "Batch: {name}": "一括実行: {name}",
    "Targets ({count} page(s))": "対象 (全 {count} ページ)",
    "Current page": "現在のページ",
    "All pages": "すべてのページ",
    "Page range (e.g. 1-3,5)": "ページ範囲 (例: 1-3,5)",
    "On failure:": "失敗時:",
    "stop": "停止",
    "continue": "続行",
    "skip_blocked": "ブロックをスキップ",
    "Run": "実行",
    "Cancel": "キャンセル",
}
_JA_FALLBACK_TRANSLATIONS["PackageProjectDialog"] = {
    "Package Project": "プロジェクトをパッケージ",
    "Destination folder:": "出力フォルダー:",
    "Browse...": "参照...",
    "Resource": "素材",
    "State": "状態",
    "Size": "サイズ",
    "Action": "処理",
    "Write Package": "パッケージを書き出す",
    "Close": "閉じる",
    "Choose the package folder": "パッケージフォルダーを選択",
    "Copy into package": "パッケージにコピー",
    "Leave external": "外部のまま",
    "Already embedded": "埋め込み済み",
    "Missing": "見つからない",
    "Copy {copy} / external {external} / embedded {embed} / missing {missing}": "コピー {copy} / 外部 {external} / 埋め込み {embed} / 欠落 {missing}",
    "Issues: {issues}": "問題: {issues}",
    "Save the document before packaging.": "パッケージの前にドキュメントを保存してください。",
    "Package written: {path} ({copied} asset(s) copied, {omitted} omitted)": "パッケージを書き出しました: {path} (素材 {copied} 件コピー、{omitted} 件除外)",
    "Package failed: {code} {detail}": "パッケージに失敗しました: {code} {detail}",
}
_JA_FALLBACK_TRANSLATIONS["ComicProjectPanel"] = {
    "Comic Project Panel": "コミックプロジェクトパネル",
    "Story, dialogue and production management": "ネーム・台詞・制作進捗の管理",
    "Comic Project": "コミックプロジェクト",
    "Project:": "プロジェクト:",
    "New...": "新規...",
    "Import...": "読み込み...",
    "Export...": "書き出し...",
    "Search dialogue...": "台詞を検索...",
    "Chapters / pages": "章 / ページ",
    "#": "#",
    "Speaker": "話者",
    "Text": "台詞",
    "Page": "ページ",
    "Review": "校正",
    "Placed": "配置",
    "yes": "済",
    "no": "未",
    "Speaker:": "話者:",
    "Text:": "台詞:",
    "Review:": "校正:",
    "Direction:": "方向:",
    "Page:": "ページ:",
    "draft": "下書き",
    "needs_review": "要校正",
    "approved": "承認済",
    "vertical": "縦書き",
    "horizontal": "横書き",
    "name": "ネーム",
    "ink": "ペン入れ",
    "tone": "トーン",
    "lettering": "写植",
    "review": "校正",
    "done": "完了",
    "Apply to dialogue": "台詞に反映",
    "Add chapter": "章を追加",
    "Add page": "ページを追加",
    "Add dialogue": "台詞を追加",
    "Delete dialogue": "台詞を削除",
    "Link page to current": "現在のページにリンク",
    "Create text object": "テキストを作成",
    "Next unplaced": "次の未配置",
    "Next needs review": "次の要校正",
    "Check links": "リンクを検査",
    "(no project)": "(プロジェクトなし)",
    "(unassigned)": "(未割り当て)",
    "(new line)": "(新しい台詞)",
    "(linked)": "(リンク済)",
    "{title} ({count} pages)": "{title} ({count} ページ)",
    "p.{number}  [{status}]  {placed}/{total} placed": "p.{number}  [{status}]  配置 {placed}/{total}",
    "Not linked to a Comic Project.": "コミックプロジェクトにリンクされていません。",
    "{chapters} chapter(s), {pages} page(s) ({linked} linked), {dialogues} line(s), {placed} placed, {review} need review.": "{chapters} 章、{pages} ページ ({linked} リンク済)、台詞 {dialogues} 件、配置 {placed} 件、要校正 {review} 件。",
    "The project was changed elsewhere; it has been reloaded.": "プロジェクトが別の場所で変更されたため再読み込みしました。",
    "The change could not be saved.": "変更を保存できませんでした。",
    "New Comic Project": "新しいコミックプロジェクト",
    "Title:": "タイトル:",
    "Chapter {number}": "第 {number} 章",
    "Select a page entry and open the page to link.": "リンクするページ項目を選び、対象ページを開いてください。",
    "Assign the line to a linked page first.": "先に台詞をリンク済みページへ割り当ててください。",
    "The linked page could not be opened.": "リンク先のページを開けませんでした。",
    "The text object could not be created.": "テキストオブジェクトを作成できませんでした。",
    "Links: {unresolved_pages} missing page(s), {unplaced} unplaced, {unresolved_text_refs} lost text object(s), {duplicates} duplicate(s), {text_diffs} text difference(s).": "リンク: ページ欠落 {unresolved_pages}、未配置 {unplaced}、テキスト消失 {unresolved_text_refs}、重複 {duplicates}、本文差分 {text_diffs}。",
    "Import Script": "台本の読み込み",
    "Export Script": "台本の書き出し",
    "Script files (*.csv *.json)": "台本ファイル (*.csv *.json)",
    "The file could not be read.": "ファイルを読み込めませんでした。",
    "The file could not be written.": "ファイルを書き込めませんでした。",
    "The file was rejected: {codes}": "ファイルを受け付けませんでした: {codes}",
    "Add {add} line(s), update {update}, unchanged {unchanged}, invalid {invalid}. Apply?": "追加 {add} 件、更新 {update} 件、変更なし {unchanged} 件、不正 {invalid} 件。適用しますか?",
}
_JA_FALLBACK_TRANSLATIONS["ExportDigitalPackageDialog"] = {
    "Export Digital Comic Package": "デジタル漫画パッケージを書き出し",
    "CBZ": "CBZ",
    "Fixed-Layout EPUB 3": "固定レイアウト EPUB 3",
    "Image Package + Manifest": "画像パッケージ + マニフェスト",
    "Right binding (right-to-left)": "右綴じ (右から左)",
    "Left binding (left-to-right)": "左綴じ (左から右)",
    "Include the LoomStroke manifest in the CBZ": "CBZ に LoomStroke マニフェストを含める",
    "Metadata": "書誌情報",
    "Generate": "生成",
    "Pages": "ページ",
    "Format:": "形式:",
    "Images:": "画像:",
    "Binding:": "綴じ方向:",
    "Title:": "タイトル:",
    "Language:": "言語:",
    "Identifier:": "識別子:",
    "Creators:": "作者:",
    "Series:": "シリーズ:",
    "Volume:": "巻:",
    "Description:": "説明:",
    "Cover:": "表紙:",
    "CBZ is a de facto format: readers interpret ComicInfo.xml differently.": "CBZ は事実上の標準であり、ComicInfo.xml の解釈はリーダーごとに異なります。",
    "Image-only fixed-layout EPUB 3. Conformance is reported as checked only when EPUBCheck is installed.": "画像のみの固定レイアウト EPUB 3 です。適合性は EPUBCheck が導入されている場合にのみ「検査済み」と表示します。",
    "A folder of images plus publication_manifest.json — a neutral exchange package, not CBZ or EPUB.": "画像フォルダーと publication_manifest.json による中立な交換パッケージです (CBZ / EPUB ではありません)。",
    "{count} page(s) in story order, page progression {progression}.": "{count} ページ (ストーリー順)、ページ進行方向 {progression}。",
    "No pages are included.": "含まれるページがありません。",
    "Title, language and identifier are required.": "タイトル・言語・識別子は必須です。",
    "The cover page is not included.": "表紙ページが含まれていません。",
    "No pages are available to export.": "書き出せるページがありません。",
    "Choose Output Folder": "出力フォルダーを選択",
    "Comic Book Archive (*.cbz)": "コミックブックアーカイブ (*.cbz)",
    "EPUB Publication (*.epub)": "EPUB 出版物 (*.epub)",
    "{name} already exists and will be replaced. Continue?": "{name} は既に存在し、置き換えられます。続行しますか?",
    "Exporting package...": "パッケージを書き出しています...",
    "Cancel": "キャンセル",
    "The package could not be written: {codes}": "パッケージを書き出せませんでした: {codes}",
    "Exported {count} page(s) to {name}.": "{name} に {count} ページを書き出しました。",
    "Warnings: {codes}": "警告: {codes}",
    "EPUB conformance checked (EPUBCheck).": "EPUB 適合性を検査しました (EPUBCheck)。",
    "EPUB conformance not checked (EPUBCheck unavailable or failed).": "EPUB 適合性は未検査です (EPUBCheck が利用できないか失敗しました)。",
}
_JA_FALLBACK_TRANSLATIONS["MangaPreflightDialog"] = {
    "Manga Preflight": "漫画プリフライト",
    "Profile:": "プロファイル:",
    "Pages:": "対象:",
    "Current page": "現在のページ",
    "All pages": "すべてのページ",
    "Run Preflight": "プリフライトを実行",
    "Show:": "表示:",
    "Fatal": "致命的",
    "Warning": "警告",
    "Info": "情報",
    "Severity": "重要度",
    "Page": "ページ",
    "Rule": "ルール",
    "Reason": "理由",
    "Recommended action": "推奨対応",
    "Run the preflight to see issues.": "プリフライトを実行すると問題が表示されます。",
    "{fatal} fatal, {warning} warning(s), {info} info — {checked} rule(s) checked, {skipped} not checked.": "致命的 {fatal} 件、警告 {warning} 件、情報 {info} 件 — 検査したルール {checked}、未検査 {skipped}。",
    "Not checked:": "未検査:",
    "No pages are available to check.": "検査できるページがありません。",
    "Digital comic": "デジタル漫画",
    "Webtoon": "Webtoon",
    "Print (monochrome)": "印刷 (モノクロ)",
    "The document has no pages.": "ドキュメントにページがありません。",
    "Duplicate page id.": "ページ ID が重複しています。",
    "Odd page count ({count}); print books usually need an even count.": "ページ数が奇数です ({count})。印刷本は通常偶数ページが必要です。",
    "Page is {width} x {height} px, below the minimum of {minimum} px.": "ページは {width} x {height} px で、最小 {minimum} px を下回っています。",
    "Page size {width} x {height} px differs from the document's {expected_width} x {expected_height} px.": "ページサイズ {width} x {height} px がドキュメントの {expected_width} x {expected_height} px と異なります。",
    "Resolution {dpi} dpi is below the minimum of {minimum} dpi.": "解像度 {dpi} dpi は最小 {minimum} dpi を下回っています。",
    "Pages use different resolutions ({values} dpi).": "ページごとに解像度が異なります ({values} dpi)。",
    "The page has no visible content.": "ページに表示される内容がありません。",
    "Page content could not be inspected.": "ページ内容を検査できませんでした。",
    "Text does not fit its frame.": "テキストがフレームに収まっていません。",
    "Text layout was not verified (vertical area text).": "テキストのレイアウトは未検証です (縦書きエリアテキスト)。",
    "Empty text object.": "空のテキストオブジェクトです。",
    "Font size {size} pt is below the minimum of {minimum} pt.": "フォントサイズ {size} pt は最小 {minimum} pt を下回っています。",
    'Font "{family}" is not installed.': "フォント「{family}」がインストールされていません。",
    "The document has no working colour profile.": "ドキュメントに作業用カラープロファイルがありません。",
    "No physical layout: bleed and trim are unknown for this page.": "物理レイアウトがないため、このページの塗り足しと仕上がりは不明です。",
    "Add a blank page": "白ページを追加する",
    "Adjust with Image Size...": "「画像サイズ...」で調整する",
    "Enlarge the text frame": "テキストフレームを広げる",
    "Reduce the font size": "フォントサイズを小さくする",
    "Delete the object": "オブジェクトを削除する",
    "Increase the font size": "フォントサイズを大きくする",
    "Install the font": "フォントをインストールする",
    "Replace the font": "フォントを置き換える",
    "Assign a colour profile in Image Properties": "「画像のプロパティ」でカラープロファイルを割り当てる",
    "Create the page from a manga profile": "漫画プロファイルからページを作成する",
    "Linked assets are not available in this build.": "リンク素材はこのビルドでは利用できません。",
    "Page content was not loaded.": "ページ内容が読み込まれていません。",
    "The font database is unavailable.": "フォントデータベースを利用できません。",
    "The profile references an unknown rule.": "プロファイルが未知のルールを参照しています。",
    "The rule failed to run.": "ルールの実行に失敗しました。",
    "The scan was cancelled.": "検査はキャンセルされました。",
    "Preflight found {count} fatal issue(s). Export anyway?": "プリフライトで致命的な問題が {count} 件見つかりました。それでも書き出しますか?",
    "The preflight report is out of date; run Manga Preflight again.": "プリフライトの結果が古くなっています。漫画プリフライトを再実行してください。",
    "Export Pages": "ページの一括書き出し",
    "Export for Print": "印刷用に書き出し",
    "Export Webtoon": "Webtoon を書き出し",
}
_JA_FALLBACK_TRANSLATIONS["ExportWebtoonDialog"] = {
    "Export Webtoon": "Webtoon を書き出し",
    "Segments": "セグメント",
    "Settings": "設定",
    "Profile: {name} (rev {revision}, updated {date})": "プロファイル: {name} (rev {revision}、更新 {date})",
    "Generic": "汎用",
    "Check the platform's current requirements before publishing.": "投稿前に各プラットフォームの最新の投稿条件を確認してください。",
    "Target width:": "出力幅:",
    "Max slice height:": "スライス最大高さ:",
    "Gap between pages:": "ページ間の余白:",
    " px": " px",
    "Format:": "形式:",
    "File names:": "ファイル名:",
    "Existing files:": "既存ファイル:",
    "Ask before replacing": "上書き前に確認",
    "Replace existing files": "既存ファイルを置き換える",
    "Skip existing files": "既存ファイルをスキップ",
    "Add a numeric suffix": "連番を付けて保存",
    "Write webtoon_manifest.json": "webtoon_manifest.json を書き出す",
    "Stream: {width} x {height} px, {count} slice(s), tallest {tallest} px": "ストリーム: {width} x {height} px、{count} スライス、最大高さ {tallest} px",
    "Boundaries are fixed-height only; check them in the preview before publishing.": "分割位置は固定高さのみで決めています。投稿前にプレビューで確認してください。",
    "No pages are included.": "含まれるページがありません。",
    "Invalid file name template: {error}": "ファイル名テンプレートが不正です: {error}",
    "Choose Output Folder": "出力フォルダーを選択",
    "No pages are available to export.": "書き出せるページがありません。",
    "{count} file(s) already exist and will be replaced. Continue?": "{count} 件のファイルが既に存在し、置き換えられます。続行しますか?",
    "Exporting webtoon slices...": "Webtoon スライスを書き出しています...",
    "Cancel": "キャンセル",
    "Exported {done} of {total} slice(s).": "{total} スライス中 {done} スライスを書き出しました。",
    "Skipped: {count}": "スキップ: {count} 件",
    "Warnings: {codes}": "警告: {codes}",
    "Failed: {names}": "失敗: {names}",
    "The manifest could not be written.": "webtoon_manifest.json を書き込めませんでした。",
}
_JA_FALLBACK_TRANSLATIONS["ExportPagesDialog"] = {
    "Existing files:": "既存ファイル:",
    "Ask before replacing": "上書き前に確認",
    "Replace existing files": "既存ファイルを置き換える",
    "Skip existing files": "既存ファイルをスキップ",
    "Add a numeric suffix": "連番を付けて保存",
    "Write export-report.json": "export-report.json を書き出す",
    "Skipped: {count}": "スキップ: {count} 件",
    "Warnings: {codes}": "警告: {codes}",
    "The export report could not be written.": "export-report.json を書き込めませんでした。",
    "Export Pages": "ページの一括書き出し",
    "All Pages": "すべてのページ",
    "Page Range": "ページ範囲",
    "Current Page": "現在のページ",
    "Odd Pages": "奇数ページ",
    "Even Pages": "偶数ページ",
    "By Role": "役割で選択",
    "Pages:": "対象:",
    "Range:": "範囲:",
    "Roles:": "役割:",
    "Format:": "形式:",
    "Folder:": "出力先:",
    "File names:": "ファイル名:",
    "Browse...": "参照...",
    "Cover": "表紙",
    "Inside Cover": "表紙裏",
    "Title": "扉",
    "Body": "本文",
    "Colophon": "奥付",
    "Select at least one role.": "役割を 1 つ以上選んでください。",
    "Invalid page range: {error}": "ページ範囲が不正です: {error}",
    "No pages match this scope.": "対象になるページがありません。",
    "Invalid name template: {error}": "ファイル名テンプレートが不正です: {error}",
    "The template produces duplicate file names (e.g. {name}).": (
        "ファイル名が重複します (例: {name})。"
    ),
    "Choose a destination folder.": "出力先フォルダーを選んでください。",
    "{count} pages -> {names}": "{count} ページ → {names}",
    "{count} file(s) already exist and will be replaced. Continue?": (
        "{count} 件のファイルが既に存在し、置き換えられます。続行しますか?"
    ),
    "Exporting pages...": "ページを書き出しています...",
    "Cancel": "キャンセル",
    "Exported {done} of {total} page(s).": "{total} ページ中 {done} ページを書き出しました。",
    "Failed: {names}": "失敗: {names}",
}
_JA_FALLBACK_TRANSLATIONS["ComicBookSettingsDialog"] = {
    "Comic Book Settings": "漫画作品の設定",
    "Binding:": "綴じ方向:",
    "Left Binding": "左綴じ",
    "Right Binding": "右綴じ",
    "Start body page:": "本文の開始面:",
    "Auto": "自動",
    "Recto (front face)": "表面 (recto)",
    "Verso (back face)": "裏面 (verso)",
    (
        "Which face the first body page falls on. Auto keeps the "
        "current layout; Recto and Verso insert at most one blank "
        "facing slot to line the body up."
    ): (
        "本文の最初のページがどちらの面に来るかを決めます。自動は現在の配置の"
        "まま、表面 / 裏面を選ぶと面を揃えるために空きスロットを最大 1 つ挿入"
        "します。"
    ),
    "Page display:": "ページ表示:",
    "Single": "単ページ",
    "Spread": "見開き",
    "Folio starts at:": "ノンブル開始番号:",
    # §6.1: spread preview inside the dialog.
    "Spread preview:": "見開きプレビュー:",
    "(empty)": "(空き)",
    "... and {count} more": "… 他 {count} 見開き",
    # F2: folio prefix + per-role display exclusion.
    "Folio prefix:": "ノンブル接頭辞:",
    "Example: {folio}": "例: {folio}",
    "Hide folio for:": "ノンブルを表示しない役割:",
    "Cover": "表紙",
    "Inside Cover": "表紙裏",
    "Title": "扉",
    "Body": "本文",
    "Colophon": "奥付",
    "Changing the binding does not reorder pages": (
        "綴じ方向を変えてもページの並び順は変わりません"
    ),
    "OK": "OK",
    "Cancel": "キャンセル",
}
_JA_FALLBACK_TRANSLATIONS["InterfacePage"] = {
    "Remember zoom and pan per page": "ページごとに拡大率と表示位置を記憶する",
    (
        "Each page keeps the zoom and position it was left at. Off keeps "
        "one view for the whole document."
    ): (
        "各ページが最後に見ていた拡大率と位置を保ちます。"
        "オフのときは作品全体で 1 つの表示状態を使います。"
    ),
}
_JA_FALLBACK_TRANSLATIONS["ColorSwatch"] = {
    "Foreground and background colors": "描画色と背景色",
    "Foreground color (click to change)": "描画色 (クリックで変更)",
    "Background color (click to change)": "背景色 (クリックで変更)",
    "Swap foreground and background colors": "描画色と背景色を入れ替え",
    "Default colors (black / white)": "初期設定の色 (黒 / 白)",
}
_JA_FALLBACK_TRANSLATIONS["PerformancePage"] = {
    "Undo Steps": "元に戻す回数",
    "Recompose FPS Limit": "再合成FPS上限",
    "Lower values reduce CPU load on large canvases": (
        "値を下げると大きなキャンバスでのCPU負荷が軽減されます"
    ),
    "Spread Facing Page Quality": "見開きの相手ページ画質",
    "Enable sharp vector rendering (zoom)": "拡大時にベクターをくっきり表示する",
    (
        "Composites the display at view resolution so vector and text "
        "layers stay sharp when zoomed in, regardless of layer order."
    ): (
        "表示解像度で合成し、拡大してもベクターレイヤーとテキストレイヤーを"
        "レイヤー順に関係なくくっきり表示します。"
    ),
    "Full": "フル",
    "Balanced": "バランス",
    "Low": "低",
    (
        "Lowers the composite resolution of the facing page in a spread. "
        "Full keeps it as sharp as the page being edited; the lower steps "
        "trade sharpness for GPU memory on large, many-layered pages."
    ): (
        "見開きで隣に表示される相手ページの合成解像度を下げます。"
        "フルは編集中のページと同じシャープさを保ちます。"
        "大きく多レイヤーのページでは、下げるほど GPU メモリを節約できます。"
    ),
}
_JA_FALLBACK_TRANSLATIONS["FileIOPage"] = {
    "Enable autosave (effective after restart)": "自動保存を有効化 (再起動後に有効)",
    (
        "Crash recovery requires autosave. If autosave is off, unsaved changes "
        "cannot be restored after a forced quit or crash."
    ): (
        "クラッシュ復元には自動保存が必要です。自動保存をオフにすると、"
        "強制終了やクラッシュ後に未保存の変更を復元できません。"
    ),
    "Autosave interval (effective after restart)": "自動保存間隔 (再起動後に有効)",
}
_JA_FALLBACK_TRANSLATIONS["InterfacePage"].update(
    {
        "Grid interval": "グリッド間隔",
    }
)
_JA_FALLBACK_TRANSLATIONS["ShortcutsPage"].update(
    {
        "Showing {shown} of {total}": "{total} 件中 {shown} 件を表示",
    }
)
_JA_FALLBACK_TRANSLATIONS["BasicMoveTool"].update(
    {
        "Pixel Perfect": "ピクセルパーフェクト",
        "Basic Move": "ベーシック移動",
        "Move tool for selection, vector objects, or active raster layer": (
            "選択範囲・ベクターオブジェクト・アクティブなラスターレイヤーを移動するツール"
        ),
    }
)
_JA_FALLBACK_TRANSLATIONS["BasicEraser"].update(
    {
        "Erasers": "消しゴム",
        "Size": "サイズ",
        "Strength": "強さ",
        "Hardness": "硬さ",
        "Spacing": "間隔",
        "Anti-alias": "アンチエイリアス",
        "Soft Erase": "ソフト消去",
        "Edge Cleanup": "エッジ調整",
    }
)
_JA_FALLBACK_TRANSLATIONS["NodeTabsWidget"].update(
    {
        "User-defined Filters": "ユーザー定義フィルター",
        "(No node name conditions)": "(ノード名条件なし)",
        "Filter Settings": "フィルター設定",
        "Add": "追加",
        "Edit": "編集",
        "Delete": "削除",
        "Close": "閉じる",
        "OK": "OK",
        "Cancel": "キャンセル",
        "Tab Name": "タブ名",
        "Node Name Keywords": "ノード名キーワード",
        "Parameter Name Keywords": "パラメーター名キーワード",
        "Value Types (image/number, etc.)": "値タイプ (image/number など)",
        "Custom": "カスタム",
        "Image Source:": "画像ソース:",
        "Current: {value}": "現在: {value}",
        "(Use workflow file)": "(ワークフローファイルを使用)",
        "File: {file_name}": "ファイル: {file_name}",
        "--- Layers ---": "--- レイヤー ---",
        "Using layer: {display}": "レイヤーを使用: {display}",
        "Selected file: {file_name}": "選択中のファイル: {file_name}",
        "Using workflow: {workflow}": "ワークフローを使用: {workflow}",
        "Apply Result To:": "結果の適用先:",
        "(Optional) Choose a layer to receive SaveImage output": (
            "(任意) SaveImage の出力先レイヤーを選択"
        ),
        "(Do not import)": "(取り込まない)",
        "Select prompt snippet...": "プロンプトスニペットを選択...",
        "Node: {node}": "ノード: {node}",
        "Optional:": "任意:",
        "Range: {min_val} to {max_val}": "範囲: {min_val} から {max_val}",
        "Integer value (min: {min_val}, max: {max_val})": (
            "整数値 (最小: {min_val}, 最大: {max_val})"
        ),
        "Sampling algorithm": "サンプリングアルゴリズム",
        "Noise scheduler": "ノイズスケジューラー",
        "Model checkpoint": "モデルチェックポイント",
        "Available models/checkpoints": "利用可能なモデル/チェックポイント",
        "Additional model components": "追加モデルコンポーネント",
        "Control seed behavior after generation": "生成後の seed 挙動を制御",
        "Upscaling algorithm": "アップスケールアルゴリズム",
        # Metadata binding UI
        "Bind to metadata": "メタデータにバインド",
        "Clear binding": "バインドを解除",
        "(no layers)": "(レイヤーなし)",
        "Layer": "レイヤー",
        "Canvas": "キャンバス",
        "Selection": "選択範囲",
        "Color": "カラー",
        "Viewport": "ビューポート",
        "Bounds X": "範囲 X",
        "Bounds Y": "範囲 Y",
        "Bounds W": "範囲 幅",
        "Bounds H": "範囲 高さ",
        "Width": "幅",
        "Height": "高さ",
        "Red": "赤",
        "Green": "緑",
        "Blue": "青",
        "X": "X",
        "Y": "Y",
    }
)
_JA_FALLBACK_TRANSLATIONS["VectorPathTool"].update(
    {
        "Shift Mode": "Shift モード",
        "Auto Close Tolerance (px)": "自動クローズ許容値 (px)",
        "Stroke Width": "線幅",
        "Stroke Color": "線の色",
        "Fill Enabled": "塗りを有効化",
        "Fill Color": "塗りの色",
        "Vector Path Tool": "ベクターパスツール",
        "Click-based vector path tool": "クリック操作でパスを作成するベクターツール",
    }
)
_JA_FALLBACK_TRANSLATIONS["VectorPathEditTool"].update(
    {
        "Stroke Color": "線の色",
        "Fill Enabled": "塗りを有効化",
        "Fill Color": "塗りの色",
        "Vector Path Edit Tool": "ベクターパス編集ツール",
        "Edit anchors/handles of vector paths": "ベクターパスのアンカーとハンドルを編集します。",
    }
)
_JA_FALLBACK_TRANSLATIONS["VectorPreciseTool"].update(
    {
        "Edit Mode": "編集モード",
        "Auto Close": "自動クローズ",
        "Stroke Width": "線幅",
        "Stroke Color": "線の色",
        "Fill Enabled": "塗りを有効化",
        "Fill Color": "塗りの色",
        "Anchor Density": "アンカー密度",
        "Handle Smoothness": "ハンドルの滑らかさ",
        "Illustrator-like precise vector pen": "Illustrator風の高精度ベクターペン",
    }
)
_JA_FALLBACK_TRANSLATIONS["BasicSelectionRectTool"].update(
    {
        "Mode": "モード",
        "Operation": "演算",
        "Pixel Perfect": "ピクセルパーフェクト",
        "Feather": "ぼかし",
        "Feather Radius": "ぼかし半径",
        "Mask Radius": "マスク半径",
        "Transform X": "変形 X",
        "Transform Y": "変形 Y",
        "Scale X": "拡大率 X",
        "Scale Y": "拡大率 Y",
        "Rotation": "回転",
        "Clipboard": "クリップボード",
        "Copy": "コピー",
        "Cut": "切り取り",
        "Paste": "貼り付け",
        "Mask Ops": "マスク操作",
        "Invert": "反転",
        "Expand": "拡張",
        "Contract": "縮小",
        "Transform": "変形",
        "Move": "移動",
        "Scale": "拡大縮小",
        "Rotate": "回転",
        "Flip H": "左右反転",
        "Flip V": "上下反転",
        "Reset": "リセット",
        "Apply": "適用",
        "Cancel": "キャンセル",
        "Basic Selection": "ベーシック選択",
        "Rectangle selection tool": "矩形選択ツール",
    }
)
_JA_FALLBACK_TRANSLATIONS["ColorPanelPlugin"] = {
    "Color Manager": "カラーマネージャー",
    "Color picker and palette management panel": "カラーピッカーとパレット管理パネル",
    "Color": "色",
}
_JA_FALLBACK_TRANSLATIONS["PagePanelPlugin"] = {
    "Insert a blank page here": "ここに白ページを挿入",
    "Insert Blank Page": "白ページの挿入",
    "Insert a blank page here?": "ここに白ページを挿入しますか?",
    "A blank page cannot be inserted before the first page. Use Add Page instead.": "先頭ページの前には挿入できません。「ページを追加」を使ってください。",
    "Include raster artwork (stored inside the template)": "ラスター画像を含める (テンプレート内に保存)",
    "Raster artwork: about {size} uncompressed.": "ラスター画像: 非圧縮で約 {size}。",
    "Large artwork bloats the template library.": "大きな画像はテンプレートライブラリを肥大化させます。",
    "Category...": "カテゴリ...",
    "Template Category": "テンプレートのカテゴリ",
    "Template category:": "テンプレートのカテゴリ:",
    "The page could not be loaded. Select it again to retry.": "ページを読み込めませんでした。もう一度選択すると再試行します。",
    "Pages Panel": "ページパネル",
    "Page management panel": "ページ管理パネル",
    "Pages": "ページ",
    # CT-06: "Binding" (綴じ方向) and physical "Side" (左右の側) stay
    # distinct in translation.  View is runtime UI state; Binding is a
    # book setting stored in the document.
    "View": "表示",
    "Single": "単ページ",
    "Spread": "見開き",
    "Binding": "綴じ方向",
    "Left Binding": "左綴じ",
    "Right Binding": "右綴じ",
    "Left Side": "左側",
    "Right Side": "右側",
    "Empty": "空き",
    "Active Page": "アクティブページ",
    "Spread {number}": "見開き{number}",
    "Spread {number}, {side}, {page}": "見開き{number}、{side}、{page}",
    "Changing the binding does not reorder pages": (
        "綴じ方向を変えてもページの並び順は変わりません"
    ),
    # CT-06 §6.2: page role context menu.
    "Page Role": "ページの役割",
    "Cover": "表紙",
    "Inside Cover": "表紙裏",
    "Title": "扉",
    "Body": "本文",
    "Colophon": "奥付",
    # CT-07 T1b: comic template section in the add-page menu.
    "Comic": "漫画",
    # CT-07 T2: template capture options + template manager.
    "Save Page Template": "ページテンプレートを保存",
    "Template name:": "テンプレート名:",
    "Include vector objects": "ベクターオブジェクトを含める",
    "Include guides": "ガイドを含める",
    "The template could not be saved. See the log for details.": (
        "テンプレートを保存できませんでした。詳細はログを確認してください。"
    ),
    "Manage Templates": "テンプレートの管理",
    "Manage Templates...": "テンプレートの管理...",
    "Rename...": "名前を変更...",
    "Rename Template": "テンプレート名の変更",
    "Duplicate": "複製",
    "Delete": "削除",
    "Delete Template": "テンプレートの削除",
    "Delete this template? This cannot be undone.": (
        "このテンプレートを削除しますか? 削除は元に戻せません。"
    ),
    "Built-in": "組み込み",
    "User": "ユーザー",
    # CT-07 T3b: master pages.
    "Manage Masters...": "マスターページの管理...",
    "Manage Masters": "マスターページの管理",
    "Create from Current Page...": "現在のページから作成...",
    "Apply": "適用",
    "All Pages": "すべてのページ",
    "Current Page": "現在のページ",
    "Target Roles": "対象の役割",
    "Update from Current Page": "現在のページから更新",
    "Unlink on Current Page": "現在のページでリンク解除",
    "Master name:": "マスター名:",
    "{name} (rev {revision}, {count} page(s))": "{name} (rev {revision}、{count} ページ)",
    "The current page has no vector layers to capture.": (
        "現在のページに取り込めるベクターレイヤーがありません。"
    ),
    "Update this master and refresh {count} page(s)?": (
        "このマスターを更新し、{count} ページの instance を更新しますか?"
    ),
    "No pages match this scope.": "対象になるページがありません。",
    "The current page has no instance of this master.": (
        "現在のページにこのマスターの instance はありません。"
    ),
    "Delete this master definition? {count} page instance(s) stay on their pages as detached content.": (
        "このマスター定義を削除しますか? {count} ページの instance は"
        "リンクの切れた内容としてページに残ります。"
    ),
    # CT-06 F3: per-page folio submenu.
    "Folio": "ノンブル",
    "Automatic": "自動",
    "Hidden": "隠す",
    "Custom...": "カスタム...",
    "Custom Folio": "カスタムノンブル",
    "Folio text:": "ノンブル文字列:",
}
_JA_FALLBACK_TRANSLATIONS["PromptEditorPanelPlugin"] = {
    "Prompt Editor": "プロンプトエディター",
    "Compose prompts, manage a library, and submit to LLM.": (
        "プロンプトを組み立て、ライブラリを管理し、LLM に送信します。"
    ),
}
_JA_FALLBACK_TRANSLATIONS["PromptEditorPanel"] = {
    "Panel ready.": "パネルの準備ができました。",
    "Compose": "作成",
    "Library": "ライブラリ",
    "LLM": "LLM",
    "Prompt": "プロンプト",
    "String": "文字列",
    "List": "リスト",
    "System Prompt (LLM)": "システムプロンプト (LLM)",
    "User Prompt (LLM)": "ユーザープロンプト (LLM)",
    "Components": "コンポーネント",
    "Prompt structure": "プロンプト構成",
    "Clear components": "コンポーネントをクリア",
    "Live prompt": "ライブプロンプト",
    "Separator": "区切り",
    "Copy": "コピー",
    "Save": "保存",
    "Save As": "名前を付けて保存",
    "Title": "タイトル",
    "Prompt title": "プロンプトタイトル",
    "Tags": "タグ",
    "Tags (comma separated)": "タグ (カンマ区切り)",
    "New": "新規",
    "Search prompts...": "プロンプトを検索...",
    "Details": "詳細",
    "Component type": "コンポーネント種別",
    "Preview": "プレビュー",
    "Duplicate": "複製",
    "Delete": "削除",
    "Export": "エクスポート",
    "Refresh": "更新",
    "LLM Settings": "LLM 設定",
    "Stream updates": "ストリーム更新",
    "Expect JSON response": "JSON 応答を期待する",
    "Provider": "プロバイダー",
    "Model": "モデル",
    "Temperature": "温度",
    "Max tokens": "最大トークン",
    "Schema": "スキーマ",
    "System prompt": "システムプロンプト",
    "Select system prompt...": "システムプロンプトを選択...",
    "Optional system instructions...": "任意のシステム指示...",
    "User prompt": "ユーザープロンプト",
    "Select user prompt...": "ユーザープロンプトを選択...",
    "Enter user prompt...": "ユーザープロンプトを入力...",
    "Submit": "送信",
    "Cancel": "キャンセル",
    "Clear": "クリア",
    "Output": "出力",
    "Delete prompt": "プロンプトを削除",
    "Export prompt": "プロンプトをエクスポート",
    "Text Files (*.txt);;All Files (*.*)": "テキストファイル (*.txt);;すべてのファイル (*.*)",
    "Save failed: {error}": "保存に失敗しました: {error}",
    "Saved '{title}'.": "'{title}' を保存しました。",
    "No prompt selected.": "プロンプトが選択されていません。",
    "Duplicated '{title}'.": "'{title}' を複製しました。",
    "Export failed: {error}": "エクスポートに失敗しました: {error}",
    "Exported to {path}.": "{path} にエクスポートしました。",
    "Created '{title}'.": "'{title}' を作成しました。",
    "LLM gateway unavailable.": "LLM ゲートウェイを利用できません。",
    "Failed to load providers: {error}": "プロバイダーの読み込みに失敗しました: {error}",
    "Failed to load models: {error}": "モデルの読み込みに失敗しました: {error}",
    "User prompt is empty.": "ユーザープロンプトが空です。",
    "Submission failed: {error}": "送信に失敗しました: {error}",
    "Submitted job {job_id}: {prompt}": "ジョブ {job_id} を送信しました: {prompt}",
    "Received streaming output.": "ストリーミング出力を受信しました。",
    "Streaming failed: {error}": "ストリーミングに失敗しました: {error}",
    "Result retrieval failed: {error}": "結果の取得に失敗しました: {error}",
    "Received response from LLM.": "LLM からの応答を受信しました。",
    "LLM response did not include text.": "LLM 応答にテキストが含まれていません。",
    "No active job to cancel.": "キャンセルできるジョブがありません。",
    "Cancel failed: {error}": "キャンセルに失敗しました: {error}",
    "Cancelled job {job_id}.": "ジョブ {job_id} をキャンセルしました。",
    "Cleared prompts.": "プロンプトをクリアしました。",
    "Failed to refresh snippets.": "スニペットの更新に失敗しました。",
    "Loaded prompt '{title}'.": "プロンプト '{title}' を読み込みました。",
    "Prompt is empty.": "プロンプトが空です。",
    "Saved prompt '{title}'.": "プロンプト '{title}' を保存しました。",
    "Deleted prompt '{title}'.": "プロンプト '{title}' を削除しました。",
    "Prompt copied to clipboard.": "プロンプトをクリップボードにコピーしました。",
    "Prompt composed and emitted.": "プロンプトを生成して送信しました。",
    "Drag components to build a prompt.": "コンポーネントをドラッグしてプロンプトを組み立てます。",
}
_JA_FALLBACK_TRANSLATIONS["PromptComponentWidget"] = {
    "All tags": "すべてのタグ",
    "String snippet": "文字列スニペット",
    "List item": "リスト項目",
    "Inline text": "インラインテキスト",
    "Prompt reference": "プロンプト参照",
    "Remove component": "コンポーネントを削除",
    "Tag": "タグ",
    "Snippet": "スニペット",
    "Enter text...": "テキストを入力...",
}
_JA_FALLBACK_TRANSLATIONS["ComponentPaletteWidget"] = {
    "Add {label} component": "{label} コンポーネントを追加",
    "String": "文字列",
    "List": "リスト",
    "Text": "テキスト",
    "Prompt": "プロンプト",
}
_JA_FALLBACK_TRANSLATIONS["NanoBananaPanelPlugin"] = {
    "NanoBanana Panel": "NanoBanana パネル",
    "Configure NanoBanana Gemini jobs": "NanoBanana Gemini ジョブを設定します。",
    "NanoBanana": "NanoBanana",
    "Service unavailable": "サービスを利用できません",
    "Job {job_id} submitted": "ジョブ {job_id} を送信しました",
    "{step}/{total}: {message}": "{step}/{total}: {message}",
    "Job completed": "ジョブが完了しました",
    "Job failed": "ジョブが失敗しました",
    "Error: {error}": "エラー: {error}",
}
_JA_FALLBACK_TRANSLATIONS["NanoBananaPanel"] = {
    "Nothing": "なし",
    "<New Layer>": "<新規レイヤー>",
    "<No prompts found>": "<プロンプトが見つかりません>",
    "Input Layers (select up to {count}):": "入力レイヤー (最大 {count}):",
    "Prompt is required": "プロンプトは必須です",
    "Submitted job to queue": "ジョブをキューに送信しました",
    "Using prompt: {title}": "使用中のプロンプト: {title}",
    "Text+Image": "テキスト+画像",
    "Image": "画像",
    "{aspect} ({width}x{height})": "{aspect} ({width}x{height})",
    (
        "External AI services are disabled. Enable 'Allow external AI services' "
        "in Preferences to generate."
    ): (
        "外部 AI サービスは無効です。生成するには Preferences で"
        "「Allow external AI services」を有効にしてください。"
    ),
    "Prompt (from Prompt Editor):": "プロンプト (Prompt Editor から):",
    "Refresh": "更新",
    "Selected prompt content...": "選択したプロンプトの内容...",
    "Response modality": "応答モード",
    "Aspect ratio": "アスペクト比",
    "Target layer": "出力先レイヤー",
    "Generate": "生成",
    "Status messages will appear here": "ステータスメッセージがここに表示されます",
}
_JA_FALLBACK_TRANSLATIONS["GPTImage2PanelPlugin"] = {
    "GPT Image 2 Panel": "GPT Image 2 パネル",
    "Configure GPT Image 2 jobs": "GPT Image 2 ジョブを設定します",
    "GPT Image 2": "GPT Image 2",
    "Service unavailable": "サービスを利用できません",
    "Job {job_id} submitted": "ジョブ {job_id} を送信しました",
    "{step}/{total}: {message}": "{step}/{total}: {message}",
    "Job completed": "ジョブが完了しました",
    "Job failed": "ジョブが失敗しました",
    "Error: {error}": "エラー: {error}",
}
_JA_FALLBACK_TRANSLATIONS["GPTImage2Panel"] = {
    "<New Layer>": "<新規レイヤー>",
    "Prompt is required": "プロンプトは必須です",
    "Submitted job to queue": "ジョブをキューに送信しました",
    "Auto": "自動",
    "Low": "低",
    "Medium": "中",
    "High": "高",
    "Opaque": "不透明",
    "PNG": "PNG",
    "JPEG": "JPEG",
    "WebP": "WebP",
    (
        "External AI services are disabled. Enable 'Allow external AI services' "
        "in Preferences to generate."
    ): (
        "外部 AI サービスは無効です。生成するには Preferences で"
        "「Allow external AI services」を有効にしてください。"
    ),
    "Prompt:": "プロンプト:",
    "Enter prompt text...": "プロンプトを入力...",
    "Model": "モデル",
    "Quality": "品質",
    "Size": "サイズ",
    "Format": "形式",
    "Background": "背景",
    "Moderation": "モデレーション",
    "Compression": "圧縮",
    "Target layer": "出力先レイヤー",
    "Generate": "生成",
    "Status messages will appear here": "ステータスメッセージがここに表示されます",
}
_JA_FALLBACK_TRANSLATIONS["ToolFlyoutButton"] = {
    "Shortcuts: {shortcuts}": "ショートカット: {shortcuts}",
    "Group: {label}": "グループ: {label}",
}
_JA_FALLBACK_TRANSLATIONS["ToolToolbar"] = {
    "Tool manager not available": "ツールマネージャーを利用できません",
    "No tool plugins registered": "ツールプラグインが登録されていません",
    "No tools available": "利用可能なツールがありません",
}
_JA_FALLBACK_TRANSLATIONS["ToolPropertiesPanel"] = {
    "Preset:": "プリセット:",
    "Save": "保存",
    "Delete": "削除",
    "More": "その他",
    "Common Properties": "共通プロパティ",
    "Tool Properties": "ツールプロパティ",
    "Select a tool to edit properties": "ツールを選択してプロパティを編集",
    "Preset name": "プリセット名",
    "Name:": "名前:",
    "Size": "サイズ",
    "Stamp Density": "スタンプ密度",
    "Opacity": "不透明度",
    "Hardness": "硬さ",
    "Spacing": "間隔",
    "Pressure Sensitive": "筆圧感知",
    "Flow": "流量",
    "Tone Range": "トーン範囲",
    "Tone Gamma": "トーンガンマ",
    "Stabilization": "手ブレ補正",
    "Tilt Shading": "傾きシェーディング",
    "Strength": "強さ",
    "Anti alias": "アンチエイリアス",
    "Anti-alias": "アンチエイリアス",
    "Tolerance": "許容値",
    "Sample": "サンプル",
    "Contiguous": "隣接のみ",
    "Mode": "モード",
    "Operation": "演算",
    "Feather": "ぼかし",
    "Pixel Perfect": "ピクセルパーフェクト",
    "Feather Radius": "ぼかし半径",
    "Mask Radius": "マスク半径",
    "Transform X": "変形 X",
    "Transform Y": "変形 Y",
    "Scale X": "拡大率 X",
    "Scale Y": "拡大率 Y",
    "Rotation": "回転",
    "Color Tolerance": "色許容値",
    "Gap Close (px)": "隙間閉じ (px)",
    "Fill Target": "塗りつぶし対象",
    "Current Layer": "現在のレイヤー",
    "All Layers": "すべてのレイヤー",
    "Merged": "結合結果",
    "Active Layer": "アクティブレイヤー",
    "New Layer": "新規レイヤー",
    "Text Mode": "テキストモード",
    "Font Size (pt)": "フォントサイズ (pt)",
    "Paragraph Flow": "段落フロー",
    "Line Height": "行間",
    "Tracking": "文字間",
    "Convert": "変換",
    "Selection To Area": "選択範囲をエリアテキストに変換",
    "point": "ポイントテキスト",
    "area": "エリアテキスト",
    "left": "左方向",
    "right": "右方向",
    "Rectangle": "矩形",
    "Ellipse": "楕円",
    "Lasso": "投げ縄",
    "Magic Wand": "自動選択",
    "Replace": "置換",
    "Add": "追加",
    "Subtract": "減算",
    "Intersect": "交差",
    "Edit Mode": "編集モード",
    "Auto Close": "自動閉合",
    "Shift Mode": "シフトモード",
    "Auto Close Tolerance (px)": "自動閉合許容値 (px)",
    "Stroke Width": "線幅",
    "Stroke Color": "線色",
    "Fill Enabled": "塗りつぶし有効",
    "Fill Color": "塗り色",
    "Anchor Density": "アンカー密度",
    "Handle Smoothness": "ハンドル滑らかさ",
    "constraint": "制約",
    "disable_snap": "スナップ無効",
    "anchor": "アンカー",
    "shape": "シェイプ",
    "Clipboard": "クリップボード",
    "Copy": "コピー",
    "Cut": "切り取り",
    "Paste": "貼り付け",
    "Mask Ops": "マスク操作",
    "Invert": "反転",
    "Expand": "拡張",
    "Contract": "縮小",
    "Transform": "変形",
    "Move": "移動",
    "Scale": "拡大縮小",
    "Rotate": "回転",
    "Flip H": "左右反転",
    "Flip V": "上下反転",
    "Reset": "リセット",
    "Apply": "適用",
    "Cancel": "キャンセル",
}
_JA_FALLBACK_TRANSLATIONS["ComfyUIPlugin"] = {
    "ComfyUI Panel": "ComfyUI パネル",
    "ComfyUI integration panel for AI image generation": "AI画像生成のための ComfyUI 連携パネル",
    "ComfyUI": "ComfyUI",
}
_JA_FALLBACK_TRANSLATIONS["TextEditorPanelPlugin"] = {
    "Text Editor": "テキストエディター",
    "Prompt authoring panel with snippet and LLM integration": (
        "スニペットと LLM 連携に対応したプロンプト編集パネル"
    ),
}
_JA_FALLBACK_TRANSLATIONS["ConnectionBar"] = {
    "Disconnected": "未接続",
    "Connecting...": "接続中...",
    "Connected": "接続済み",
    "Connection failed": "接続に失敗しました",
    "Queue: {count}": "キュー: {count}",
    "Connect": "接続",
    "Disconnect": "切断",
    "Server": "サーバー",
    "Choose a ComfyUI server configured in Preferences.": (
        "Preferences で設定した ComfyUI サーバーを選択します。"
    ),
    "Refresh object info": "オブジェクト情報を更新",
    "External access disabled": "外部アクセスが無効です",
}
_JA_FALLBACK_TRANSLATIONS["QueuePanel"] = {
    "Job Queue": "ジョブキュー",
    "ID": "ID",
    "Status": "状態",
    "Progress": "進行状況",
    "Workflow": "ワークフロー",
    "Started": "開始",
    "Actions": "操作",
    "Cancel": "キャンセル",
    "Preview": "プレビュー",
    "Clear Finished": "完了分を消去",
    "Clear All": "すべて消去",
    "{pending} pending, {running} running": "{pending} 件待機中, {running} 件実行中",
    "Pending": "待機中",
    "Running": "実行中",
    "Completed": "完了",
    "Failed": "失敗",
    "Cancelled": "キャンセル済み",
    "Cancel job": "ジョブをキャンセル",
    "Preview result": "結果をプレビュー",
    "Unnamed": "名称未設定",
}
_JA_FALLBACK_TRANSLATIONS["ComfyUIMainPanel"] = {
    "Workflow": "ワークフロー",
    "Preview for job {job_id} is not available yet.": (
        "ジョブ {job_id} のプレビューはまだ利用できません。"
    ),
    "Cleared {count} finished jobs": "完了したジョブを {count} 件消去しました",
    "No finished jobs to clear": "消去できる完了ジョブはありません",
    "Connected to ComfyUI": "ComfyUI に接続しました",
    "Disconnected from ComfyUI": "ComfyUI から切断しました",
    "Load ComfyUI Workflow": "ComfyUI ワークフローを読み込む",
    "JSON Files (*.json);;All Files (*)": "JSON ファイル (*.json);;すべてのファイル (*)",
    "Job {job_id} cancelled": "ジョブ {job_id} をキャンセルしました",
    "Job {job_id} failed": "ジョブ {job_id} が失敗しました",
    "{message}: {error}": "{message}: {error}",
    "Job {job_id} completed": "ジョブ {job_id} が完了しました",
    "Queued job: {job_id}...": "ジョブをキューに追加しました: {job_id}...",
    "Pending": "待機中",
    "Running": "実行中",
    "Completed": "完了",
    "Failed": "失敗",
    "Cancelled": "キャンセル済み",
}
_JA_FALLBACK_TRANSLATIONS["_DetailWidget"] = {
    "Regenerate": "再生成",
    "Restore": "復元",
    "Copy to Nodes": "ノードにコピー",
    "Delete": "削除",
    "Transfer to layer:": "レイヤーへ転送:",
    "(New Layer)": "(新規レイヤー)",
    "Transfer": "転送",
    "No image": "画像なし",
    "Service": "サービス",
    "Model": "モデル",
    "Prompt": "プロンプト",
    "Seed": "シード",
    "Size": "サイズ",
    "Duration": "所要時間",
    "Status": "ステータス",
    "Time": "時刻",
    "Error": "エラー",
}
_JA_FALLBACK_TRANSLATIONS["GalleryPanel"] = {
    "All": "すべて",
    "Completed": "完了",
    "Failed": "失敗",
    "Cancelled": "キャンセル済み",
    "All Services": "すべてのサービス",
    "Delete Record": "レコードを削除",
    "Delete this generation record?": "この生成レコードを削除しますか?",
    "Warning": "警告",
    "No image available": "利用可能な画像がありません",
    "Image file not found": "画像ファイルが見つかりません",
    "Overwrite Layer": "レイヤーを上書き",
    "Overwrite the selected layer with this image?": "選択したレイヤーをこの画像で上書きしますか?",
    "Don't ask again": "次回から確認しない",
    "Error": "エラー",
    "Layer import not available": "レイヤーインポートは利用できません",
}
_JA_FALLBACK_TRANSLATIONS["ProductionControlPanel"] = {
    "Refresh": "更新",
    "Overview": "概要",
    "Catalog": "カタログ",
    "Artifacts": "成果物",
    "Jobs": "ジョブ",
    "Bible": "Bible",
    "Visual QA": "Visual QA",
    "Export": "書き出し",
    "Recipes": "レシピ",
    "Features": "機能",
    "AI Effect presets": "AI Effect プリセット",
    "Bible entries": "Bible 項目",
    "Visual QA reports": "Visual QA レポート",
    "Export profiles": "書き出しプロファイル",
    "Store root": "保存先",
    "External services": "外部サービス",
    "MCP runtime is not available.": "MCP runtime が利用できません。",
    "Refresh failed: {error}": "更新に失敗しました: {error}",
    "Updated from local Production Control store.": (
        "ローカルの Production Control store から更新しました。"
    ),
    "Feature": "機能",
    "Gateway": "Gateway",
    "Preset": "プリセット",
    "{count} presets": "{count} 件のプリセット",
    "Kind": "種類",
    "ID": "ID",
    "Name": "名前",
    "Status": "状態",
    "Details": "詳細",
    "Artifact": "成果物",
    "Source": "ソース",
    "Size": "サイズ",
    "Job": "ジョブ",
    "Class": "分類",
    "Progress": "進捗",
    "Updated": "更新日時",
    "Scope": "スコープ",
    "Scope ID": "スコープ ID",
    "Entry": "項目",
    "Title": "タイトル",
    "Report": "レポート",
    "Checks": "チェック",
    "Created": "作成日時",
    "Profile": "プロファイル",
    "Root": "ルート",
    "Format": "形式",
    "Confirmation": "確認",
    "Recipe": "レシピ",
    "Lineage": "リネージ",
    "Edge": "エッジ",
    "From": "生成元",
    "To": "生成先",
    "Artifact/Job": "成果物/ジョブ",
}
_JA_FALLBACK_TRANSLATIONS["StartPagePanel"] = {
    "Start a new document or open an existing file.": (
        "新しいドキュメントを作成するか、既存のファイルを開きます。"
    ),
    "New Document...": "新規ドキュメント...",
    "Open...": "開く...",
    "Recent": "最近使用したファイル",
    "No recent files": "最近使用したファイルはありません",
}
_JA_FALLBACK_TRANSLATIONS["TextEditorPanelPlugin"].update(
    {
        "Status messages will appear here...": "ステータスメッセージがここに表示されます...",
        "New": "新規",
        "Duplicate": "複製",
        "Delete": "削除",
        "Favorite": "お気に入り",
        "Remove": "削除",
        "Layer": "レイヤー",
        "Edit Snippet": "スニペットを編集",
        "New Snippet": "新規スニペット",
        "Text": "テキスト",
        "List": "リスト",
        "Prompt": "プロンプト",
        "System Prompt": "システムプロンプト",
        "User Prompt": "ユーザープロンプト",
        "Title": "タイトル",
        "Tags": "タグ",
        "Kind": "種類",
        "Body": "本文",
        "Untitled": "無題",
        "Textbox": "テキストボックス",
        "Add {label} component": "{label} コンポーネントを追加",
        "Save Prompt Snippet": "プロンプトをスニペット保存",
        "Stream to Job Queue": "ジョブキューへストリーム送信",
        "Expect JSON response": "JSON 応答を期待する",
        "Dry Run": "ドライラン",
        "Clear": "クリア",
        "Provider": "プロバイダー",
        "Model": "モデル",
        "Temperature": "温度",
        "Max tokens": "最大トークン",
        "Schema": "スキーマ",
        "Submission": "送信",
        "Generated Prompt": "生成されたプロンプト",
        "Prompt Components": "プロンプトコンポーネント",
        "Palette": "パレット",
        "Structure": "構成",
        "Status": "状態",
        "No LLM output to apply.": "適用できる LLM 出力がありません。",
        "LLM output copied to editor.": "LLM 出力をエディターへコピーしました。",
        "Snippet selected: {title}": "選択したスニペット: {title}",
        "Snippet selected: None": "選択したスニペット: なし",
        "Snippet saved: {title}": "スニペットを保存しました: {title}",
        "Snippet updated: {title}": "スニペットを更新しました: {title}",
        "Delete snippet": "スニペットを削除",
        "Delete '{title}'?": "'{title}' を削除しますか?",
        "Snippet deleted: {title}": "スニペットを削除しました: {title}",
        "Cannot save prompt without components": (
            "コンポーネントがないためプロンプトを保存できません"
        ),
        "Cannot save empty prompt snippet": "空のプロンプトスニペットは保存できません",
        "Prompt saved as snippet: {title}": "プロンプトをスニペット保存しました: {title}",
        "Attachment limit reached": "添付上限に達しました",
        "Failed to attach {path}: {error}": "{path} の添付に失敗しました: {error}",
        "Layer attachment failed: {error}": "レイヤー添付に失敗しました: {error}",
        (
            "External AI services are disabled. "
            "Enable 'Allow external AI services' in Preferences before submitting."
        ): (
            "外部 AI サービスは無効です。送信前に Preferences で"
            "「Allow external AI services」を有効にしてください。"
        ),
        "Prompt submission failed: {error}": "プロンプト送信に失敗しました: {error}",
        "Submitted job {job_id}: {prompt}": "ジョブ {job_id} を送信しました: {prompt}",
        "Missing placeholders: {placeholders}": "不足しているプレースホルダー: {placeholders}",
        "Cleared prompt and attachments": "プロンプトと添付をクリアしました",
        "Snippet title": "スニペットタイトル",
        "Enter prompt text...": "プロンプトを入力...",
        "Stream updates": "ストリーム更新",
        "Enter prompt for LLM...": "LLM 用プロンプトを入力...",
        "Submit": "送信",
        "LLM Output": "LLM 出力",
        "Generated prompt will appear here...": "生成されたプロンプトがここに表示されます...",
        "Use Output In Prompt": "出力をプロンプトに使用",
        "Snippets": "スニペット",
        "tag1, tag2": "tag1, tag2",
        "Component": "コンポーネント",
        "String": "文字列",
        "System Prompt (LLM)": "システムプロンプト (LLM)",
        "User Prompt (LLM)": "ユーザープロンプト (LLM)",
        "LLM": "LLM",
        "LLM Prompt": "LLM プロンプト",
        "LLM gateway unavailable; submission disabled.": (
            "LLM ゲートウェイが利用できないため送信を無効化しました。"
        ),
        "Failed to load snippets.": "スニペットの読み込みに失敗しました。",
        "Loaded snippet: {title}": "スニペットを読み込みました: {title}",
        "Ready for new snippet.": "新規スニペットを作成できます。",
        "Failed to delete snippet.": "スニペットの削除に失敗しました。",
        "Snippet deleted.": "スニペットを削除しました。",
        "Cannot save empty prompt.": "空のプロンプトは保存できません。",
        "Failed to save snippet.": "スニペットの保存に失敗しました。",
        "Autosaved: {title}": "自動保存しました: {title}",
        "LLM gateway unavailable.": "LLM ゲートウェイを利用できません。",
        "LLM prompt is empty.": "LLM プロンプトが空です。",
        "Submitted job {job_id}.": "ジョブ {job_id} を送信しました。",
        "Received streaming output.": "ストリーミング出力を受信しました。",
        "Received response from LLM.": "LLM からの応答を受信しました。",
        "LLM response did not include text.": "LLM 応答にテキストが含まれていません。",
        "Submission failed: {error}": "送信に失敗しました: {error}",
        "Streaming failed: {error}": "ストリーミングに失敗しました: {error}",
        "Result retrieval failed: {error}": "結果の取得に失敗しました: {error}",
    }
)
_JA_FALLBACK_TRANSLATIONS["PluginManagerDialog"] = {
    "Plugin Manager": "プラグインマネージャー",
    "Import...": "インポート...",
    "Enable": "有効化",
    "Disable": "無効化",
    "Update...": "更新...",
    "Remove": "削除",
    "Rollback...": "ロールバック...",
    "Refresh": "更新",
    "Plugin ID": "プラグイン ID",
    "Display Name": "表示名",
    "Types": "種類",
    "Enabled": "有効",
    "Disabled": "無効",
    "Version": "バージョン",
    "Source": "ソース",
    "Error": "エラー",
    "Close": "閉じる",
    "Built-in": "内蔵",
    "External": "外部",
    "{count} plugin(s) loaded": "{count} 個のプラグインを読み込みました",
    "Import Plugin Package": "プラグインパッケージをインポート",
    "Update Plugin Package": "プラグインパッケージを更新",
    "Plugin ZIP (*.zip);;All Files (*.*)": "プラグイン ZIP (*.zip);;すべてのファイル (*.*)",
    "Import completed.": "インポートが完了しました。",
    "Import requested.": "インポートを要求しました。",
    "Import requested: {path}": "インポートを要求しました: {path}",
    "Import cancelled.": "インポートをキャンセルしました。",
    "Update requested ({plugin_id}): {path}": "更新を要求しました ({plugin_id}): {path}",
    "Update requested: {plugin_id}": "更新を要求しました: {plugin_id}",
    "Update requested.": "更新を要求しました。",
    "Update cancelled.": "更新をキャンセルしました。",
    "No plugin selected.": "プラグインが選択されていません。",
    "Confirm Remove": "削除の確認",
    "Type '{plugin_id}' to confirm removal:": (
        "削除を確認するには '{plugin_id}' と入力してください:"
    ),
    "Remove cancelled.": "削除をキャンセルしました。",
    "Confirm Rollback": "ロールバックの確認",
    "Type '{plugin_id}' to confirm rollback:": (
        "ロールバックを確認するには '{plugin_id}' と入力してください:"
    ),
    "Rollback cancelled.": "ロールバックをキャンセルしました。",
    "Enabled: {plugin_id}": "有効化しました: {plugin_id}",
    "Plugin already enabled: {plugin_id}": "すでに有効です: {plugin_id}",
    "Enable may be pending: {plugin_id}": "有効化を処理中の可能性があります: {plugin_id}",
    "Enable failed: {plugin_id}": "有効化に失敗しました: {plugin_id}",
    "Enable failed: plugin {plugin_id} not found.": (
        "有効化に失敗しました: プラグイン {plugin_id} が見つかりません。"
    ),
    "Disabled: {plugin_id}": "無効化しました: {plugin_id}",
    "Plugin already disabled: {plugin_id}": "すでに無効です: {plugin_id}",
    "Disable may be pending: {plugin_id}": "無効化を処理中の可能性があります: {plugin_id}",
    "Disable failed: plugin {plugin_id} not found.": (
        "無効化に失敗しました: プラグイン {plugin_id} が見つかりません。"
    ),
    "Removed: {plugin_id}": "削除しました: {plugin_id}",
    "Remove failed: {plugin_id} still exists.": (
        "削除に失敗しました: {plugin_id} がまだ存在します。"
    ),
    "Remove may be pending: {plugin_id}": "削除を処理中の可能性があります: {plugin_id}",
    "Plugin {plugin_id} not found.": "プラグイン {plugin_id} が見つかりません。",
    "Update completed: {plugin_id}": "更新が完了しました: {plugin_id}",
    "Update completed without version change: {plugin_id} (version {version})": (
        "更新は完了しましたがバージョン変更はありません: {plugin_id} (バージョン {version})"
    ),
    "Update may be pending: {plugin_id}": "更新を処理中の可能性があります: {plugin_id}",
    "Update failed: {plugin_id} not found.": "更新に失敗しました: {plugin_id} が見つかりません。",
    "Rollback unavailable: no backup for {plugin_id}": (
        "{plugin_id} のロールバック用バックアップがありません。"
    ),
    "Rollback completed: {plugin_id}": "ロールバックが完了しました: {plugin_id}",
    "Rollback failed: backup still exists for {plugin_id}": (
        "ロールバックに失敗しました: {plugin_id} のバックアップがまだ残っています。"
    ),
    "Rollback may be pending: {plugin_id}": "ロールバックを処理中の可能性があります: {plugin_id}",
    "No rollback backup for {plugin_id}": "{plugin_id} のロールバック用バックアップがありません。",
    "{action} requested": "{action} を要求しました",
    "{action} requested: {plugin_id}": "{action} を要求しました: {plugin_id}",
}
_JA_FALLBACK_TRANSLATIONS["PluginCompatibilityDialog"] = {
    "Plugin Installation - {name}": "プラグインのインストール - {name}",
    "Plugin Information": "プラグイン情報",
    "Version: {version}": "バージョン: {version}",
    "Author: {author}": "作者: {author}",
    "Validation Status": "検証状態",
    "<b>Warnings:</b>": "<b>警告:</b>",
    "Required Permissions": "必要な権限",
    "This plugin requires the following permissions to function:": (
        "このプラグインを動作させるには次の権限が必要です:"
    ),
    "File System Access": "ファイルシステムアクセス",
    "Read and write files on your computer": "コンピューター上のファイルを読み書きします",
    "Network Access": "ネットワークアクセス",
    "Connect to internet services": "インターネットサービスに接続します",
    "Allowed network domains:": "許可されたネットワークドメイン:",
    "GPU Acceleration": "GPU アクセラレーション",
    "Use GPU for performance optimization": "パフォーマンス最適化のために GPU を使用します",
    "Compatibility": "互換性",
    "Engine requirement: {engine_version}": "エンジン要件: {engine_version}",
    "Python requirement: {python_version}": "Python 要件: {python_version}",
    "Cannot Install": "インストールできません",
    "Install": "インストール",
    "Unknown Status": "不明な状態",
    "✅ Valid": "✅ 有効",
    "❌ Incompatible Version": "❌ バージョン非互換",
    "❌ Invalid Manifest": "❌ マニフェスト不正",
    "❌ Missing Dependencies": "❌ 依存関係不足",
    "⚠️ Permissions Required": "⚠️ 権限が必要",
    "❌ Invalid Signature": "❌ 署名が無効",
    "⚠️ Untrusted Source": "⚠️ 信頼されていない配布元",
    "Network access requires at least one allowed domain": (
        "ネットワークアクセスには少なくとも 1 つの許可ドメインが必要です"
    ),
    "File system access is required": "ファイルシステムアクセスが必要です",
    "Network access is required": "ネットワークアクセスが必要です",
    "Permission Required": "権限が必要です",
    "Please grant the required permissions to install this plugin.": (
        "このプラグインをインストールするには必要な権限を許可してください。"
    ),
}
_JA_FALLBACK_TRANSLATIONS["PluginUpdateDialog"] = {
    "Plugin Updates Available": "利用可能なプラグイン更新",
    "plugin update(s) available": "件のプラグイン更新があります",
    "Select All": "すべて選択",
    "Select None": "選択解除",
    "Update Selected": "選択した項目を更新",
    "No Updates Selected": "更新対象が選択されていません",
    "Please select at least one plugin to update.": (
        "更新するプラグインを少なくとも 1 つ選択してください。"
    ),
}

_RUNTIME_FALLBACK_TRANSLATIONS: dict[str, dict[str, dict[str, str]]] = {
    "ja": _JA_FALLBACK_TRANSLATIONS,
    "zh_CN": _panel_and_layer_menu_fallbacks(
        brush_manager="画笔管理器",
        layers="图层",
        preview="预览",
        toolbar="工具栏",
        pages="页面",
        color="颜色",
        font="字体",
        shape_manager="形状管理器",
        prompt_editor="提示编辑器",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="图库",
        new_layer="新建图层",
        duplicate="复制图层",
        delete="删除图层",
        merge_down="向下合并",
        merge_visible="合并可见图层",
        group_layers="编组图层",
        arrange="排列",
        bring_to_front="置于顶层",
        bring_forward="前移一层",
        send_backward="后移一层",
        send_to_back="置于底层",
        rasterize="栅格化",
        last_filter="上次滤镜",
        not_implemented="尚未实现",
    ),
    "zh_Hant": _panel_and_layer_menu_fallbacks(
        brush_manager="筆刷管理器",
        layers="圖層",
        preview="預覽",
        toolbar="工具列",
        pages="頁面",
        color="顏色",
        font="字體",
        shape_manager="形狀管理器",
        prompt_editor="提示編輯器",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="圖庫",
        new_layer="新增圖層",
        duplicate="複製圖層",
        delete="刪除圖層",
        merge_down="向下合併",
        merge_visible="合併可見圖層",
        group_layers="群組圖層",
        arrange="排列",
        bring_to_front="置於頂層",
        bring_forward="前移一層",
        send_backward="後移一層",
        send_to_back="置於底層",
        rasterize="點陣化",
        last_filter="上次濾鏡",
        not_implemented="尚未實作",
    ),
    "zh_TW": _panel_and_layer_menu_fallbacks(
        brush_manager="筆刷管理器",
        layers="圖層",
        preview="預覽",
        toolbar="工具列",
        pages="頁面",
        color="顏色",
        font="字型",
        shape_manager="形狀管理器",
        prompt_editor="提示編輯器",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="圖庫",
        new_layer="新增圖層",
        duplicate="複製圖層",
        delete="刪除圖層",
        merge_down="向下合併",
        merge_visible="合併可見圖層",
        group_layers="群組圖層",
        arrange="排列",
        bring_to_front="置於頂層",
        bring_forward="前移一層",
        send_backward="後移一層",
        send_to_back="置於底層",
        rasterize="點陣化",
        last_filter="上次濾鏡",
        not_implemented="尚未實作",
    ),
    "ko": _panel_and_layer_menu_fallbacks(
        brush_manager="브러시 관리자",
        layers="레이어",
        preview="미리보기",
        toolbar="도구 모음",
        pages="페이지",
        color="색상",
        font="글꼴",
        shape_manager="도형 관리자",
        prompt_editor="프롬프트 편집기",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="갤러리",
        new_layer="새 레이어",
        duplicate="레이어 복제",
        delete="레이어 삭제",
        merge_down="아래 레이어와 병합",
        merge_visible="보이는 레이어 병합",
        group_layers="레이어 그룹",
        arrange="순서",
        bring_to_front="맨 앞으로",
        bring_forward="앞으로 가져오기",
        send_backward="뒤로 보내기",
        send_to_back="맨 뒤로",
        rasterize="래스터화",
        last_filter="마지막 필터",
        not_implemented="아직 구현되지 않음",
    ),
    "es": _panel_and_layer_menu_fallbacks(
        brush_manager="Administrador de pinceles",
        layers="Capas",
        preview="Vista previa",
        toolbar="Barra de herramientas",
        pages="Páginas",
        color="Color",
        font="Fuente",
        shape_manager="Administrador de formas",
        prompt_editor="Editor de prompts",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="Galeria",
        new_layer="Nueva capa",
        duplicate="Duplicar capa",
        delete="Eliminar capa",
        merge_down="Combinar hacia abajo",
        merge_visible="Combinar visibles",
        group_layers="Agrupar capas",
        arrange="Organizar",
        bring_to_front="Traer al frente",
        bring_forward="Adelantar",
        send_backward="Enviar atrás",
        send_to_back="Enviar al fondo",
        rasterize="Rasterizar",
        last_filter="Último filtro",
        not_implemented="No implementado",
    ),
    "ar": _panel_and_layer_menu_fallbacks(
        brush_manager="مدير الفراشي",
        layers="الطبقات",
        preview="معاينة",
        toolbar="شريط الأدوات",
        pages="الصفحات",
        color="اللون",
        font="الخط",
        shape_manager="مدير الأشكال",
        prompt_editor="محرر المطالبات",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="معرض",
        new_layer="طبقة جديدة",
        duplicate="تكرار الطبقة",
        delete="حذف الطبقة",
        merge_down="دمج لأسفل",
        merge_visible="دمج المرئي",
        group_layers="تجميع الطبقات",
        arrange="ترتيب",
        bring_to_front="إحضار إلى الأمام",
        bring_forward="تقديم للأمام",
        send_backward="إرسال إلى الخلف",
        send_to_back="إرسال إلى الخلف تمامًا",
        rasterize="تحويل إلى نقطي",
        last_filter="آخر مرشح",
        not_implemented="غير مطبق بعد",
    ),
    "hi": _panel_and_layer_menu_fallbacks(
        brush_manager="ब्रश प्रबंधक",
        layers="परतें",
        preview="पूर्वावलोकन",
        toolbar="टूलबार",
        pages="पृष्ठ",
        color="रंग",
        font="फ़ॉन्ट",
        shape_manager="आकार प्रबंधक",
        prompt_editor="प्रॉम्प्ट संपादक",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="गैलरी",
        new_layer="नई लेयर",
        duplicate="लेयर डुप्लिकेट करें",
        delete="लेयर हटाएं",
        merge_down="नीचे वाली लेयर में मर्ज करें",
        merge_visible="दृश्य लेयर मर्ज करें",
        group_layers="लेयर समूहित करें",
        arrange="क्रम",
        bring_to_front="सबसे आगे लाएँ",
        bring_forward="एक स्तर आगे लाएँ",
        send_backward="एक स्तर पीछे भेजें",
        send_to_back="सबसे पीछे भेजें",
        rasterize="रास्टराइज़ करें",
        last_filter="पिछला फ़िल्टर",
        not_implemented="अभी लागू नहीं",
    ),
    "pt_BR": _panel_and_layer_menu_fallbacks(
        brush_manager="Gerenciador de pincéis",
        layers="Camadas",
        preview="Visualização",
        toolbar="Barra de ferramentas",
        pages="Páginas",
        color="Cor",
        font="Fonte",
        shape_manager="Gerenciador de formas",
        prompt_editor="Editor de prompts",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="Galeria",
        new_layer="Nova camada",
        duplicate="Duplicar camada",
        delete="Excluir camada",
        merge_down="Mesclar para baixo",
        merge_visible="Mesclar visíveis",
        group_layers="Agrupar camadas",
        arrange="Organizar",
        bring_to_front="Trazer para frente",
        bring_forward="Avançar",
        send_backward="Enviar para trás",
        send_to_back="Enviar para o fundo",
        rasterize="Rasterizar",
        last_filter="Último filtro",
        not_implemented="Ainda não implementado",
    ),
    "bn": _panel_and_layer_menu_fallbacks(
        brush_manager="ব্রাশ ম্যানেজার",
        layers="লেয়ার",
        preview="পূর্বরূপ",
        toolbar="টুলবার",
        pages="পৃষ্ঠা",
        color="রঙ",
        font="ফন্ট",
        shape_manager="আকৃতি ব্যবস্থাপক",
        prompt_editor="প্রম্পট সম্পাদক",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="গ্যালারি",
        new_layer="নতুন লেয়ার",
        duplicate="লেয়ার নকল করুন",
        delete="লেয়ার মুছুন",
        merge_down="নিচের লেয়ারের সাথে মার্জ করুন",
        merge_visible="দৃশ্যমান লেয়ার মার্জ করুন",
        group_layers="লেয়ার গ্রুপ করুন",
        arrange="বিন্যাস",
        bring_to_front="সামনে আনুন",
        bring_forward="এক ধাপ সামনে আনুন",
        send_backward="এক ধাপ পিছনে পাঠান",
        send_to_back="সবচেয়ে পেছনে পাঠান",
        rasterize="রাস্টারাইজ",
        last_filter="শেষ ফিল্টার",
        not_implemented="এখনও বাস্তবায়িত নয়",
    ),
    "ru": _panel_and_layer_menu_fallbacks(
        brush_manager="Менеджер кистей",
        layers="Слои",
        preview="Предпросмотр",
        toolbar="Панель инструментов",
        pages="Страницы",
        color="Цвет",
        font="Шрифт",
        shape_manager="Менеджер фигур",
        prompt_editor="Редактор промптов",
        nano_banana="NanoBanana",
        comfyui="ComfyUI",
        gallery="Галерея",
        new_layer="Новый слой",
        duplicate="Дублировать слой",
        delete="Удалить слой",
        merge_down="Объединить с нижним",  # noqa: RUF001
        merge_visible="Объединить видимые",  # noqa: RUF001
        group_layers="Сгруппировать слои",
        arrange="Упорядочить",
        bring_to_front="На передний план",  # noqa: RUF001
        bring_forward="Переместить вперёд",
        send_backward="Переместить назад",
        send_to_back="На задний план",  # noqa: RUF001
        rasterize="Растрировать",
        last_filter="Последний фильтр",
        not_implemented="Ещё не реализовано",
    ),
}

_RUNTIME_OVERRIDE_TRANSLATIONS: dict[str, dict[str, dict[str, str]]] = {
    "ja": {
        "PerformancePage": {
            "Undo Steps": "元に戻す回数",
            "Recompose FPS Limit": "再合成FPS上限",
            "Lower values reduce CPU load on large canvases": (
                "値を下げると大きなキャンバスでのCPU負荷が軽減されます"
            ),
            "Spread Facing Page Quality": "見開きの相手ページ画質",
            "Full": "フル",
            "Balanced": "バランス",
            "Low": "低",
            (
                "Lowers the composite resolution of the facing page in a spread. "
                "Full keeps it as sharp as the page being edited; the lower steps "
                "trade sharpness for GPU memory on large, many-layered pages."
            ): (
                "見開きで隣に表示される相手ページの合成解像度を下げます。"
                "フルは編集中のページと同じシャープさを保ちます。"
                "大きく多レイヤーのページでは、下げるほど GPU メモリを節約できます。"
            ),
        },
    },
}

for _language, _domain_map in NON_JA_PANEL_FALLBACK_TRANSLATIONS.items():
    _catalog = _RUNTIME_FALLBACK_TRANSLATIONS.setdefault(_language, {})
    for _domain, _messages in _domain_map.items():
        _catalog.setdefault(_domain, {}).update(_messages)

for _language, _domain_map in NON_JA_TOOL_FALLBACK_TRANSLATIONS.items():
    _catalog = _RUNTIME_FALLBACK_TRANSLATIONS.setdefault(_language, {})
    for _domain, _messages in _domain_map.items():
        _catalog.setdefault(_domain, {}).update(_messages)

for _language, _messages in NON_JA_SHARED_FALLBACK_TRANSLATIONS.items():
    _catalog = _RUNTIME_FALLBACK_TRANSLATIONS.setdefault(_language, {})
    _catalog.setdefault("__shared__", {}).update(_messages)


for _domain, _messages in PAINTING_TOOL_JA.items():
    _JA_FALLBACK_TRANSLATIONS.setdefault(_domain, {}).update(_messages)


def _identity(text: str) -> str:
    return text


class TranslatedLiteral(str):
    """String subclass that retains the untranslated source literal."""

    source_text: str
    translation_domain: str | None

    def __new__(
        cls,
        value: str,
        source_text: str,
        translation_domain: str | None = None,
    ) -> TranslatedLiteral:
        obj = super().__new__(cls, value)
        obj.source_text = source_text
        obj.translation_domain = translation_domain
        return obj


def translated_literal(
    translator: TranslatorFn,
    source_text: str,
    *,
    domain: str | None = None,
) -> str:
    """Return translated text while keeping the original literal for later reuse."""

    resolved_domain = domain or _infer_translation_domain(translator)
    translated = translator(source_text)
    if isinstance(translated, TranslatedLiteral):
        if resolved_domain and not getattr(translated, "translation_domain", None):
            return TranslatedLiteral(
                str(translated),
                translated.source_text,
                resolved_domain,
            )
        return translated
    return TranslatedLiteral(translated, source_text, resolved_domain)


def translated_literal_domain(
    domain: str,
    source_text: str,
    *,
    use_fallback: bool = False,
) -> str:
    """Translate text for a fixed domain and keep that domain for future refreshes."""

    translator = _domain_translator(domain, use_fallback=use_fallback)
    return translated_literal(translator, source_text, domain=domain)


def _source_literal(text: str) -> str:
    source_text = getattr(text, "source_text", None)
    if isinstance(source_text, str) and source_text:
        return source_text
    return str(text)


def _source_domain(text: str) -> str | None:
    domain = getattr(text, "translation_domain", None)
    if isinstance(domain, str) and domain:
        return domain
    return None


def translate_runtime_text(text: str, translator: TranslatorFn | None = None) -> str:
    """Translate a literal, honoring any stored translation domain when available."""

    source_text = _source_literal(text)
    domain = _source_domain(text)
    if domain:
        return translate_with_fallback(domain, source_text)
    if translator is None:
        return source_text
    return translator(source_text)


def translate_with_fallback(domain: str, source_text: str) -> str:
    """Translate via Qt and fall back to curated literals for missing runtime entries."""

    for language in _current_language_candidates():
        localized_catalog = _RUNTIME_OVERRIDE_TRANSLATIONS.get(language)
        if localized_catalog is None:
            continue
        localized = localized_catalog.get(domain, {}).get(source_text)
        if localized is not None:
            return localized
    translated = QCoreApplication.translate(domain, source_text)
    if translated != source_text:
        return translated
    for language in _current_language_candidates():
        localized_catalog = _RUNTIME_FALLBACK_TRANSLATIONS.get(language)
        if localized_catalog is None:
            continue
        localized = localized_catalog.get(domain, {}).get(source_text)
        if localized is not None:
            return localized
        shared_localized = localized_catalog.get("__shared__", {}).get(source_text)
        if shared_localized is not None:
            return shared_localized
    return translated


def _current_language_setting() -> str:
    try:
        from loomstroke.platform import settings_manager as settings_manager_module  # noqa: PLC0415
    except Exception:
        return "system"
    settings = getattr(settings_manager_module, "_settings_manager", None)
    if settings is None:
        return "system"
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return "system"
    try:
        value = getter("ui/language", "system")
    except Exception:
        return "system"
    return value if isinstance(value, str) else "system"


def _current_language_candidates() -> tuple[str, ...]:
    try:
        from loomstroke.ui.i18n import _canonical_language_code  # noqa: PLC0415
    except Exception:

        def _canonical_language_code(code: str) -> str:
            return str(code or "").strip().replace("-", "_")

    setting = _current_language_setting()
    if setting == "system":
        return ()
    canonical = _canonical_language_code(setting)
    candidates: list[str] = []

    def _append(code: str) -> None:
        if code and code not in candidates:
            candidates.append(code)

    _append(canonical)
    if canonical == "zh_TW":
        _append("zh_Hant")
    if "_" in canonical:
        _append(canonical.split("_", 1)[0])
    return tuple(candidates)


def _infer_translation_domain(translator: TranslatorFn | None) -> str | None:
    if translator is None:
        return None
    domain = getattr(translator, "translation_domain", None)
    if isinstance(domain, str) and domain:
        return domain

    bound_self = getattr(translator, "__self__", None)
    if bound_self is None or getattr(translator, "__name__", "") != "tr":
        return None

    meta_object = getattr(bound_self, "metaObject", None)
    if callable(meta_object):
        try:
            qt_meta = meta_object()
        except Exception:
            qt_meta = None
        class_name = getattr(qt_meta, "className", lambda: None)()
        if isinstance(class_name, str) and class_name:
            return class_name

    cls = bound_self if isinstance(bound_self, type) else type(bound_self)
    class_name = getattr(cls, "__name__", None)
    if isinstance(class_name, str) and class_name:
        return class_name
    return None


def _domain_translator(
    domain: str,
    *,
    use_fallback: bool = False,
) -> TranslatorFn:
    def _translator(source_text: str) -> str:
        if use_fallback:
            return translate_with_fallback(domain, source_text)
        return QCoreApplication.translate(domain, source_text)

    _translator.translation_domain = domain  # type: ignore[attr-defined]  # function attribute metadata
    return _translator


@dataclass(slots=True)
class TranslationBinding:
    """Stores a setter and the original text that requires translation."""

    setter: Callable[[str], None]
    source_text: str
    domain: str | None = None


class TranslationBinder:
    """Tracks translation-aware setters and reapplies them on language change."""

    def __init__(self, translator: TranslatorFn | None = None) -> None:
        self._translator: TranslatorFn = translator or _identity
        self._bindings: list[TranslationBinding] = []

    def register(self, setter: Callable[[str], None], source_text: str) -> None:
        """Register a setter and immediately apply the current translation."""

        self._add_binding(
            TranslationBinding(
                setter=setter,
                source_text=_source_literal(source_text),
                domain=_source_domain(source_text),
            )
        )

    def register_domain(
        self,
        domain: str,
        setter: Callable[[str], None],
        source_text: str,
    ) -> None:
        """Register a setter that uses a Qt translation domain."""

        self._add_binding(
            TranslationBinding(
                setter=setter,
                source_text=_source_literal(source_text),
                domain=domain,
            )
        )

    def extend(self, pairs: Iterable[tuple[Callable[[str], None], str]]) -> None:
        """Register multiple bindings in one call."""

        for setter, text in pairs:
            self.register(setter, text)

    def set_translator(self, translator: TranslatorFn | None) -> None:
        """Replace the translator callable (use identity when ``None``)."""

        self._translator = translator or _identity

    def retranslate(self) -> None:
        """Reapply translations to every registered binding."""

        remaining: list[TranslationBinding] = []
        for binding in self._bindings:
            try:
                binding.setter(self._translate(binding))
            except RuntimeError as exc:
                if _is_deleted_binding_error(exc):
                    continue
                raise
            except ReferenceError:
                continue
            remaining.append(binding)
        self._bindings = remaining

    def _add_binding(self, binding: TranslationBinding) -> None:
        try:
            binding.setter(self._translate(binding))
        except RuntimeError as exc:
            if _is_deleted_binding_error(exc):
                return
            raise
        except ReferenceError:
            return
        self._bindings.append(binding)

    def _translate(self, binding: TranslationBinding) -> str:
        if binding.domain:
            return translate_with_fallback(binding.domain, binding.source_text)
        return self._translator(binding.source_text)


def _is_deleted_binding_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "Internal C++ object" in message
        or "already deleted" in message
        or "wrapped C/C++ object" in message
    )


__all__ = [
    "TranslatedLiteral",
    "TranslationBinder",
    "TranslationBinding",
    "translate_runtime_text",
    "translate_with_fallback",
    "translated_literal",
    "translated_literal_domain",
]
