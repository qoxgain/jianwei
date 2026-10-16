"""Public errors contain paths and safe explanations, never configuration inputs."""
from __future__ import annotations
from pydantic import ValidationError


class ContractError(ValueError):
    def __init__(self, code: str, message: str, errors: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.errors = errors or []

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "errors": self.errors}


def schema_errors(error: ValidationError) -> list[dict[str, str]]:
    # Pydantic's default error representation contains the input (potential secret).
    # Only include locations/types and our controlled semantic messages.
    result = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        message = item["msg"] if item["type"] == "value_error" else {
            "extra_forbidden": "不允许未知字段",
            "missing": "缺少必填字段",
        }.get(item["type"], f"字段不满足约束（{item['type']}）")
        result.append({"path": ".".join(str(v) for v in item["loc"]), "message": message})
    return result
