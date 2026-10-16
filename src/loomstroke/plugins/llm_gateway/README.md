# LLM Gateway Plugin

This plugin is focused on LLM execution (OpenAI/Anthropic/Gemini) and no longer provides a standalone settings panel.

## Settings entry points
- API keys are registered from Preferences → AI Effect → API Keys and read through `SecretProvider`.
- API keys are stored in the OS keyring and are fetched only for the duration of a gateway call.
- Gateway service settings are driven from existing LLM-related settings and editor-side workflows.
- Prompt and system prompt defaults are primarily managed in the Prompt Editor (`prompt_editor/llm/*`) and related text-editor flows.

## Notes
- There is no standalone “LLM Settings” panel in the current build.
- If you rely on a separate LLM settings panel UI, migrate that workflow to Prompt Editor settings.
