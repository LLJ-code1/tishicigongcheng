"""Standalone Qwen3-VL GGUF worker."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from vision_worker_common import encode_worker_result, validate_image_path


SYSTEM_PROMPT = (
    "You are a precise visual analyst for image-generation prompt extraction."
)
USER_PROMPT = (
    "Describe the visible subjects, appearance, clothing, actions, interactions, "
    "scene, composition, camera, lighting, effects, text, and art style. "
    "Separate facts from uncertain observations and do not invent identities."
)


def qwen_model_status(
    model_path: Path,
    mmproj_path: Path,
    exists: Callable[[Path], bool] = Path.is_file,
) -> dict:
    missing = [
        str(path)
        for path in (model_path, mmproj_path)
        if not exists(path)
    ]
    return {"installed": not missing, "missing": missing}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Qwen3-VL llama-server exited during startup")
        try:
            with urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise RuntimeError("Qwen3-VL llama-server startup timed out")


def analyze(
    image_path: Path,
    model_path: Path,
    mmproj_path: Path,
    server_path: Path,
) -> str:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    model_arg = os.path.relpath(model_path, Path.cwd())
    mmproj_arg = os.path.relpath(mmproj_path, Path.cwd())
    process = subprocess.Popen(
        [
            str(server_path),
            "--model",
            model_arg,
            "--mmproj",
            mmproj_arg,
            "--no-mmproj-offload",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            "8192",
            "--n-gpu-layers",
            "0",
            "--image-max-tokens",
            "4096",
            "--parallel",
            "1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    try:
        _wait_for_server(base_url, process, 120)
        body = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": USER_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{encoded}",
                                },
                            },
                        ],
                    },
                ],
                "max_tokens": 512,
                "temperature": 0,
                "seed": 42,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=480) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload["choices"][0]["message"]["content"]).strip()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--mmproj", required=True)
    parser.add_argument("--server", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    result = analyze(
        validate_image_path(args.image),
        Path(args.model),
        Path(args.mmproj),
        Path(args.server),
    )
    print(encode_worker_result("qwenvl", result, time.perf_counter() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
