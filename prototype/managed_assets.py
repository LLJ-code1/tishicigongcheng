"""Safe local PNG asset storage for Prompt Studio.

SQLite stores only immutable metadata and relative paths.  Source PNGs and their
thumbnails live in the managed asset directory and are never embedded in the DB.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

import db


ROOT = Path(__file__).resolve().parent
DEFAULT_ASSET_ROOT = ROOT / "data" / "managed-assets"
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_ASSET_PIXELS = 40_000_000
THUMBNAIL_SIZE = (480, 480)


class ManagedAssetError(ValueError):
    """Raised for invalid asset input or a safe storage failure."""


def asset_root() -> Path:
    configured = os.environ.get("PROMPT_STUDIO_MANAGED_ASSETS")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_ASSET_ROOT


def _validate_png(image_bytes: bytes) -> tuple[int, int]:
    if not image_bytes or len(image_bytes) > MAX_ASSET_BYTES:
        raise ManagedAssetError("PNG 资产为空或超过 25 MiB 限制")
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ManagedAssetError("受管理资产当前仅接受真实 PNG 文件")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format != "PNG":
                raise ManagedAssetError("受管理资产当前仅接受真实 PNG 文件")
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_ASSET_PIXELS:
                raise ManagedAssetError("PNG 尺寸或像素数无效")
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
    except ManagedAssetError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ManagedAssetError("PNG 文件损坏或无法完整解码") from error
    return width, height


def _safe_path(root: Path, relative_path: str) -> Path:
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ManagedAssetError("资产路径越出受管理目录") from error
    return path


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _thumbnail_bytes(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail(THUMBNAIL_SIZE)
        output = BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
        return output.getvalue()


def import_png(image_bytes: bytes, db_path: Path | str | None = None) -> tuple[dict, bool]:
    """Import a PNG by content hash, returning ``(asset, created)``."""

    width, height = _validate_png(image_bytes)
    sha256 = hashlib.sha256(image_bytes).hexdigest()
    existing = db.get_managed_asset_by_sha256(sha256, db_path)
    if existing is not None:
        return existing, False

    root = asset_root()
    relative_path = f"files/{sha256[:2]}/{sha256}.png"
    thumbnail_relative_path = f"thumbnails/{sha256[:2]}/{sha256}.jpg"
    source_path = _safe_path(root, relative_path)
    thumbnail_path = _safe_path(root, thumbnail_relative_path)
    if not source_path.exists():
        _atomic_write(source_path, image_bytes)
    if not thumbnail_path.exists():
        _atomic_write(thumbnail_path, _thumbnail_bytes(image_bytes))
    asset, created = db.register_managed_asset(
        {
            "id": f"asset-{sha256[:32]}",
            "sha256": sha256,
            "relativePath": relative_path,
            "thumbnailRelativePath": thumbnail_relative_path,
            "mimeType": "image/png",
            "byteSize": len(image_bytes),
            "width": width,
            "height": height,
        },
        db_path,
    )
    return asset, created


def read_asset_file(asset: dict, *, thumbnail: bool = False) -> bytes:
    key = "thumbnailRelativePath" if thumbnail else "relativePath"
    path = _safe_path(asset_root(), str(asset.get(key, "")))
    try:
        return path.read_bytes()
    except OSError as error:
        raise ManagedAssetError("受管理资产文件不存在或无法读取") from error


def delete_asset_files(asset: dict) -> None:
    """Delete exactly the two known files after an explicit confirmation."""

    root = asset_root()
    paths = [
        _safe_path(root, str(asset.get("relativePath", ""))),
        _safe_path(root, str(asset.get("thumbnailRelativePath", ""))),
    ]
    for path in paths:
        if path.exists():
            try:
                path.unlink()
            except OSError as error:
                raise ManagedAssetError("无法删除受管理资产文件") from error
