#!/usr/bin/env bash
# Disposable GitHub runner only. Fail visibly; never continue without packages.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  echo 'This bounded installer is for disposable GitHub Actions runners only.' >&2
  exit 2
fi
if [[ "$#" -eq 0 ]]; then
  echo 'At least one package is required.' >&2
  exit 2
fi
ci_apt_options=(-o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
sudo timeout --kill-after=10s 180s apt-get "${ci_apt_options[@]}" update
sudo timeout --kill-after=10s 180s apt-get "${ci_apt_options[@]}" install -y "$@"
