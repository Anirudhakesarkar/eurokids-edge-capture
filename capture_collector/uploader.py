from __future__ import annotations

import logging
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import NamedTuple, TypeAlias

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class UploadJob(NamedTuple):
    local_path: Path
    object_key: str


UploadQueue: TypeAlias = Queue[UploadJob | None]


def make_upload_queue(maxsize: int = 500) -> UploadQueue:
    return Queue(maxsize=maxsize)


def enqueue_upload(q: UploadQueue, job: UploadJob) -> None:
    q.put(job, block=True)


def close_upload_queue(q: UploadQueue) -> None:
    q.put(None, block=True)


def _s3_client():
    endpoint = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL")
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-south-1"
    kwargs: dict = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(
        "s3",
        config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}),
        **kwargs,
    )


def run_uploader(
    bucket: str,
    queue: UploadQueue,
    stop: Event,
    *,
    dry_run: bool = False,
) -> None:
    if dry_run:
        logger.info("dry-run: uploads disabled")
        while not stop.is_set():
            try:
                job = queue.get(timeout=1.0)
            except Empty:
                continue
            if job is None:
                break
            logger.info("dry-run would upload %s -> s3://%s/%s", job.local_path, bucket, job.object_key)
            try:
                job.local_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("could not remove spool file %s: %s", job.local_path, e)
        return

    if not bucket:
        logger.error("bucket is empty; set bucket in config or skip uploads with --dry-run")
        return

    client = _s3_client()
    backoff = 2.0

    while not stop.is_set():
        try:
            job = queue.get(timeout=1.0)
        except Empty:
            continue
        if job is None:
            break
        if not job.local_path.is_file():
            logger.warning("missing spool file %s", job.local_path)
            continue
        try:
            client.upload_file(
                str(job.local_path),
                bucket,
                job.object_key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            logger.info("uploaded s3://%s/%s", bucket, job.object_key)
            job.local_path.unlink(missing_ok=True)
            backoff = 2.0
        except ClientError as e:
            logger.error("upload failed %s: %s (retry in %.0fs)", job.object_key, e, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 120.0)
            queue.put(job)
        except OSError as e:
            logger.error("upload/io error %s: %s", job.local_path, e)


def start_uploader_threads(
    bucket: str,
    queue: UploadQueue,
    stop: Event,
    *,
    workers: int = 2,
    dry_run: bool = False,
) -> list[Thread]:
    threads: list[Thread] = []
    for i in range(workers):
        t = Thread(
            target=run_uploader,
            name=f"uploader-{i}",
            args=(bucket, queue, stop),
            kwargs={"dry_run": dry_run},
            daemon=True,
        )
        t.start()
        threads.append(t)
    return threads
