> **最終照合: 2026-05-30**

# LoomStroke ユーザー向けロードマップ

予定は固定ではなく、安定性・保存互換性・privacy・credential 保護を優先して入れ替わる場合があります。
実装済みの項目はこのロードマップから外し、未完了・検証継続・将来候補だけを載せています。

## 現在地

- Beta 公開前 polish
- 既存機能の安定化
- 保存互換性の確認
- 外部通信と API キー保護の確認

## 近日: Beta 公開準備

- 起動 / 新規作成 / 保存 / 読み込み
- Autosave Recovery
- `.lstroke` 読み込み安全性
- Credential / API Key 保護
- External Service Gate
- 外部プラグイン ZIP 安全検査
- 外部プラグイン署名 / 更新 / ロールバック
- Dark / Light Theme
- Runtime Language Switch
- DPI / ドックレイアウト復元
- ペンストローク体感性能
- Pan / Zoom 体感性能
- レイヤー操作体感性能

## Beta 公開後: 安定化

- データ損失防止
- 保存不能 / 復旧不能の修正
- Credential 漏えいリスク対応
- 意図しない外部通信の防止
- 起動不能 / 終了時クラッシュ対応
- Qt 由来の不安定動作対応
- Stroke / Pan / Zoom / レイヤー合成性能
- Theme / 言語切り替え / レイアウト崩れ
- AI 生成エラー表示
- AI 生成リトライ UX

## Beta 期間の機能候補

- Image Size / Resample
- ComfyUI Workflow Bridge 残作業
- ペンツール強化
- CMYK Soft Proof

## Stable に向けて

- Unit QA
- E2E QA
- 手動 QA
- Windows インストール確認
- Windows 起動 / 保存 / 終了確認
- 既知の制限事項整理
- インストール手順更新
- ユーザーガイド更新
- AI / Plugin 関連手順更新
- Packaging / 依存関係確認
- Release Notes

## Stable 後の大きな計画

- AI Effect Workshop
- Parameter Layout Editor
- 内部構造の整理
- GPU 基盤の長期移行

## Beta で残る見込みの制限

- ページごとの独立 Undo / Redo
- 未キャッシュページのサムネイル placeholder
- AI / 外部サービス機能の許可設定
- Image Size / Resample
- CMYK Soft Proof
- GPU / DPI / Locale / 外部 API 環境の網羅

## フィードバックで特に助かること

- 再現手順
- 保存前後の状況
- ページ切り替え前後の状況
- レイヤー操作前後の状況
- Theme / 言語 / DPI / キャンバスサイズ
- ComfyUI / NanoBanana / 外部プラグイン利用有無
