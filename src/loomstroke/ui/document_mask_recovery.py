"""Present a recovered document without selecting its original as the save target."""

from .translations import translate_with_fallback

MASK_RECOVERY_MESSAGE = (
    "Some layer or effect masks are missing. The document may look different. "
    "Use Save As to keep the original file."
)


def show_missing_mask_recovery(coordinator, payload) -> bool:
    count = payload.get("missing_mask_count", 0) if isinstance(payload, dict) else 0
    if not isinstance(count, int) or count <= 0:
        return False
    window = coordinator._window
    window._document_load_recovered = True
    window._document_path = None
    window._document_dirty = True
    window._set_document_present(True)
    window._update_window_title()
    message = translate_with_fallback("MainWindow", MASK_RECOVERY_MESSAGE)
    window.statusBar().showMessage(message, 10000)
    coordinator._message_box().warning(window, window._ts("Warning"), message)
    return True
