"""Stable source text and Japanese fallback for palette persistence feedback."""

TITLE = "Palette"
LOAD_FAILED = "The palette file could not be loaded. It has not been overwritten."
SAVE_FAILED = "The palette could not be saved. Your last confirmed palette is still active."
SETTINGS_FAILED = (
    "The palette file was saved, but its location setting could not be updated. "
    "The saved palette remains active in this session."
)

PALETTE_FEEDBACK_JA = {
    TITLE: "パレット",
    LOAD_FAILED: "パレットファイルを読み込めませんでした。ファイルは上書きしていません。",
    SAVE_FAILED: "パレットを保存できませんでした。最後に確定したパレットを引き続き使用します。",
    SETTINGS_FAILED: (
        "パレットファイルは保存されましたが、保存先設定を更新できませんでした。"
        "現在のセッションでは保存済みのパレットを使用します。"
    ),
}
