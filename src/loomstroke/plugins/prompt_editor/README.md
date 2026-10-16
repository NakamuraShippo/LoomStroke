# Prompt Editor Plugin

Use the Prompt Editor to assemble complete prompts from reusable snippets, manage a prompt library, and submit text to the LLM gateway.

## Opening the panel
1. Launch LoomStroke.
2. Choose `Window > Prompt Editor` (enable it in `Window > Panels` if necessary).
3. Dock or float the panel; window placement is remembered.

## Layout overview
- **Compose tab:** build prompts from components, preview the result, and save templates.
- **Library tab:** search by tag/type, edit title/tags/type/body, duplicate, delete, and export saved prompts.
- **LLM tab:** configure provider/model options, edit System/User prompts, and view outputs.

## Creating a prompt
1. Ensure the snippets you need exist in the Text Editor; updates there appear here automatically (no refresh required).
2. In the **Compose** tab, click **New** and add components from the palette.
3. The preview refreshes immediately; use **Compose** or **Copy** as needed.
4. Save the prompt with **Save / Save As**.
5. In the **Library** tab, select saved prompts to review or update their details.
6. In the **LLM** tab, choose System/User prompt snippets from the dropdowns and submit them to the gateway.

## Integration highlights
- Text Editor changes propagate instantly. When a snippet is created, updated, or deleted, corresponding components refresh automatically.

## Tips
- Organize snippets with tags and categories (set in the Text Editor) to keep the component selector manageable.
- Use the **List** component for option sets (e.g., styles).
- Export prompts from the **Library** tab to share curated collections.

## Troubleshooting
- If the component selector shows stale data, verify that the Text Editor panel is autosaving and try reopening the Prompt Editor.
- Saving requires at least one component; the **Save** button remains disabled until the preview contains content.
