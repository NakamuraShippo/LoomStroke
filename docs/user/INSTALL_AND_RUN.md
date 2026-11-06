> **最終照合: 2026-11-06**

# LoomStroke インストールと起動ガイド

この文書は現行コードに合わせたセットアップ手順です。詳細な起動パターンは
`docs/user/RUN_INSTRUCTIONS.md`、プラグイン開発は `docs/dev/SDK_GUIDE.md` を参照してください。

## 推奨セットアップ

Windows 64-bitではPythonの事前インストールは不要です。インターネットに接続し、
リポジトリ直下の `install.bat` をダブルクリックしてください。
利用可能なPythonがない場合、Python公式のNuGet版3.13.16を `.runtime/` に取得し、
固定したSHA-256を検証してから仮想環境の作成と依存インストールを実行します。
管理者権限、システムPythonの追加・更新、永続的なPATH変更は必要ありません。
既存の `.venv` / `venv` があれば再利用し、なければ `.venv` を作ります。
完了後は `boot_LoomStroke.bat` で起動してください。失敗時はエラーを画面に残し、
既存環境を削除・置換せず終了します。`google-genai` を含むNanoBananaの依存も標準で入ります。
従来の `--nanobanana` も引き続き使えます。`--no-pause` で待機なし、
`--python "C:\path\python.exe"` で新規環境の作成に使うPythonを指定できます。
`--no-python-download` でPythonの自動取得を止められます。
`.runtime/` は `.venv` の基盤なので、セットアップ後も同じフォルダに残してください。
取得や検証に失敗した場合は処理を止め、既存環境を削除・置換しません。

### ZIP版のアップデート（Windows）

1. 原稿を保存してLoomStrokeを終了します。
2. インターネットに接続し、`boot_LoomStroke.bat` と同じ場所の `update.bat` をダブルクリックします。
3. `Update completed successfully` と表示されたら画面を閉じ、`boot_LoomStroke.bat` で起動します。

公開リポジトリの `main` の最新版を取得し、本体・同梱素材・説明書を置き換えてから、
既存のインストーラーで必要なPythonパッケージとNanoBananaの`google-genai`を更新します。Gitのインストールは不要です。
`user_data/`（設定・追加プラグイン等）、`.venv` / `venv`、`.runtime/`、本体フォルダ外の原稿や
ルートに置いた `.lstroke`・画像には上書きしません。独自のプラグインは `user_data/plugins/` に置いてください。

旧本体は `.update-backups/日時-ID/` に残します。本体管理下の `src/loomstroke/`、`img/`、
`docs/user/`、`LICENSES/` に直接追加・編集したファイルもバックアップ側へ移るため、
個人の原稿・素材はこれらのフォルダに保存しないでください。バックアップは正常動作の確認後に削除できます。
ダウンロード・ZIP検査の失敗時は本体を変更しません。置き換え・依存更新が失敗した場合は旧本体を戻します。
依存パッケージ自体は巻き戻せないため、依存更新の途中で失敗した場合は `install.bat` を再実行してください。
強制終了・停電等で中断した場合は、画面に表示されたバックアップ先の `RECOVERY.txt` と `changes.json` を確認してください。

初めてこの更新機能を導入するときは、最新版ZIPの `update.bat` と `update.ps1` を両方、
現在利用している本体フォルダへコピーしてください。以降は `update.bat` だけで更新できます。
公開Git版の更新は下記の手順に対応しています。開発用フォルダは自動更新の対象外です。
コマンドからの `update.bat --no-pause` は結果表示後のキー入力を省略します。
旧EXE配布用 `update_LoomStroke.bat` とは別の、ソースZIP版用の手順です。

### Git版のアップデート（Windows）

公開リポジトリ `NakamuraShippo/LoomStroke` をGitで導入した場合も、アプリを終了して
`update.bat` をダブルクリックすると更新できます。Git for Windowsが利用でき、`main`ブランチであることが必要です。
更新後は既存のインストーラーでPythonパッケージとNanoBananaの`google-genai`も更新します。

ローカル変更・未追跡ファイル・独自コミット・進行中のGit操作がある場合は、自動で破棄せず停止します。
Git管理外の設定・原稿・実行環境を引き継ぎ、更新ファイルと衝突する場合も上書きしません。
依存更新で失敗した場合、Gitの本体更新は保持されるので、案内に従って`install.bat`を再実行してください。

旧更新スクリプトがGit版を拒否する場合は、一度だけGitで最新版を取り込んでください。
以降は同じ`update.bat`で更新できます。開発リポジトリや`main`以外のブランチは従来のGit操作を使います。

手動でセットアップする場合は、以下の手順を使います。

### 1. 仮想環境を作成

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux の場合:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. パッケージをインストール

通常利用:

