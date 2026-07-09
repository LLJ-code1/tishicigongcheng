"""Standalone JoyCaption worker reusing the existing local model."""

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
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from vision_worker_common import encode_worker_result, validate_image_path


SYSTEM_PROMPT = (
    "You are a precise image captioner for image-generation prompt extraction."
)
USER_PROMPT = (
    "Describe only visible subjects, appearance, clothing, pose, interaction, "
    "scene, composition, lighting, effects, and art style. Do not invent names."
)


def resolve_joycaption_model(
    candidates: Iterable[Path],
    exists: Callable[[Path], bool] = Path.is_dir,
) -> Path | None:
    paths = list(candidates)
    paths.sort(
        key=lambda path: (
            "fp8-dynamic" not in path.name.casefold(),
            "beta" not in path.name.casefold(),
        )
    )
    return next((path for path in paths if exists(path)), None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = process.stderr.read()[-1200:] if process.stderr else ""
            raise RuntimeError(
                f"JoyCaption llama-server exited during startup: {detail}"
            )
        try:
            with urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise RuntimeError("JoyCaption llama-server startup timed out")


def analyze_gguf(
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
            "4096",
            "--n-gpu-layers",
            "0",
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
        _wait_for_server(base_url, process, 180)
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
                "max_tokens": 384,
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
        with urlopen(request, timeout=900) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload["choices"][0]["message"]["content"]).strip()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def analyze_hf(image_path: Path, model_path: Path) -> str:
    import torch
    from PIL import Image
    from transformers import AutoProcessor, LlavaForConditionalGeneration

    processor = AutoProcessor.from_pretrained(
        str(model_path),
        use_fast=False,
        image_processor_type="CLIPImageProcessor",
        image_size=336,
        local_files_only=True,
    )
    model = LlavaForConditionalGeneration.from_pretrained(
        str(model_path),
        dtype="auto",
        device_map="cuda" if torch.cuda.is_available() else "cpu",
        local_files_only=True,
    ).eval()
    conversation = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]
    prompt = processor.apply_chat_template(
        conversation,
        tokenize=False,
        add_generation_prompt=True,
    )
    image = Image.open(image_path).convert("RGB")
    inputs = processor(text=[prompt], images=[image], return_tensors="pt")
    device = next(model.parameters()).device
    inputs = inputs.to(device)
    if inputs.get("pixel_values") is not None:
        inputs["pixel_values"] = inputs["pixel_values"].to(model.dtype)
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=384,
            do_sample=False,
            use_cache=True,
        )[0]
    generated = generated[inputs["input_ids"].shape[1] :]
    return processor.tokenizer.decode(
        generated,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--mmproj")
    parser.add_argument("--server")
    args = parser.parse_args()
    started = time.perf_counter()
    image_path = validate_image_path(args.image)
    if args.mmproj and args.server:
        result = analyze_gguf(
            image_path,
            Path(args.model),
            Path(args.mmproj),
            Path(args.server),
        )
    else:
        result = analyze_hf(image_path, Path(args.model))
    print(
        encode_worker_result(
            "joycaption",
            result,
            time.perf_counter() - started,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
