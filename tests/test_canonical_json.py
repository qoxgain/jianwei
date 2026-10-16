"""Cross-runtime hashes must identify the same semantic content."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from packages.config.errors import ContractError
from packages.knowledge.service import content_hash
from scripts.export_schemas import schemas


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for cross-runtime conformance")
def test_semantic_hash_matches_browser_json_for_float_boundaries_and_unicode():
    value = {
        "numbers": [1.0, 0.0, -0.0, 1e-7, 1e-6, 1e20, 1e21, 1e-27, 1.2345678901234568e29],
        "nested": {"\uffff": "末尾", "\U0001f600": "动作\n\"名称\"", "a": True},
    }
    javascript = """
      const fs = require('fs');
      function canonical(value) {
        if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
        if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
        return JSON.stringify(value);
      }
      process.stdout.write(canonical(JSON.parse(fs.readFileSync(0, 'utf8'))));
    """
    browser_json = subprocess.run(
        ["node", "-e", javascript], input=json.dumps(value), text=True,
        capture_output=True, check=True,
    ).stdout
    assert content_hash(value) == hashlib.sha256(browser_json.encode()).hexdigest()
    assert content_hash({"seconds": 1}) == content_hash({"seconds": 1.0})


@pytest.mark.parametrize("value", [2**53, float("nan"), float("inf"), "\ud800"])
def test_unrepresentable_hash_values_return_controlled_error(value):
    with pytest.raises(ContractError) as caught:
        content_hash({"value": value})
    assert caught.value.code == "CANONICAL_VALUE_INVALID"


def test_committed_product_schemas_match_shared_models():
    directory = Path(__file__).resolve().parents[1] / "schemas"
    for name, schema in schemas().items():
        assert json.loads((directory / f"{name}.schema.json").read_text()) == schema
