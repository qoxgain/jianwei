#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
npm --prefix apps/web ci --cache "$repo_root/.cache/npm"
npm --prefix apps/web run build
