"""The documented command line must preserve failure status."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("profile", ["demo", "lab", "site"])
def test_static_doctor(profile):
    run = subprocess.run([sys.executable, "-m", "jianwei", "doctor", "--profile", profile], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 0
    output = json.loads(run.stdout)
    assert output["status"] == "PASS"
    assert output["profile"] == profile
    assert len(output["config_hash"]) == 64


def test_site_preflight_is_blocked():
    run = subprocess.run([sys.executable, "-m", "jianwei", "doctor", "--profile", "site", "--preflight"], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["status"] == "BLOCKED"


def test_site_serve_is_not_silently_demo():
    run = subprocess.run([sys.executable, "-m", "jianwei", "serve", "--profile", "site"], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["detail"]["code"] == "PROFILE_SERVICE_UNAVAILABLE"


def test_missing_config_is_explicit():
    run = subprocess.run([sys.executable, "-m", "jianwei", "doctor", "--config", "/tmp/jianwei-absent-config-7811.yaml"], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["detail"]["code"] == "CONFIG_FILE_MISSING"
