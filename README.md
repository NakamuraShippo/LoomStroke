# LoomStroke

LoomStrokeは、PySide6とwgpuによるデスクトップペイントアプリです。
ラスター・ベクター編集、マルチページ原稿、文字、コマ・吹き出し、
ComfyUI・LLM・画像生成サービスとの連携を備えます。

このリポジトリはLoomStroke License v1.0によるソース公開版です。
利用条件は[LICENSE](LICENSE)を正本とし、日本語訳は[LICENSE_JA](LICENSE_JA)を参照してください。
商用利用を含む使用が許可されています。再配布・共有、配布を目的とする改変、
販売・譲渡などには著作権者の事前の書面による許可が必要です。
制作物の利用については、本体のライセンスによる制限はありません。
第三者のコード・リソースには、それぞれのライセンスが適用されます。

## 動作要件

- Python 3.11以上
- wgpu対応GPUとドライバ（WindowsのD3D12、LinuxのVulkan、macOSのMetal）
- OSの資格情報ストア（paid GatewayのAPIキー保存・読出しに必要）

OSごとの実動作は環境に依存します。macOSでの実機検証は本スナップショットの検証に含めていません。

## インストールと起動

Windowsでは、64-bit Python 3.11以上を準備し、`install.bat` をダブルクリックしてください。
既存の `.venv` / `venv` を再利用し、なければ `.venv` を作り、本体と必須依存をインストールします。
完了後は `boot_LoomStroke.bat` で起動します。失敗時はエラーを画面に残して終了し、
既存の仮想環境を削除・置換しません。

`install.bat --nanobanana` でNanoBananaの追加依存を有効にできます。
自動実行時は `--no-pause`、新しい仮想環境の作成に使うPythonを指定する場合は
`--python "C:\path\python.exe"` を追加してください。

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

NanoBananaの追加依存を使う場合は、同じ仮想環境で次を実行します。

```powershell
python -m pip install -e ".[nanobanana]"
```

`keyring` は必須依存です。APIキーは `SecretProvider` 経由でOSの資格情報ストアを利用し、
そのストアが利用できない場合、paid GatewayのAPIキー保存・読出しは拒否されます。
外部サービスへの接続には、アプリ設定の `privacy/allow_external_services` による許可が必要です。
SAMの実行環境やモデル重みは同梱せず、対応機能の利用時に別途準備します。

## ソース構成

- `src/loomstroke/`: 本体・組み込みプラグイン・描画処理・実行用リソース
- `img/`: アプリのロゴ・アイコン
- `docs/user/`: 利用者向けガイド
- `LICENSES/` と `THIRD_PARTY_LICENSES`: 第三者ライセンスの告知

開発リポジトリの履歴、非公開テスト素材、テスト一式、内部開発資料、
ユーザーデータ、APIキー、モデル重み、仮想環境、ビルド済み配布物は含みません。
既存ガイド内の `docs/dev/` への参照先は、このスナップショットには含まれません。
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
