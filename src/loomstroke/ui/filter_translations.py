"""Japanese source labels for filter controls and layer blending captions."""

FILTER_PANEL_JA = {
    "No filter selected": "フィルターが選択されていません",
}

DIRECT_FILTER_JA = {
    "Rasterize layer?": "レイヤーをラスタライズしますか?",
    'Applying this filter requires rasterizing "{name}". The vector or text '
    "will no longer be editable after applying. Continue?": (
        "このフィルターを適用するには「{name}」のラスタライズが必要です。"
        "適用後はベクターや文字として編集できなくなります。続行しますか?"
    ),
    "Select a vector or text layer to rasterize.": (
        "ラスタライズするベクターか文字レイヤーを選択してください。"
    ),
    "Use this managed layer's own controls to rasterize it.": (
        "この管理レイヤーは専用の編集操作からラスタライズしてください。"
    ),
    "The layer could not be rasterized. The original objects were preserved.": (
        "レイヤーをラスタライズできませんでした。元のオブジェクトは保持されています。"
    ),
    "Filter Error": "フィルターエラー",
    "Canvas service is not available.": "キャンバスを利用できません。",
    "Preparing filter preview…": "フィルターのプレビューを準備中…",
    "Apply to the current layer. Undo restores the original pixels.": (
        "現在のレイヤーへ直接適用します。元の画像にはUndoで戻せます。"
    ),
    "The editing target changed. Reopen the filter to continue.": (
        "編集対象が変更されました。フィルターを開き直してください。"
    ),
    "Finish the current stroke or text edit first.": "描画中の線や文字編集を完了してください。",
    "Unlock the layer and its parent groups before applying a filter.": (
        "レイヤーと親グループのロックを解除してからフィルターを適用してください。"
    ),
    "The filter could not be applied. The original pixels were preserved.": (
        "フィルターを適用できませんでした。元の画像は保持されています。"
    ),
    "The selected filter is not available.": "選択されたフィルターは利用できません。",
    "Select a raster layer to apply a filter.": (
        "フィルターを適用するラスターレイヤーを選択してください。"
    ),
    "Filters cannot edit locked layers or layer masks.": (
        "ロックされたレイヤーやレイヤーマスクにはフィルターを適用できません。"
    ),
    "Rasterize the layer transform before applying a filter.": (
        "レイヤーの変形をラスタライズしてからフィルターを適用してください。"
    ),
    "The panel drawing target is not editable.": (
        "このコマの編集対象へはフィルターを適用できません。"
    ),
}

FILTER_PANEL_PLUGIN_JA = {
    "Filters": "フィルター",
    "Edit parameters of filter layers.": "フィルターレイヤーのパラメーターを編集します。",
}

FILTER_LAYER_JA = {
    "Add Filter Layer": "フィルターレイヤーを追加",
    "Add Filter": "フィルターを追加",
    "Layer Blend": "レイヤーブレンド",
    "How this layer blends with the layers below": "このレイヤーと下のレイヤーのブレンド方法",
}

FILTER_APPEARANCE_JA = {
    "Filter Layer": "フィルターレイヤー",
    "No filters available": "利用できるフィルターがありません",
}

FILTER_EXPORT_JA = {
    "No SVG-compatible vector objects to export. Unsupported blend modes or live filters "
    "were omitted.": (
        "SVG に書き出せるベクターオブジェクトがありません。対応していない描画モード"
        "またはライブフィルターを使用するレイヤーは省略されました。"
    ),
    "Exported {name}, but omitted {count} unsupported vector layer(s). Layers using "
    "non-normal blend modes or visible live filters cannot be represented in the current "
    "SVG subset.": (
        "{name} を書き出しましたが、未対応のベクターレイヤー {count} 件を省略しました。"
        "通常以外の描画モード、または表示中のライブフィルターを使用するレイヤーは、"
        "現在の SVG 対応範囲では表現できません。"
    ),
}
