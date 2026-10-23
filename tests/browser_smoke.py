"""Run against a live demo API and its self-contained offline build.

Optional dev dependencies: Playwright 1.62.0 and Chromium. Defaults to the local
configured development service; override arguments for an isolated test server.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def exercise(page, address, mode, screenshots):
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append((request.url, request.resource_type)))
    page.goto(address)
    expect(page.get_by_role("heading", name="从一个场景，开始看见。")).to_be_visible()
    expect(page.locator(".connection")).to_contain_text("浏览器演示" if mode == "offline" else "API 已连接")
    page.screenshot(path=str(screenshots / f"{mode}-overview.png"), full_page=True, animations="disabled")
    page.get_by_role("button", name="编辑动作与 SOP").click()
    page.get_by_role("button", name="检查口袋", exact=True).click()
    name = page.get_by_label("动作名称", exact=True)
    # A temporary invalid field must remain editable, and must not be saved.
    name.fill("")
    expect(name).to_be_visible()
    name.fill("检查口袋 · 浏览器验收")
    page.get_by_role("button", name="校验并预览", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("严格校验通过")
    expect(dialog).to_contain_text("check_pocket")
    dialog.get_by_role("button", name="保存候选版本", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_text("未激活候选", exact=True).first).to_be_visible()
    page.reload()
    page.get_by_role("button", name="编辑动作与 SOP").click()
    page.get_by_role("button", name="检查口袋", exact=True).click()
    expect(page.get_by_label("动作名称", exact=True)).to_have_value("检查口袋")
    page.get_by_role("tab", name=re.compile("版本记录")).click()
    expect(page.get_by_text("未激活候选", exact=True).first).to_be_visible()
    page.get_by_role("tab", name=re.compile("动作定义")).click()
    editor = page.get_by_label("知识 YAML 编辑器")
    original = editor.input_value()
    editor.fill(original + "\nknowledge_version: duplicate\n")
    page.get_by_role("button", name="校验并预览", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("校验未通过")
    expect(page.get_by_role("button", name="保存候选版本", exact=True)).to_have_count(0)
    page.get_by_role("button", name="返回编辑", exact=True).click()
    editor.fill(original)
    page.screenshot(path=str(screenshots / f"{mode}-editor.png"), full_page=True, animations="disabled")
    page.get_by_role("button", name="环境检查", exact=True).click()
    page.get_by_role("button", name=re.compile("现场环境")).click()
    page.get_by_role("button", name="运行检查", exact=True).click()
    expect(page.locator(".checks-panel")).to_contain_text("未就绪")
    if mode == "api":
        expect(page.locator(".checks-panel")).to_contain_text("AUTH_NOT_IMPLEMENTED")
    # Mobile navigation and document width, rather than screenshots alone.
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="打开导航", exact=True).click()
    page.get_by_role("button", name="场景概览", exact=True).click()
    expect(page.locator(".sidebar")).not_to_have_class(re.compile(r"\bopen\b"))
    expect(page.get_by_role("button", name="场景概览", exact=True)).to_have_attribute("aria-current", "page")
    if page.get_by_role("button", name="关闭提示", exact=True).is_visible():
        page.get_by_role("button", name="关闭提示", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "mobile horizontal overflow"
    page.screenshot(path=str(screenshots / f"{mode}-mobile.png"), full_page=False, animations="disabled")
    assert not errors, errors
    if mode == "offline":
        # Scene selection updates the query string before reload. Only the same
        # self-contained document may load; assets/API/external requests may not.
        document_url = urlsplit(address)
        assert not [(url, kind) for url, kind in requests
                    if url.startswith(("http:", "https:")) and
                    (kind != "document" or urlsplit(url)[:3] != document_url[:3])], requests
    return {"mode": mode, "passed": True, "console_errors": errors,
            "checks": ["boot", "edit-invalid-recover", "preview", "candidate-save", "reload-persistence", "baseline-unchanged", "duplicate-yaml-rejected", "environment-blockers", "mobile-width"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--demo", default="http://127.0.0.1:8001/", help="Static offline-demo server, or a file path on browsers permitting file URLs")
    args = parser.parse_args()
    screenshots = ROOT / "artifacts/browser"
    screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, args=["--no-sandbox"])
        results = []
        for mode, address in [("api", args.url), ("offline", (args.demo if args.demo.startswith(("http://", "https://")) else Path(args.demo).resolve().as_uri()))]:
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            try:
                results.append(exercise(context.new_page(), address, mode, screenshots))
            finally:
                context.close()
        browser.close()
    (screenshots / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
