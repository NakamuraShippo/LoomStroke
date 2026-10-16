"""Japanese labels for selection, fill, vector erasing, and brush dynamics."""

PAINTING_TOOL_JA = {
    "BasicSelectionRectTool": {
        "Polygon": "多角形",
        "Unable to apply selection.": "選択範囲を反映できませんでした。",
    },
    "BasicFillBucket": {
        "Fill Mode": "塗りつぶし方式",
        "Bucket": "バケツ",
        "Enclose and Fill": "囲って塗る",
        "Gap Fill": "塗り残しを塗る",
        "Reference": "参照",
        "Visible Layers": "表示レイヤー",
        "Area Scaling": "領域の拡張・縮小",
        "Gap Fill Size": "塗り残しブラシのサイズ",
        "A fill is still being prepared.": "塗りつぶしを準備中です。",
        "The fill could not be prepared. The original pixels were preserved.": (
            "塗りつぶしを準備できませんでし" "た。元の画像を保持しました。"
        ),
        "The document changed. The prepared fill was cancelled.": "文書が変更されたため、準備中の塗りつぶしを取り消しました。",  # noqa: E501
        "The fill could not be applied. The original pixels were preserved.": (
            "塗りつぶしを反映できませんでし" "た。元の画像を保持しました。"
        ),
        "Fill requires an unlocked raster layer.": "塗りつぶしにはロックされていないラスターレイヤーが必要です。",  # noqa: E501
        "The fill reference could not be read.": "塗りつぶしの参照画像を取得できませんでした。",
    },
    "BasicPen": {
        "Basic Settings": "基本",
        "Stroke Shape": "線の形状",
        "Pressure Response": "筆圧",
        "Tone and Shading": "トーン・濃淡",
        "Texture and Mixing": "紙目・混色",
        "Scatter and Variation": "散布・ばらつき",
        "Other Settings": "その他の設定",
        "Pen Settings": "ペン設定",
        "Pressure (%)": "筆圧 (%)",
        "Curve: {gamma}; minimum: {minimum}%": "カーブ: {gamma} / 下限: {minimum}%",
        (
            "Drag the middle handle to change the curve, or the left handle to change the minimum. "
            "Left/Right choose a handle; Up/Down adjust; Shift increases the step; Home resets; "
            "Esc cancels the drag."
        ): (
            "中央のハンドルでカーブ、左のハンドルで下限を調整します。"
            "左右キーで選択、上下キーで調整、Shiftで大きく調整、"
            "Homeで初期値へ戻し、Escでドラッグを取り消します。"
        ),
        "Size Pressure Curve": "サイズの筆圧カーブ",
        "Minimum Pressure Size": "筆圧サイズの下限",
        "Opacity Pressure": "筆圧で不透明度を変える",
        "Opacity Pressure Curve": "不透明度の筆圧カーブ",
        "Minimum Pressure Opacity": "筆圧不透明度の下限",
        "Scatter": "散布",
        "Size Variation": "サイズのばらつき",
        "Angle Variation": "角度のばらつき",
        "Color Variation": "色のばらつき",
        "Paper Texture": "紙目の強さ",
        "Paper Grain Size": "紙目の大きさ",
        "Color Mixing": "下地との混色",
        "Variation Seed": "ばらつきのシード",
    },
    "BasicEraser": {
        "Vector Erase Mode": "ベクター消去方式",
        "Touched Area": "触れた部分",
        "Whole Stroke": "線全体",
        "To Intersection": "交点まで",
        "A vector erase is still being prepared.": "ベクター消去を準備中です。",
        "The vector erase could not be prepared. The original strokes were preserved.": (
            "ベクター消去を準備できませんで" "した。元の線を保持しました。"
        ),
        "The document changed. The vector erase was cancelled.": "文書が変更されたため、ベクター消去を取り消しました。",  # noqa: E501
    },
    "VectorTools": {
        "Edit Width Stop": "線幅の調整点を編集",
        "Reset Width Profile": "線幅の調整をリセット",
        "Position along stroke (%)": "線上の位置 (%)",
        "Width at this position (px)": "この位置の線幅 (px)",
    },
    "MainWindow": {
        "PSD export cannot preserve this filter layer. Export a flattened image instead.": (
            "このフィルターレイヤーはPSDで保持できませ" "ん。統合した画像として書き出してください。"
        ),
        "A layer could not be rendered for PSD export.": "PSD書き出し用のレイヤー画像を取得できませんでした。",  # noqa: E501
        "PSD export cannot preserve a transformed layer mask.": "変形したレイヤーマスクはPSDで保持できません。",  # noqa: E501
    },
    "ToolPropertiesPanel": {
        "Candidate Actions": "候補操作",
        "Apply Options": "適用方法",
        "Generation": "生成",
        "Basic Settings": "基本",
        "Stroke Quality": "線の品質",
        "Vector Erasing": "ベクター消去",
        "Region and Gaps": "領域・隙間",
        "Output": "出力",
        "Color Stops": "中間色",
        "Output Quality": "出力品質",
        "Selection Edges": "選択範囲の境界",
        "Transform": "変形",
        "Fill": "塗りつぶし",
        "Path Shape": "パスの形状",
        "Text Spacing": "文字の間隔",
        "Frame Padding": "枠の余白",
        "Writing Direction": "文字の方向",
        "Stroke Shape": "線の形状",
        "Pressure Response": "筆圧",
        "Tone and Shading": "トーン・濃淡",
        "Detection": "検出",
        "Preview": "プレビュー",
        "Click Behavior": "クリック時の動作",
        "Export": "書き出し",
        "Other Settings": "その他の設定",
        "Tool Settings": "ツール設定",
        "Polygon": "多角形",
        "Fill Mode": "塗りつぶし方式",
        "Bucket": "バケツ",
        "Enclose and Fill": "囲って塗る",
        "Gap Fill": "塗り残しを塗る",
        "Reference": "参照",
        "Visible Layers": "表示レイヤー",
        "Area Scaling": "領域の拡張・縮小",
        "Gap Fill Size": "塗り残しブラシのサイズ",
        "Vector Erase Mode": "ベクター消去方式",
        "Touched Area": "触れた部分",
        "Whole Stroke": "線全体",
        "To Intersection": "交点まで",
    },
}
