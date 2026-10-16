"""Generate product JSON Schemas from the shared Python contracts.

Run with ``python -m scripts.export_schemas`` from the checkout root.
The separate contracts/openapi.yaml remains the supplied future API specification;
the running service exposes its implemented API at /openapi.json.
"""
import json
from pathlib import Path

from packages.contracts.models import Forecast, Knowledge, MemoryPatch, SystemConfig, TeacherResult


ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "system": SystemConfig,
    "labels": Knowledge,
    "memory_patch": MemoryPatch,
    "teacher_result": TeacherResult,
    "forecast": Forecast,
}


def schemas():
    return {
        name: {**model.model_json_schema(), "$schema": "https://json-schema.org/draft/2020-12/schema"}
        for name, model in MODELS.items()
    }


def main():
    directory = ROOT / "schemas"
    directory.mkdir(exist_ok=True)
    for name, schema in schemas().items():
        (directory / f"{name}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Generated {len(MODELS)} product JSON Schemas")


if __name__ == "__main__":
    main()
