"""Shim: upstream forces the FLASH_ATTENTION SDP backend exclusively.

``sam3/model/decoder.py`` wraps its non-FA3 attention in
``with sdpa_kernel(SDPBackend.FLASH_ATTENTION):``. PyTorch does not ship the
flash SDP kernel on Windows, so every tracker attention call dies with
"No available kernel". The shim rebinds the module-level ``sdpa_kernel``
reference inside the decoder module to a context that keeps the flash
preference but allows EFFICIENT/MATH fallbacks.

Detection-first: a tiny probe checks whether the exclusive flash context
actually works on this machine; when it does (Linux with flash kernels) the
upstream path is left untouched and the shim reports ``native``.
"""

from __future__ import annotations

from .registry import CompatibilityStatus, ShimResult

SHIM_NAME = "windows_sdp"
_MARKER = "_loomstroke_sam31_sdp_shim"

_EXCLUSIVE_SNIPPET = "with sdpa_kernel(SDPBackend.FLASH_ATTENTION):"


def _flash_only_probe_works() -> bool:
    """Return True when an exclusive flash SDP context can actually run."""

    import torch  # noqa: PLC0415
    import torch.nn.functional as torch_functional  # noqa: PLC0415
    from torch.nn.attention import SDPBackend, sdpa_kernel  # noqa: PLC0415

    if not torch.cuda.is_available():
        return False
    try:
        query = torch.zeros(1, 1, 8, 64, device="cuda", dtype=torch.bfloat16)
        with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
            torch_functional.scaled_dot_product_attention(query, query, query)
        return True
    except Exception:  # noqa: BLE001 - "No available kernel" and friends
        return False


def apply() -> ShimResult:
    try:
        import inspect  # noqa: PLC0415

        import sam3.model.decoder as decoder_module  # noqa: PLC0415

        existing = getattr(decoder_module, _MARKER, None)
        if existing is not None:
            return ShimResult(SHIM_NAME, existing, "already evaluated")

        if _flash_only_probe_works():
            status = CompatibilityStatus.NATIVE
            detail = "exclusive flash SDP context works on this machine"
            setattr(decoder_module, _MARKER, status)
            return ShimResult(SHIM_NAME, status, detail)

        source = inspect.getsource(decoder_module)
        if _EXCLUSIVE_SNIPPET not in source:
            status = CompatibilityStatus.UNSUPPORTED_VERSION
            detail = "decoder no longer forces an exclusive flash SDP context"
            setattr(decoder_module, _MARKER, status)
            return ShimResult(SHIM_NAME, status, detail)

        from torch.nn.attention import SDPBackend, sdpa_kernel  # noqa: PLC0415

        def _fallback_sdpa_kernel(*_args: object, **_kwargs: object):
            return sdpa_kernel(
                [
                    SDPBackend.FLASH_ATTENTION,
                    SDPBackend.EFFICIENT_ATTENTION,
                    SDPBackend.MATH,
                ]
            )

        decoder_module.sdpa_kernel = _fallback_sdpa_kernel
        status = CompatibilityStatus.SHIM_APPLIED
        setattr(decoder_module, _MARKER, status)
        return ShimResult(SHIM_NAME, status, "decoder SDP context allows efficient/math fallbacks")
    except Exception as exc:  # noqa: BLE001 - fail closed with diagnostics
        return ShimResult(SHIM_NAME, CompatibilityStatus.FAILED, f"{type(exc).__name__}: {exc}")


__all__ = ["SHIM_NAME", "apply"]
