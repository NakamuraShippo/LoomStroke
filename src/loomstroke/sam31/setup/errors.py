"""Setup error codes with user-facing actions."""

from __future__ import annotations

ROOT_INVALID = "ROOT_INVALID"
CHECKPOINT_MISSING = "CHECKPOINT_MISSING"
PYTHON_UNSUPPORTED = "PYTHON_UNSUPPORTED"
VENV_CREATE_FAILED = "VENV_CREATE_FAILED"
PIP_FAILED = "PIP_FAILED"
TORCH_INSTALL_FAILED = "TORCH_INSTALL_FAILED"
CUDA_UNAVAILABLE = "CUDA_UNAVAILABLE"
CUDA_MISMATCH = "CUDA_MISMATCH"
SAM31_INSTALL_FAILED = "SAM31_INSTALL_FAILED"
COMPATIBILITY_UNSUPPORTED = "COMPATIBILITY_UNSUPPORTED"
WORKER_START_FAILED = "WORKER_START_FAILED"
HEALTH_FAILED = "HEALTH_FAILED"
MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
OOM = "OOM"
NETWORK_CONSENT_REQUIRED = "NETWORK_CONSENT_REQUIRED"
CANCELLED = "CANCELLED"

ACTIONS: dict[str, str] = {
    ROOT_INVALID: "Choose a writable SAM 3.1 root folder in Preferences.",
    CHECKPOINT_MISSING: (
        "Place the SAM 3.1 checkpoint (sam3.1_multiplex.pt with its config"
        " files) inside the root folder. LoomStroke never downloads"
        " checkpoints automatically."
    ),
    PYTHON_UNSUPPORTED: ("Install Python 3.12 or newer (the py launcher must find it)."),
    VENV_CREATE_FAILED: "Check disk space and folder permissions, then retry Setup.",
    PIP_FAILED: "pip could not be initialized in the runtime; retry Setup.",
    TORCH_INSTALL_FAILED: (
        "PyTorch installation failed; check the setup log and network, then retry."
    ),
    CUDA_UNAVAILABLE: (
        "No CUDA GPU was detected. SAM 3.1 video tracking requires CUDA;"
        " image segmentation would be CPU-only and slow."
    ),
    CUDA_MISMATCH: (
        "The installed PyTorch CUDA build does not match this driver;"
        " update the GPU driver or retry Setup to reinstall."
    ),
    SAM31_INSTALL_FAILED: (
        "Installing the sam3 package failed; check the setup log."
        " Installing from runtime/sam3-src also requires permission for missing dependencies."
    ),
    COMPATIBILITY_UNSUPPORTED: (
        "The installed sam3 version is not recognized by the compatibility"
        " layer; report this so a new shim can be validated."
    ),
    WORKER_START_FAILED: "The SAM 3.1 worker failed to start; see the worker log.",
    HEALTH_FAILED: "The worker started but did not answer the health check.",
    MODEL_LOAD_FAILED: "Loading the SAM 3.1 model failed; see the worker log.",
    OOM: "The GPU ran out of memory; close other GPU applications and retry.",
    NETWORK_CONSENT_REQUIRED: (
        "Allow external services in Preferences > Privacy before installing"
        " missing packages, or prepare all runtime dependencies in advance"
        " to use Setup offline. A local sam3 source copy alone is insufficient."
    ),
    CANCELLED: "Setup was cancelled.",
}


class Sam31SetupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.action = ACTIONS.get(code, "")
        super().__init__(message)


__all__ = [name for name in dir() if name.isupper()] + ["Sam31SetupError", "ACTIONS"]
