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



