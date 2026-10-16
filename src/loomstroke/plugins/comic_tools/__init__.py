"""Comic Tools plugin: コマ割り・フキダシ・セリフ管理の最小漫画制作基盤."""

__all__ = ["ComicToolsPlugin", "create_plugin"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    from .tool import ComicToolsPlugin, create_plugin  # noqa: PLC0415

    exports = {"ComicToolsPlugin": ComicToolsPlugin, "create_plugin": create_plugin}
    globals().update(exports)
    return exports[name]
