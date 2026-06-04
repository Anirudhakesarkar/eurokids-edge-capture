# Eurokids Edge Capture

Standalone Linux service: pull RTSP from school cameras, sample JPEGs (~1/min), upload to **AWS S3** or **Cloudflare R2**.

Not part of the VMS desktop app. Copy this folder to the POC edge server.

## Requirements

- Python 3.10+
- **FFmpeg** on `PATH` (`sudo apt install ffmpeg`)
- Network: RTSP to cameras + HTTPS to S3/R2

## Quick start (office / Windows dev)

```powershell
cd d:\eurokids-edge-capture
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# Local capture only (no upload)
python -m capture_collector --once --camera-id test --rtsp "C:\path\to\sample.mp4" --dry-run

# One frame + S3 upload (set env first)
$env:AWS_ACCESS_KEY_ID="..."
$env:AWS_SECRET_ACCESS_KEY="..."
$env:AWS_DEFAULT_REGION="ap-south-1"
$env:EUROKIDS_S3_BUCKET="eurokids-poc"
python -m capture_collector --once --camera-id test --rtsp "C:\path\to\sample.mp4" --site-id test-site
```

## Config

1. Copy `config.example.yaml` → `/etc/eurokids/cameras.yaml` on the server.
2. Set RTSP URLs via environment variables (see example `${RTSP_CAM_PLAYGROUND}`).
3. Create spool directory: `sudo mkdir -p /var/lib/eurokids-capture/spool`

## Run on Linux (5 cameras)

```bash
cd /opt/eurokids/capture
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=ap-south-1
export EUROKIDS_S3_BUCKET=eurokids-poc
# R2 only:
# export AWS_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com

export RTSP_CAM_PLAYGROUND="rtsp://user:pass@192.168.1.101/..."
# ... other cameras

python -m capture_collector --config /etc/eurokids/cameras.yaml
```

Object keys:

```text
s3://{bucket}/raw/{site_id}/{camera_id}/{YYYY-MM-DD}/{timestamp}.jpg
```

## systemd

See `deploy/eurokids-capture.service`. Install:

```bash
sudo cp deploy/eurokids-capture.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eurokids-capture
sudo journalctl -u eurokids-capture -f
```

## Architecture

- **One process**, one thread per camera (RTSP + FFmpeg snapshot).
- **Shared upload queue** (2 worker threads) so slow S3 does not block capture.
- On RTSP failure: log, exponential backoff, retry.

## Env reference

| Variable | Purpose |
|----------|---------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 or R2 API keys |
| `AWS_DEFAULT_REGION` | e.g. `ap-south-1` |
| `AWS_ENDPOINT_URL` | R2 endpoint (optional) |
| `RTSP_CAM_*` | Expanded in YAML `${RTSP_CAM_PLAYGROUND}` |

Bucket name is set in YAML `bucket:` (not env), unless you only use `--once` with defaults.
