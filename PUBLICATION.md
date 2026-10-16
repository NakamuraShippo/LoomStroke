> **最終照合: 2026-10-16**

# 公開用ソースの対象範囲

このフォルダは開発用 `LoomStroke_dev` の作業中ソースから作成した配布用スナップショットです。
配布先は [NakamuraShippo/LoomStroke](https://github.com/NakamuraShippo/LoomStroke) です。
利用者向け文書も開発側の最新版を収録し、公開用README・除外ルール・第三者告知を適用しています。
開発リポジトリのGit履歴・ブランチ・タグ・remote設定を引き継ぎません。
LoomStroke本体の `LICENSE` と `LICENSE_JA` は開発側から内容を変更せず収録しています。

## 収録するもの

- 本体ソース `src/loomstroke/` と実行に必要な翻訳・アイコン・schema・ICC
- ロゴ `img/`、第三者告知 `LICENSES/`、`THIRD_PARTY_LICENSES`
- 利用者向け文書 `docs/user/`
- `pyproject.toml`、`requirements.txt`
- `main.py`、`install.bat`、`boot_LoomStroke.bat`、`run.sh`

公開側のREADMEと除外ルールを用意し、earcut移植部分のISC告知を補っています。
製品のPythonソース、保存形式、公開APIは変更していません。
`wgpu==0.31.0` の必須依存・snapshotを開発側と揃え、Windows用 `install.bat` を収録しています。
開発・検証用の `requirements.lock` は開発側で保持し、公開側には収録しません。
`install.bat --no-pause` による新規仮想環境の実インストール、pip check、公開ソースのimport、
実GPU device作成を確認済みです。検証で生成した `.venv` はGit管理対象外です。

## 収録しないもの

- 開発リポジトリの `.git/` と内部開発資料 `docs/dev/`
- 開発・検証用の `requirements.lock`
- `tests/`、`benchmarks/`、開発用 `scripts/`・`tools/`、CI設定
- 非公開の吹き出し画像（原本 `fukidashi_test/` とテスト用コピー）
- `sam3Test/`、作業中の画像・原稿、生成結果、検証出力
- エージェント・エディタ設定、`AGENTS.md`、`CLAUDE.md`
- `user_data/`、credential、APIキー、ログ、キャッシュ
- 仮想環境、モデル重み、ビルド出力、バイナリ用インストーラー・更新用配布物

テストの非収録は公開範囲の選択です。開発側のテストや非公開素材を削除・緩和したものではありません。
このフォルダ単独では開発側の全テストスイートを実行できません。

## 更新時の扱い

開発フォルダ全体やGit履歴をコピーせず、上記の収録範囲を選別します。
非公開画像はファイル名だけでなく、同じ内容の別名コピーも含めないよう確認します。
`.gitignore` は補助であり、追跡済みファイルの非公開化には使えません。

このスナップショットは開発側と自動同期しません。更新時は `LoomStroke_dev` から必要な範囲を選び、
公開向けの文書・告知を保ったうえで差分・ライセンス・秘密情報を再確認します。

## オンライン公開前の残件

- 新規環境での通常GUI起動と実操作を確認する（インストール・import・GPU作成は確認済み）。
- 利用者向け既存ガイドの内部文書リンク・旧仕様の記載を整理する。
- 実際に公開する最終コミットのファイル一覧・秘密情報・告知を再確認する。

配布先に用意されたGitリポジトリとremote設定を使用します。この移行作業ではpushや公開設定の変更を行いません。
