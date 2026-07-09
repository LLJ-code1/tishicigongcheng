"""Manage the project-local llama.cpp text model service."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_FILENAME = "Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf"
MODEL_ALIAS = MODEL_FILENAME
SHARED_LLAMA_DIR = Path(
    os.environ.get(
        "PROMPT_STUDIO_LLAMA_DIR",
        r"H:\AI\apps\llama.cpp\b9442-cuda12.4",
    )
)
SHARED_MODEL_PATH = Path(
    os.environ.get(
        "PROMPT_STUDIO_LLM_MODEL",
        rf"H:\AI\models\llm\{MODEL_FILENAME}",
    )
)


@dataclass
class LocalLlmError(Exception):
    message: str
    code: str = "local_llm_error"
    status: int = 500

    def __str__(self) -> str:
        return self.message


def runtime_paths(project_root: Path = PROJECT_ROOT) -> dict[str, Path]:
    root = project_root.resolve()
    data = root / "prototype" / "data"
    use_shared_runtime = root == PROJECT_ROOT.resolve()
    runtime = SHARED_LLAMA_DIR if use_shared_runtime else root / "runtime" / "llama.cpp"
    model = SHARED_MODEL_PATH if use_shared_runtime else root / "models" / "text" / MODEL_FILENAME
    return {
        "root": root,
        "runtime": runtime,
        "executable": runtime / "llama-server.exe",
        "model": model,
        "pid": data / "local_llm.pid",
        "stdout": data / "local_llm.stdout.log",
        "stderr": data / "local_llm.stderr.log",
    }


def build_llama_command(paths: dict[str, Path], port: int = 8080) -> list[str]:
    return [
        str(paths["executable"]),
        "--model",
        str(paths["model"]),
        "--alias",
        MODEL_ALIAS,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        "8192",
        "--parallel",
        "1",
        "--flash-attn",
        "auto",
        "--jinja",
        "--chat-template-kwargs",
        '{"enable_thinking":false}',
        "--threads",
        "12",
        "--reasoning",
        "off",
        "--reasoning-budget",
        "0",
        "-ngl",
        "99",
    ]


def probe_local_llm(port: int = 8080, timeout: float = 1.5) -> bool:
    request = Request(
        f"http://127.0.0.1:{port}/v1/models",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (URLError, TimeoutError, OSError):
        return False


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
        return value if value > 0 else None
    except (OSError, ValueError):
        return None


def local_llm_status(project_root: Path = PROJECT_ROOT) -> dict:
    paths = runtime_paths(project_root)
    running = probe_local_llm()
    model_size = paths["model"].stat().st_size if paths["model"].exists() else 0
    return {
        "installed": paths["executable"].is_file(),
        "modelInstalled": paths["model"].is_file(),
        "running": running,
        "model": MODEL_ALIAS,
        "modelFile": paths["model"].name,
        "modelBytes": model_size,
        "endpoint": "http://127.0.0.1:8080/v1",
        "pid": read_pid(paths["pid"]),
        "message": (
            "现有本地模型运行中"
            if running
            else "运行时或模型尚未安装"
            if not paths["executable"].is_file() or not paths["model"].is_file()
            else "本地模型已安装，当前未启动"
        ),
    }


def start_local_llm(
    project_root: Path = PROJECT_ROOT,
    ready_timeout: float = 120,
) -> dict:
    paths = runtime_paths(project_root)
    if probe_local_llm():
        return local_llm_status(project_root)
    if not paths["executable"].is_file() or not paths["model"].is_file():
        raise LocalLlmError(
            "未找到现有本地 LLM，请检查 H:\\AI 下的 llama.cpp 与 GGUF 模型路径",
            code="runtime_not_installed",
            status=409,
        )

    paths["pid"].parent.mkdir(parents=True, exist_ok=True)
    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    with paths["stdout"].open("ab") as stdout, paths["stderr"].open("ab") as stderr:
        process = subprocess.Popen(
            build_llama_command(paths),
            cwd=paths["runtime"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creation_flags,
            close_fds=True,
        )
    paths["pid"].write_text(str(process.pid), encoding="ascii")

    deadline = time.monotonic() + max(0, ready_timeout)
    while time.monotonic() < deadline:
        if probe_local_llm(timeout=1):
            return local_llm_status(project_root)
        if process.poll() is not None:
            raise LocalLlmError(
                "llama-server 启动失败，请检查 prototype/data/local_llm.stderr.log",
                code="runtime_start_failed",
                status=500,
            )
        time.sleep(0.5)

    if ready_timeout <= 0:
        return local_llm_status(project_root)
    raise LocalLlmError(
        "本地模型仍在加载，稍后点击“测试”检查状态",
        code="runtime_start_timeout",
        status=504,
    )


def stop_local_llm(project_root: Path = PROJECT_ROOT) -> dict:
    paths = runtime_paths(project_root)
    pid = read_pid(paths["pid"])
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        paths["pid"].unlink()
    except OSError:
        pass

    deadline = time.monotonic() + 8
    while probe_local_llm(timeout=0.5) and time.monotonic() < deadline:
        time.sleep(0.25)
    status = local_llm_status(project_root)
    status["message"] = "本地模型已停止，显存已释放" if not status["running"] else "模型仍在停止中"
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "start", "stop"))
    args = parser.parse_args()
    try:
        if args.action == "start":
            result = start_local_llm()
        elif args.action == "stop":
            result = stop_local_llm()
        else:
            result = local_llm_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except LocalLlmError as error:
        print(
            json.dumps(
                {"error": error.message, "code": error.code},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
