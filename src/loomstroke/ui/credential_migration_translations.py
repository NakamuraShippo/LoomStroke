"""English sources and Japanese fallback for preserved credential notices."""

TITLE = "API key migration needs attention"
PRESERVED = (
    "Some older API key data could not be migrated to the system keyring. "
    "The old data has been kept for recovery. Check your API key settings and "
    "restart the app to try again."
)
CONFLICT = (
    "Older API key data differs from the system keyring. Neither copy was replaced, "
    "and the old data has been kept for recovery. Review your API key settings "
    "before restarting to try again."
)
CLEANUP = (
    "Cleanup of older API key data could not be completed. The remaining files were kept. "
    "Review your API key settings before restarting the app to retry."
)

CREDENTIAL_MIGRATION_JA = {
    TITLE: "APIキーの移行を確認してください",
    PRESERVED: (
        "以前のAPIキーデータの一部をシステムのキーリングへ移行できませんでした。"
        "復旧できるように古いデータを保持しています。APIキーの設定を確認し、"
        "アプリを再起動して再試行してください。"
    ),
    CONFLICT: (
        "以前のAPIキーデータとシステムのキーリングの内容が異なります。"
        "どちらも置き換えず、復旧できるように古いデータを保持しています。"
        "APIキーの設定を確認してから、再起動して再試行してください。"
    ),
    CLEANUP: (
        "以前のAPIキーデータの整理を完了できませんでした。残ったファイルは保持しています。"
        "APIキーの設定を確認してからアプリを再起動し、再試行してください。"
    ),
}