```powershell
python -m pip install -e .
```

開発用ツール込み:

```powershell
python -m pip install -e .[dev]
```

NanoBanana の依存関係も使う場合:

```powershell
python -m pip install -e .[nanobanana]
```

`wgpu==0.31.0` は必須依存に登録されており、`rendercanvas` とともに自動で入ります。
`requirements.txt` / `requirements.lock` にも両パッケージが含まれます。

`qasync` は通常の依存関係に含まれているため、現行構成では追加で個別
インストールする必要はありません。

`cryptography` も通常の依存関係に含まれており、外部プラグインの署名検証に
使用されます。API キーは `SecretProvider` 経由で OS の資格情報ストアに保存されます。
OS keyring が利用できない環境では、paid Gateway の API キー保存は失敗扱いになります。

### 3. 起動

インストール済み環境では、次のどちらでも起動できます。

```powershell
loomstroke
```

```powershell
python -m loomstroke
```

### ComfyUIを自動起動するときのウィンドウ表示（Windows）

Preferences → AI Effect → ComfyUI Foldersの
「ComfyUIのコンソールウィンドウを表示」で切り替えられます。
ONにすると次回の自動起動から別のコンソールにComfyUIの出力を表示し、
OFFでは従来どおりバックグラウンドで起動します。既定はOFFです。
フォルダの指定、「LoomStroke起動時にComfyUIを自動起動」、外部サービスの許可も必要です。

起動済みのComfyUIには切り替えが反映されないため、次回の起動時に確認してください。
バックグラウンドで自動起動したComfyUIは、生成中でもLoomStroke終了時に自動で終了します。
別コンソールで起動した場合、LoomStroke終了時にも止めるかどうかは
隣の「LoomStroke終了時にComfyUIも終了する」で設定します。コンソールを閉じてもComfyUIは終了します。
既に起動していたComfyUIと遠隔サーバーは終了対象に含めません。
この設定はコンソール表示を切り替えるもので、ブラウザーの自動表示は行いません。

## ソースチェックアウトから直接起動したい場合

開発用の root ランチャーを使うなら、次で起動できます。

```powershell
python main.py
```

低レベルに `python -m loomstroke` を使いたい場合は、`src` を `PYTHONPATH` に含めます。

```powershell
$env:PYTHONPATH = "src"
python -m loomstroke
Remove-Item Env:PYTHONPATH
```

どちらも開発補助向けです。通常は `pip install -e .` を推奨します。

## Canvas rendering backend

現行の Canvas 表示は Windows では wgpu native surface を既定候補として起動します。native surface は
live surface visual probe、scene signature、present serial で blank / stale frame を検出し、失敗時は
runtime-only で wgpu bitmap compatibility path へフォールバックします。Windows 以外では、live surface
oracle と lifecycle gate が通るまで wgpu bitmap compatibility path を既定候補として維持します。
切り替わってもドキュメント内容、Undo/Redo、保存状態は変更されません。

wgpu bitmap compatibility path を明示したい場合は `wgpu_bitmap` を指定します。
`wgpu_native` / `native` は native surface を明示します。
Windows では `wgpu`、`webgpu`、`auto`、未指定も native candidate を選びます。Windows 以外では
同じ指定が bitmap compatibility path を選びます。描画のちらつき、ストローク遅延、
ウィンドウ移動後の表示崩れがある場合は、まず `LOOMSTROKE_RENDER_BACKEND=wgpu_bitmap` で起動し、
GPU ドライバと wgpu backend (`WGPU_BACKEND_TYPE`) を確認してください。`opengl` / `gl` は退役済み alias として扱われ、通常起動では wgpu 側へ正規化されます。

## ユーザーデータの保存先

LoomStroke はまずリポジトリ直下の `user_data/` を使います。そこへ書き込めない
場合のみ、以下へ順にフォールバックします。

1. プラットフォームのアプリケーションデータ
2. ホームディレクトリ配下
3. システム一時ディレクトリ

主な保存先:

- 設定: `user_data/settings/LoomStroke.json`
- API キー: OS の資格情報ストア (`SecretProvider` 経由)
- 外部プラグイン: `user_data/plugins/`
- pytest 一時領域: `user_data/pytest_tmp`

## MCP で外部リポジトリから漫画 PNG を作る

LoomStroke MCP の comic workflow は、LoomStroke のリポジトリ外にある作業ディレクトリからでも
漫画 preview/package を作れるように設計されています。

