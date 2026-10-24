#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
if [[ "${1:-}" == "--with-encoder" ]]; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements-encoder.txt
elif [[ -n "${1:-}" ]]; then
  echo 'Usage: bash scripts/install.sh [--with-encoder]' >&2
  exit 2
fi
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo 'MP4 imports require FFmpeg and FFprobe. Install the ffmpeg system package before video acceptance.' >&2
fi
npm --prefix apps/web ci --cache "$repo_root/.cache/npm"
npm --prefix apps/web run build
