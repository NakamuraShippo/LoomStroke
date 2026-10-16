# LLM Gateway プラグイン

このプラグインは LLM 実行（OpenAI/Anthropic/Gemini）を提供するコア機能に近い位置付けで、専用の設定パネルは含みません。

## 設定の取り扱い
- API キーは Preferences → AI Effect → API Keys で登録し、`SecretProvider` 経由で読み込みます。
- API キーは OS keyring に保存され、Gateway 呼び出し中だけ read-on-demand で取得されます。
- システムプロンプトの既定値や送信設定は既存の LLM ワークフローと Prompt Editor 側の設定から反映されます。

## 補足
- 現行実装では `LLM Settings` の独立パネルUIはありません。
- 既存運用は `prompt_editor/llm/*` など Prompt Editor 経由の設定に寄せてください。
