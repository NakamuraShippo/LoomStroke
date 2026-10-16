"""JSONL stdio entry point for the SAM31 worker.

Runs inside the dedicated SAM 3.1 environment:

    <venv_python> -u -m loomstroke.sam31.worker_process \
        --checkpoint <path> --state-dir <dir> [--log-file <path>]

One JSON request per stdin line; one JSON response per stdout line. All
logging goes to stderr (and optionally a log file) so stdout stays a clean
protocol channel.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from typing import Any

from loomstroke.sam31 import worker_protocol as protocol
from loomstroke.sam31.worker_process import image_api, video_api
from loomstroke.sam31.worker_process.runtime import Sam31WorkerRuntime, WorkerError

logger = logging.getLogger("loomstroke.sam31.worker")


def _configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        with contextlib.suppress(OSError):
            handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _dispatch(  # noqa: PLR0911 - one return per protocol command
    runtime: Sam31WorkerRuntime, request: dict[str, Any]
) -> dict[str, Any]:
    command = request.get("cmd")
    if command == protocol.CMD_HEALTH:
        return runtime.health()
    if command == protocol.CMD_CAPABILITIES:
        return runtime.capabilities()
    if command == protocol.CMD_RUNTIME_INFO:
        return runtime.runtime_info()
    if command == protocol.CMD_LOAD_MODEL:
        return runtime.load_model(request.get("checkpointPath"))
    if command == protocol.CMD_UNLOAD_MODEL:
        return runtime.unload_model()
    if command == protocol.CMD_MODEL_STATUS:
        return {"modelState": runtime.model_state, "modelError": runtime.model_error}
    handler = image_api.HANDLERS.get(command) or video_api.HANDLERS.get(command)
    if handler is not None:
        return handler(runtime, request)
    raise WorkerError(protocol.ERR_UNKNOWN_COMMAND, f"unknown command: {command!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--log-file", default=None)
    arguments = parser.parse_args()
    _configure_logging(arguments.log_file)

    runtime = Sam31WorkerRuntime(arguments.checkpoint, arguments.state_dir)
    logger.info("sam31 worker ready (checkpoint=%s)", arguments.checkpoint)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            if request.get("cmd") == protocol.CMD_SHUTDOWN:
                print(json.dumps({"id": request_id, "ok": True, "result": {}}), flush=True)
                break
            result = _dispatch(runtime, request)
            response = {"id": request_id, "ok": True, "result": result}
        except WorkerError as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": error.code, "message": str(error)},
            }
        except json.JSONDecodeError as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": protocol.ERR_INVALID_REQUEST,
                    "message": f"invalid JSON: {error}",
                },
            }
        except Exception as error:  # noqa: BLE001 - the loop must survive
            logger.exception("unhandled worker error")
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": protocol.ERR_INTERNAL,
                    "message": f"{type(error).__name__}: {error}",
                },
            }
        print(json.dumps(response), flush=True)

    with contextlib.suppress(Exception):
        runtime.unload_model()
    logger.info("sam31 worker shut down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