1. LoomStroke を起動し、Preferences → LoomStroke MCP で live HTTP endpoint と Session Token を確認する
2. 出力したい外部リポジトリまたは成果物ディレクトリを MCP roots に追加する
3. Agent Approval は共有環境では `always_ask` を使う
4. MCP client から `loomstroke_get_capabilities` を呼び、`readiness.comicWorkflow` と roots を確認する
5. roots 配下に既存画像や生成済み画像がある場合は `loomstroke_import_asset` で registered asset にする
6. MCP 側の AI Effect で画像生成する場合は `readiness.graphicCompletion.generationReady` を確認し、
   先に `loomstroke_apply_ai_effect_preset` で AI Effect layer を作成する。戻り値が
   `ready=false` の場合は `loomstroke_wait_layer_ready` で `stateAvailable=true` を待ってから、
   `loomstroke_submit_generation` を `applyToCanvas=false` で実行して asset-only 結果を待つ
7. script から始める場合は `loomstroke_plan_comic_page` で `ComicPagePlan` を作る
8. `loomstroke_validate_comic_plan` で `ComicPagePlan` の geometry / text fit / roots を事前検査する
9. PNG だけ確認する場合は `loomstroke_render_comic_preview` を使う
10. 登録済み asset に低レベルな文字・矩形を焼き込みたい場合は `loomstroke_create_asset_overlay_preview` を使う
11. 漫画フキダシを高水準指定で焼き込みたい場合は `loomstroke_create_speech_bubble_overlay` を使う
12. 外部リポジトリへ成果物をまとめる場合は `loomstroke_export_comic_package` を使う

`loomstroke_export_comic_package` は roots 配下の directory に PNG preview、
`comic_page_plan.json`、`text_fit_report.json`、`package_manifest.json` を書き出します。
live canvas は変更しません。LoomStroke 上で編集可能な vector/text object として反映したい場合だけ、
live app endpoint で `loomstroke_create_comic_changeset` を作成し、内容を確認してから
`loomstroke_apply_changeset` を実行してください。適用は 1 つの Undo/Redo entry として扱われます。

MCP だけで最終 PNG まで完結させる場合は、`readiness.graphicCompletion` を確認します。
`assetImportReady` と `assetOverlayPreviewReady` が true なら、roots 配下の画像を
`loomstroke_import_asset` で登録し、その asset に `loomstroke_create_asset_overlay_preview` で
日本語テキストや矩形を焼き込めます。`text_box` は既定で透明背景なので、白い `rect` や
フキダシの上に文字だけ置く場合は追加指定なしで使えます。漫画フキダシは
`loomstroke_create_speech_bubble_overlay` で `anchor`、`maxWidth`、`tailDirection`、
`safePadding` を指定すると、矩形と text box を手作業で組み合わせずに焼き込めます。
AI Effect 生成まで MCP 内で行う場合は
`generationReady` が true である必要があります。false の場合は、外部 image generator へ逃がすのではなく、
ComfyUI preflight、NanoBanana / GPT Image 2 の credential、`privacy/allow_external_services`、
confirmation callback の不足を readiness の blocked reason で解消してください。

## 外部プラグイン（ZIP）導入手順

外部プラグインは `Plugins > Plugin Manager...` から導入します。

### 初回インポート

1. `Plugins > Plugin Manager...` を開く
2. `Import...` を押す
3. ZIP パッケージを選択する
4. 互換性と権限の確認ダイアログを確認し、必要なら許可する
5. 一覧に追加されたプラグインの `Source` が `external` であることを確認する

### 更新

1. 対象プラグインを選択する
2. `Update...` を押す
3. 更新 ZIP を指定する
4. 成功時は `Version` が更新される
5. 失敗時や戻したい場合は `Rollback...` を使う

### ロールバック

- ロールバックは直近の更新時バックアップを使います
- `Rollback...` 実行時は確認文字列の入力が必要です
- 更新が途中失敗した場合でも、復旧できないときは再インポートを検討してください

### 監査ログ

プラグイン操作は監査ログへ記録されます。

- 優先保存先: `user_data/plugin_audit/plugin_audit.jsonl`
- フィールド: `timestamp`, `plugin_id`, `action`, `result`, `reason`, `details`

確認例:

```powershell
Get-Content .\user_data\plugin_audit\plugin_audit.jsonl -Tail 40
```

## インストール確認

```powershell
python -c "import loomstroke; print(loomstroke.__file__)"
```

```powershell
python -c "from loomstroke.app import LoomStrokeApp; print('ok')"
```

## よくある問題

### `ModuleNotFoundError: No module named 'loomstroke'`

- `python -m pip install -e .` を実行していない
- もしくは `PYTHONPATH=src` を設定せずに `python -m loomstroke` を実行している

### `ImportError: No module named 'qasync'`

- 依存関係が壊れている可能性があります
- まず `python -m pip install -e .` を再実行してください

### 書き込み権限エラー

- リポジトリ配下の `user_data/` に書き込めない場合、別のフォールバック先が使われます
- 配布環境では `user_data/` を含めたまま配置すると最も分かりやすく運用できます
