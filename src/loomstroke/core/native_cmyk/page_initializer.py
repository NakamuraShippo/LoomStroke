"""Keep added pages in the document's native ink mode before publishing them."""

from .document_convert_command import DocumentCmykConvertCommand


class NativeCmykPageInitializer:
    @staticmethod
    def initialize(canvas, page):
        native = next(
            (
                layer.native_cmyk
                for layer in canvas._layer_tree().flatten_layers()
                if getattr(layer, "native_cmyk", None) is not None
            ),
            None,
        )
        if native is None:
            return
        converter = DocumentCmykConvertCommand(native.profile, bits=native.inks.dtype.itemsize * 8)
        page.layers = [converter._convert(layer) for layer in page.layers]
