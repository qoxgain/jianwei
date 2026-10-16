"""Product YAML must have the same scalar meaning in Python and the browser."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from packages.config.yaml import parse_yaml
from packages.knowledge.service import parse_knowledge


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps/web"


def browser_yaml(source):
    if shutil.which("node") is None or not (WEB / "node_modules/yaml").is_dir():
        pytest.skip("Node and frontend YAML dependency required for cross-parser conformance")
    return json.loads(subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {parse} from 'yaml'; import fs from 'node:fs'; process.stdout.write(JSON.stringify(parse(fs.readFileSync(0,'utf8'))));"],
        input=source, cwd=WEB, text=True, capture_output=True, check=True,
    ).stdout)


def test_core_scalars_match_frontend_without_yaml_11_coercion():
    source = """values:
  - on
  - yes
  - no
  - OFF
  - 2026-10-16
  - true
  - FALSE
  - 012
  - 0o12
  - 0x12
  - -0o12
  - 0b10
  - 1_000
  - 12:34
  - 1e-7
  - 1E2
  - +.5
  - 1.
  - null
"""
    parsed = parse_yaml(source)
    assert parsed["values"] == [
        "on", "yes", "no", "OFF", "2026-10-16", True, False, 12, 10, 18,
        "-0o12", "0b10", "1_000", "12:34", 1e-7, 100.0, 0.5, 1.0, None,
    ]
    assert parsed == browser_yaml(source)
    # Product resolver changes never mutate the supplied reference parser.
    assert yaml.safe_load("name: on")["name"] is True


def test_unquoted_on_name_and_date_description_remain_knowledge_strings():
    labels = parse_yaml((ROOT / "knowledge/labels.yaml").read_text())
    labels["actions"][0]["name"] = "on"
    labels["actions"][0]["description"] = "2026-10-16"
    source = yaml.safe_dump(labels, sort_keys=False, allow_unicode=True)
    source = source.replace("name: 'on'", "name: on").replace("description: '2026-10-16'", "description: 2026-10-16")
    knowledge = parse_knowledge(source)
    assert knowledge.actions[0].name == "on"
    assert knowledge.actions[0].description == "2026-10-16"
    assert knowledge.model_dump() == browser_yaml(source)
