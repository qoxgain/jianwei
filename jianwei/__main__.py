"""Commands share the same configuration loader as the application."""
from __future__ import annotations

import argparse
import json

from packages.config.errors import ContractError
from packages.config.loader import PROJECT_ROOT, assert_demo_service, load_bundle, profile_status
from packages.knowledge.service import content_hash


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m jianwei", description="见微 P0.2 数据接入与知识工作台")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "doctor"):
        child = commands.add_parser(command)
        child.add_argument("--config", default=str(PROJECT_ROOT / "config/system.yaml"))
        child.add_argument("--profile", default=None)
        if command == "doctor":
            child.add_argument("--preflight", action="store_true", help="检查本地前置条件；不代表远端服务可用")
    args = parser.parse_args()
    try:
        bundle = load_bundle(args.config, args.profile)
        if args.command == "doctor":
            result = {"check": "preflight" if args.preflight else "static", "stage": "P0.2",
                      "profile": bundle.config.active_profile, "config_hash": bundle.config_hash,
                      "knowledge_hash": content_hash(bundle.knowledge.model_dump()), "status": "PASS"}
            if args.preflight:
                result["profile_status"] = profile_status(bundle, bundle.config.active_profile)
                result["status"] = "PASS" if result["profile_status"]["runtime_ready"] else "BLOCKED"
                result["note"] = "仅检查本地配置和前置条件；未验证相机、模型、外部服务或生产能力。"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "PASS" else 2
        assert_demo_service(bundle)
        import uvicorn
        from apps.api.main import create_app
        uvicorn.run(create_app(bundle.path, args.profile), host=bundle.profile.api_host, port=bundle.profile.api_port)
        return 0
    except ContractError as error:
        print(json.dumps({"status": "FAIL", "detail": error.detail()}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
