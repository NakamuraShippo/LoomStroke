> **最終照合: 2026-10-22**

# LoomStroke 起動方法

この文書は最短の起動リファレンスです。セットアップ全体は
`docs/user/INSTALL_AND_RUN.md` を参照してください。

## 推奨起動方法

### インストール済み環境

```powershell
loomstroke
```

または:

```powershell
python -m loomstroke
```

前提:

- `python -m pip install -e .` を実行済み
- もしくは通常インストール済み

## AI 無しモード

起動時に `--no-ai` を付けると、AI 機能と関連 UI を除いて起動します。
Windows では `boot_LoomStroke_noAI.bat` をダブルクリックするだけで、このモードで起動できます。

```powershell
# Windows の起動バッチ
.\boot_LoomStroke_noAI.bat
# 通常のバッチに引数を付けても同じモードで起動
.\boot_LoomStroke.bat --no-ai

# インストール済み環境
loomstroke --no-ai
# または
python -m loomstroke --no-ai

# この開発環境
.\venv\Scripts\python.exe -m loomstroke --no-ai
```

AI Effects Manager、AI Select、ComfyUI、LLM Gateway、GPT Image 2、NanoBanana、
Prompt Editor、生成履歴、生成 3D プレビュー、Canvas Assistant、Production Control を
起動対象から外します。Preferences の AI Effect・SAM 3.1・LoomStroke MCP と、
Interface の外部 AI サービス許可欄も表示しません。ComfyUI・SAM・MCP の自動起動設定が
ON でも、この起動中は実行しません。

AI 設定やプリセットは削除せず、次回フラグなしで起動すると通常モードに戻ります。
AI 無しモードではパネル配置を保存しないため、通常用レイアウトは上書きされません。
既存文書の AI レイヤーデータは保持し、生成・入力スロット・AI パラメーター編集 UI は表示しません。
通常の描画・選択・フィルター・文字・ページ・参照画像は利用できます。

## ソースツリーからの起動

最小の開発用起動は root の `main.py` です。

```powershell
python main.py
```

`python -m loomstroke` を直接使う場合は、`src` を import path に含めます。

```powershell
$env:PYTHONPATH = "src"
python -m loomstroke
Remove-Item Env:PYTHONPATH
```

## 非推奨の起動方法

```powershell
python src\loomstroke\__main__.py
```

`src/loomstroke/__main__.py` には互換目的の import path 補助がありますが、
通常運用の起動経路としては推奨しません。検証・配布・CI では
`loomstroke` か `python -m loomstroke` を使ってください。

## ユーザーデータとログ

LoomStroke はまずリポジトリ直下の `user_data/` を使用します。
書き込めない場合のみ、プラットフォーム標準のユーザーデータ領域へ
フォールバックします。

主な場所:

- 設定: `user_data/settings/LoomStroke.json`
- プラグイン: `user_data/plugins/`
- 監査ログ: `user_data/plugin_audit/plugin_audit.jsonl`

## 起動できないときの最小確認

```powershell
python -c "import loomstroke; print('import ok')"
```

```powershell
python -c "from loomstroke.app import main; print('entry ok')"
```

```powershell
python -m pip show loomstroke
```



