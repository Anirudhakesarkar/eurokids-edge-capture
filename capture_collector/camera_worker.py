from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable

from .config import CameraConfig, CollectorConfig
from .schedule import wait_until_active

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def spool_path(cfg: CollectorConfig, camera: CameraConfig, when: datetime | None = None) -> Path:
    ts = when or _utc_now()
    day = ts.strftime("%Y-%m-%d")
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    return (
        cfg.spool_dir
        / cfg.site_id
        / camera.id
        / day
        / f"{stamp}.jpg"
    )


def object_key(cfg: CollectorConfig, camera: CameraConfig, when: datetime | None = None) -> str:
    ts = when or _utc_now()
    day = ts.strftime("%Y-%m-%d")
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    prefix = cfg.key_prefix
    return f"{prefix}/{cfg.site_id}/{camera.id}/{day}/{stamp}.jpg"


def capture_jpeg(
    rtsp_url: str,
    *,
    rtsp_transport: str,
    jpeg_quality: int,
    timeout_sec: int,
) -> bytes:
    """Grab one JPEG frame from RTSP via FFmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        rtsp_transport,
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-q:v",
        str(jpeg_quality),
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout_sec + 5,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"ffmpeg exited {proc.returncode}")
    if not proc.stdout or len(proc.stdout) < 100:
        raise RuntimeError("ffmpeg returned empty or tiny JPEG payload")
    return proc.stdout


def run_camera_worker(
    cfg: CollectorConfig,
    camera: CameraConfig,
    stop: Event,
    on_frame: Callable[[Path, str], None],
) -> None:
    """
    Loop: sample RTSP → write spool JPEG → callback(local_path, s3_key).
    Reconnects on errors with backoff.
    """
    backoff = 5.0
    max_backoff = 300.0
    log = logging.getLogger(f"camera.{camera.id}")

    while not stop.is_set():
        if not wait_until_active(cfg.active_hours, stop, log=log):
            break
        try:
            when = _utc_now()
            jpeg = capture_jpeg(
                camera.rtsp_url,
                rtsp_transport=cfg.rtsp_transport,
                jpeg_quality=cfg.jpeg_quality,
                timeout_sec=cfg.ffmpeg_timeout_sec,
            )
            dest = spool_path(cfg, camera, when)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(jpeg)
            key = object_key(cfg, camera, when)
            on_frame(dest, key)
            log.info("captured %s (%d bytes) -> %s", camera.name, len(jpeg), key)
            backoff = 5.0
        except Exception as e:
            log.warning("capture failed: %s (retry in %.0fs)", e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue

        stop.wait(cfg.sample_interval_sec)
