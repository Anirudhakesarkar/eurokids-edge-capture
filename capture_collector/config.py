from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schedule import ActiveHours, parse_active_hours

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in os.environ:
            raise ValueError(f"Environment variable {key} is not set (used in config)")
        return os.environ[key]

    return _ENV_PATTERN.sub(repl, value)


def _expand_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _expand_env(obj) if "${" in obj else obj
    if isinstance(obj, list):
        return [_expand_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand_obj(v) for k, v in obj.items()}
    return obj


@dataclass(frozen=True)
class CameraConfig:
    id: str
    name: str
    rtsp_url: str


@dataclass(frozen=True)
class CollectorConfig:
    site_id: str
    sample_interval_sec: float
    jpeg_quality: int
    spool_dir: Path
    bucket: str
    key_prefix: str
    rtsp_transport: str
    ffmpeg_timeout_sec: int
    cameras: list[CameraConfig]
    dry_run: bool = False
    active_hours: ActiveHours | None = None


def load_config(path: Path, *, dry_run: bool = False) -> CollectorConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")
    raw = _expand_obj(raw)

    cameras_raw = raw.get("cameras") or []
    if not cameras_raw:
        raise ValueError("config.cameras must list at least one camera")

    cameras: list[CameraConfig] = []
    for i, c in enumerate(cameras_raw):
        if not isinstance(c, dict):
            raise ValueError(f"cameras[{i}] must be a mapping")
        cam_id = str(c.get("id", "")).strip()
        rtsp = str(c.get("rtsp_url", "")).strip()
        if not cam_id or not rtsp:
            raise ValueError(f"cameras[{i}] requires id and rtsp_url")
        cameras.append(
            CameraConfig(
                id=cam_id,
                name=str(c.get("name") or cam_id).strip(),
                rtsp_url=rtsp,
            )
        )

    spool = Path(str(raw.get("spool_dir") or "./spool")).expanduser()
    default_tz = str(raw.get("active_hours_timezone") or "").strip() or None
    active_hours = parse_active_hours(raw.get("active_hours"), default_tz=default_tz)

    return CollectorConfig(
        site_id=str(raw.get("site_id") or "site-unknown").strip(),
        sample_interval_sec=float(raw.get("sample_interval_sec") or 60),
        jpeg_quality=int(raw.get("jpeg_quality") or 2),
        spool_dir=spool,
        bucket=str(raw.get("bucket") or "").strip(),
        key_prefix=str(raw.get("key_prefix") or "raw").strip().strip("/"),
        rtsp_transport=str(raw.get("rtsp_transport") or "tcp").strip(),
        ffmpeg_timeout_sec=int(raw.get("ffmpeg_timeout_sec") or 15),
        cameras=cameras,
        dry_run=dry_run,
        active_hours=active_hours,
    )


def single_camera_config(
    *,
    site_id: str,
    camera_id: str,
    rtsp_url: str,
    sample_interval_sec: float = 60,
    spool_dir: Path | None = None,
    bucket: str = "",
    key_prefix: str = "raw",
    dry_run: bool = False,
) -> CollectorConfig:
    return CollectorConfig(
        site_id=site_id,
        sample_interval_sec=sample_interval_sec,
        jpeg_quality=2,
        spool_dir=spool_dir or Path("./spool"),
        bucket=bucket,
        key_prefix=key_prefix,
        rtsp_transport="tcp",
        ffmpeg_timeout_sec=15,
        cameras=[CameraConfig(id=camera_id, name=camera_id, rtsp_url=rtsp_url)],
        dry_run=dry_run,
    )
