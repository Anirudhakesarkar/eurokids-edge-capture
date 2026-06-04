from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from threading import Event, Thread

from .camera_worker import run_camera_worker
from .config import CollectorConfig, load_config, single_camera_config
from .uploader import (
    UploadJob,
    close_upload_queue,
    enqueue_upload,
    make_upload_queue,
    start_uploader_threads,
)

logger = logging.getLogger("capture_collector")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _run_collector(cfg: CollectorConfig) -> int:
    stop = Event()
    upload_q = make_upload_queue()

    def on_frame(local_path: Path, object_key: str) -> None:
        enqueue_upload(upload_q, UploadJob(local_path=local_path, object_key=object_key))

    def handle_signal(*_args: object) -> None:
        logger.info("shutdown requested")
        stop.set()
        close_upload_queue(upload_q)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cfg.spool_dir.mkdir(parents=True, exist_ok=True)
    ah = cfg.active_hours
    ah_msg = (
        f"{ah.label} ({ah.timezone or 'local'})"
        if ah
        else "always"
    )
    logger.info(
        "starting site=%s cameras=%d interval=%ss spool=%s bucket=%s "
        "active_hours=%s dry_run=%s",
        cfg.site_id,
        len(cfg.cameras),
        cfg.sample_interval_sec,
        cfg.spool_dir,
        cfg.bucket or "(none)",
        ah_msg,
        cfg.dry_run,
    )

    uploaders = start_uploader_threads(
        cfg.bucket,
        upload_q,
        stop,
        workers=min(2, max(1, len(cfg.cameras))),
        dry_run=cfg.dry_run,
    )

    workers: list[Thread] = []
    for cam in cfg.cameras:
        t = Thread(
            target=run_camera_worker,
            name=f"capture-{cam.id}",
            args=(cfg, cam, stop, on_frame),
            daemon=True,
        )
        t.start()
        workers.append(t)

    try:
        while not stop.is_set():
            stop.wait(1.0)
    finally:
        stop.set()
        close_upload_queue(upload_q)
        for t in workers:
            t.join(timeout=30)
        for t in uploaders:
            t.join(timeout=60)
    logger.info("stopped")
    return 0


def _run_once(cfg: CollectorConfig, camera_id: str, rtsp_url: str) -> int:
    from .camera_worker import capture_jpeg, object_key, spool_path
    from .config import CameraConfig

    cam = next((c for c in cfg.cameras if c.id == camera_id), None)
    if cam is None:
        cam = CameraConfig(id=camera_id, name=camera_id, rtsp_url=rtsp_url)
    elif rtsp_url:
        cam = CameraConfig(id=cam.id, name=cam.name, rtsp_url=rtsp_url)

    cfg.spool_dir.mkdir(parents=True, exist_ok=True)
    jpeg = capture_jpeg(
        cam.rtsp_url,
        rtsp_transport=cfg.rtsp_transport,
        jpeg_quality=cfg.jpeg_quality,
        timeout_sec=cfg.ffmpeg_timeout_sec,
    )
    dest = spool_path(cfg, cam)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(jpeg)
    key = object_key(cfg, cam)
    logger.info("wrote %s (%d bytes) key=%s", dest, len(jpeg), key)

    if cfg.dry_run or not cfg.bucket:
        return 0

    import boto3
    from botocore.config import Config as BotoConfig

    endpoint = __import__("os").environ.get("AWS_ENDPOINT_URL")
    region = __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-south-1")
    kwargs = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    client = boto3.client("s3", config=BotoConfig(retries={"max_attempts": 3}), **kwargs)
    client.upload_file(str(dest), cfg.bucket, key, ExtraArgs={"ContentType": "image/jpeg"})
    logger.info("uploaded s3://%s/%s", cfg.bucket, key)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eurokids edge RTSP frame collector (JPEG to spool to S3/R2)",
    )
    parser.add_argument("--config", type=Path, help="YAML config path")
    parser.add_argument("--dry-run", action="store_true", help="Capture locally; do not upload")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--once", action="store_true", help="Single frame then exit")
    parser.add_argument("--camera-id", help="With --once: camera id")
    parser.add_argument("--rtsp", help="With --once: RTSP URL or path to video file")
    parser.add_argument("--site-id", default="test-site", help="With --once: site id")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    if args.once:
        if not args.camera_id or not args.rtsp:
            parser.error("--once requires --camera-id and --rtsp")
        cfg = single_camera_config(
            site_id=args.site_id,
            camera_id=args.camera_id,
            rtsp_url=args.rtsp,
            bucket=os.environ.get("EUROKIDS_S3_BUCKET", ""),
            dry_run=args.dry_run,
        )
        try:
            return _run_once(cfg, args.camera_id, args.rtsp)
        except Exception as e:
            logger.error("%s", e)
            return 1

    if not args.config:
        parser.error("--config is required unless using --once")

    try:
        cfg = load_config(args.config, dry_run=args.dry_run)
    except Exception as e:
        logger.error("config error: %s", e)
        return 1

    try:
        return _run_collector(cfg)
    except Exception as e:
        logger.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
