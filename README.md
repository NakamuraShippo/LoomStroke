<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/textlogo.png">
    <img src="img/textlogo_br.png" alt="LoomStroke" width="500">
  </picture>
</h1>

LoomStrokeは、PySide6とwgpuによるデスクトップペイントアプリです。
ラスター・ベクター編集、マルチページ原稿、文字、コマ・吹き出し、
ComfyUI・LLM・画像生成サービスとの連携を備えます。

## Windows版をZIPでダウンロード

### [最新版のLoomStrokeをダウンロード（ZIP）](https://github.com/NakamuraShippo/LoomStroke/archive/refs/heads/main.zip)

**Pythonの事前インストールは不要です。** Windows 64-bitと、初回セットアップ時の
インターネット接続が必要です。Pythonが見つからない場合は、アプリ専用のPythonを自動取得します。

1. 上のリンクからZIPをダウンロードします。
2. ZIPを右クリックして「すべて展開」し、中の `LoomStroke-main` フォルダを開きます。
3. `install.bat` をダブルクリックし、「Installation completed successfully.」と表示されるまで待ちます。
4. `boot_LoomStroke.bat` をダブルクリックして起動します。

2回目以降は `boot_LoomStroke.bat` だけで起動できます。Gitのインストールやコマンド入力は不要です。
ZIPを開いたまま実行せず、必ず展開してから操作してください。
展開先のフォルダは、セットアップ後もそのまま使用します。

このリンクは配布用 `main` の最新版を取得します。ZIPには本体・説明書・ライセンスを含み、
Python本体と仮想環境はインストール時に準備し、モデル重みとユーザーデータはZIPに含みません。
詳しくは[インストールと起動ガイド](docs/user/INSTALL_AND_RUN.md)を参照してください。

2026-10-20に、Python未導入のWindows 11・64-bit環境で、配布ZIPの展開から両BATによる
自動セットアップ・起動、新規文書への描画、Undo/Redo、保存、通常終了まで確認しました。
検証にはWindows Sandboxを使用しています。通常利用にWindows Sandboxは不要です。

このリポジトリはLoomStroke License v1.0によるソース公開版です。
利用条件は[LICENSE](LICENSE)を正本とし、日本語訳は[LICENSE_JA](LICENSE_JA)を参照してください。
商用利用を含む使用が許可されています。再配布・共有、配布を目的とする改変、
販売・譲渡などには著作権者の事前の書面による許可が必要です。
制作物の利用については、本体のライセンスによる制限はありません。
第三者のコード・リソースには、それぞれのライセンスが適用されます。

## 動作要件

- Python 3.11以上（Windowsでは `install.bat` が必要に応じて自動準備）
- wgpu対応GPUとドライバ（WindowsのD3D12、LinuxのVulkan、macOSのMetal）
- OSの資格情報ストア（paid GatewayのAPIキー保存・読出しに必要）

OSごとの実動作は環境に依存します。macOSでの実機検証は本スナップショットの検証に含めていません。

## インストールと起動

Windows 64-bitでは、Python未導入のまま `install.bat` をダブルクリックできます。
必要な場合は公式CPython 3.13.16を取得し、固定SHA-256の検証後、`.runtime/` 内に準備します。
管理者権限、システムのPythonやPATHの変更は必要ありません。
既存の `.venv` / `venv` を再利用し、なければ `.venv` を作り、本体と必須依存をインストールします。
完了後は `boot_LoomStroke.bat` で起動します。失敗時はエラーを画面に残して終了し、
既存の仮想環境を削除・置換しません。

`install.bat --nanobanana` でNanoBananaの追加依存を有効にできます。
自動実行時は `--no-pause`、新しい仮想環境の作成に使うPythonを指定する場合は
`--python "C:\path\python.exe"` を追加してください。
`--no-python-download` でPythonの自動取得を止められます。
`.runtime/` と `.venv/` は起動に使うため、セットアップ後も同じ場所に残してください。

手動でセットアップする場合は、リポジトリ直下で次を実行します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m loomstroke
```

macOS / Linuxでは、仮想環境の有効化に `source .venv/bin/activate` を使います。

描画に必須の `wgpu==0.31.0` は依存定義に含まれ、`rendercanvas` とともに自動で入ります。
`requirements.txt` にも登録されているため、個別インストールは不要です。
同ファイルは実行用のバージョン固定一覧で、NanoBanana用の追加SDKを含みます。
テスト・整形・型検査・ビルド用ツールは含めていません。

NanoBananaの追加依存を使う場合は、同じ仮想環境で次を実行します。

```powershell
python -m pip install -e ".[nanobanana]"
```

`keyring` は必須依存です。APIキーは `SecretProvider` 経由でOSの資格情報ストアを利用し、
そのストアが利用できない場合、paid GatewayのAPIキー保存・読出しは拒否されます。
外部サービスへの接続には、アプリ設定の `privacy/allow_external_services` による許可が必要です。
AI SelectionのSAM3.1実行環境やモデル重みは同梱せず、対応機能の利用時に別途準備します。

## 説明書

- [利用者向けガイドの入口](docs/user/index.html)
- [日本語マニュアル](docs/user/index_ja.html)
- [インストールと起動](docs/user/INSTALL_AND_RUN.md)
- [起動方法・トラブルシューティング](docs/user/RUN_INSTRUCTIONS.md)

2026-10-20時点の開発版を反映しています。CMYK描画、レイヤースタイル、
RGB/CMYK色パネル、ベクター線幅編集などを含みます。

メインメニューの「フィルター」から、レイヤーフィルターと同じ11種類を選び、
対象レイヤーに直接適用できます。ベクター・文字レイヤーではラスタライズの確認が表示され、
フィルター設定でOKを押すと、ラスタライズとフィルターをまとめて適用します。
確認や設定をキャンセルした場合は元のレイヤーを保ち、適用後も1回のUndoで元に戻せます。

## ソース構成

- `src/loomstroke/`: 本体・組み込みプラグイン・描画処理・実行用リソース
- `img/`: アプリのロゴ・アイコン
- `docs/user/`: 利用者向けガイド
- `LICENSES/` と `THIRD_PARTY_LICENSES`: 第三者ライセンスの告知

開発リポジトリの履歴、非公開テスト素材、テスト一式、内部開発資料、
ユーザーデータ、APIキー、モデル重み、仮想環境、ビルド済み配布物は含みません。
公開対象の境界は[PUBLICATION.md](PUBLICATION.md)に記載しています。

## 第三者コンポーネント

PySide6 / QtはLGPL-3.0の条件で利用しています。
動的にリンクされたライブラリの差し替えなど、第三者ライセンスが認める権利を
LoomStrokeの独自ライセンスで制限するものではありません。

Mapbox earcutの移植部分はISC License、同梱ICCプロファイルは各プロファイルの許諾に従います。
詳細は[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)、[LICENSES](LICENSES/)、
`src/loomstroke/io/resources/icc/` の告知を参照してください。

閲覧・forkなど公開先サービス上の操作には、そのサービスの利用規約も適用されます。
許可申請の連絡先はLICENSEに記載しています。
