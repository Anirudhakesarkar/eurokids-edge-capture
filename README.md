# Eurokids Edge Capture

Standalone Linux service: pull RTSP from school cameras, sample JPEGs (~1/min), upload to **Cloudflare R2**.

## Requirements

- Python 3.10+
- **FFmpeg** on `PATH` (`sudo apt install ffmpeg`)
- Network: RTSP to cameras + HTTPS to R2

## Setup (edge server)

```bash
git clone https://github.com/Anirudhakesarkar/eurokids-edge-capture.git
cd eurokids-edge-capture
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp cameras.example.yaml cameras.yaml
cp .env.example .env
# Edit .env with R2 keys; edit cameras.yaml if needed

sudo mkdir -p /var/lib/eurokids-capture/spool
```

## Run

```bash
source venv/bin/activate
set -a && source .env && set +a

# Test one frame
python -m capture_collector --once \
  --camera-id cam-main-gate \
  --rtsp "rtsp://..." \
  --site-id eurokids-mulund -v

# Continuous (5 cameras)
python -m capture_collector --config cameras.yaml -v
```

## Schedule

Configured in `cameras.yaml`:

- **Main Gate** — `always_on: true` (24/7)
- **Other cameras** — `active_hours` 06:30–17:00 Asia/Kolkata (every day)

Process can run 24/7 via systemd; cameras sleep outside their window automatically.

## systemd

```bash
sudo cp deploy/eurokids-capture.service /etc/systemd/system/
sudo cp cameras.yaml /etc/eurokids/cameras.yaml
sudo cp .env /etc/eurokids/capture.env
sudo chmod 600 /etc/eurokids/capture.env
sudo systemctl daemon-reload
sudo systemctl enable --now eurokids-capture
sudo journalctl -u eurokids-capture -f
```

## R2 object layout

```text
orionalerts/raw/{site_id}/{camera_id}/{YYYY-MM-DD}/{timestamp}.jpg
```

Local spool files are deleted after successful upload.

## Env reference

| Variable | Purpose |
|----------|---------|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | R2 API keys |
| `AWS_DEFAULT_REGION` | `auto` for Cloudflare R2 |
| `AWS_ENDPOINT_URL` | R2 S3 endpoint URL |

Bucket name is set in YAML `bucket:`.
