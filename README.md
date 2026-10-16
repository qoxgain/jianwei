# 见微 · 多模态自学习感知平台

按 [V3 研发规格](docs/specification/01_PRODUCT_AND_SCOPE.md) 分步实现。目前交付 **P0.1 配置与知识工作台**：严格配置校验、动作与 SOP 编辑、差异/影响预览、不可变候选版本、SQLite 持久化及迁移。

- [分步研发计划](docs/ROADMAP.md)
- [本步交付与验收记录](docs/delivery/P0.1.md)
- [下载浏览器交互 Demo](demo/jianwei-p0.1-demo.zip)（点击 Download raw file，解压后打开 index.html）
- [Demo 验收步骤](demo/README.md)

本步不包含真实视频识别、模型训练、生产权限或在线检测。原始交接包的合成特征参考工具保留在 `reference/`，其结果独立于产品效果验收。

## 运行完整本步应用

已验证 Linux、Python 3.12、Node.js 24。Python 依赖锁定在 `requirements.lock`，前端锁定在 `apps/web/package-lock.json`。从仓库根目录运行：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

配置中的 demo API 默认绑定本机 `127.0.0.1:8000`，浏览器打开同一地址即可。此地址仅用于自己的运行机器，不是公网分享地址。构建后的前端由 FastAPI 同源提供，API 文档位于 `/docs`。

服务启动执行 Alembic 升级；重启不会删除数据。候选版本存储于 `data_demo/jianwei.db`，原始 `knowledge/labels.yaml` 保持知识基准。候选不会自动激活。

配置统一来自 `config/system.yaml`；业务知识来自 `knowledge/labels.yaml`。路径以配置文件所在目录的上一级为项目根，不随 shell 工作目录改变。敏感值只用 `env://VARIABLE_NAME` 引用，不能写进配置或 Git。

P0.1 只开放单用户、回环地址上的 demo 服务。lab/site 可做静态检查和前置条件诊断；真实视频、模型服务与鉴权将在后续步骤实现。

## 检查与测试

```bash
.venv/bin/python -m jianwei doctor --profile demo
.venv/bin/python -m jianwei doctor --profile lab
.venv/bin/python -m jianwei doctor --profile site
.venv/bin/python -m jianwei doctor --profile site --preflight
.venv/bin/python -m pytest tests -q
.venv/bin/python -m unittest discover -s reference/tests -v
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run build:demo
.venv/bin/python -m scripts.package_demo
```

示例 site 缺少真实数据、模型和服务条件时，`--preflight` 返回退出码 2，这是明确诊断。静态校验通过并不代表现场可运行。

前端开发可运行 `npm --prefix apps/web run dev`，与已启动 API 联调。离线单文件 Demo 产物位于 `apps/web/dist-demo/index.html`，可复制到任何静态服务器；独立演示只保存浏览器本地候选，不连接服务器。

## 目录

| 路径 | 用途 |
|---|---|
| `apps/api` / `apps/web` | FastAPI / React 工作台 |
| `packages/config` / `packages/contracts` | 唯一配置加载器与严格数据契约 |
| `packages/knowledge` | 知识校验、语义差异与影响分析 |
| `infra` | SQLAlchemy 数据模型、Alembic 迁移 |
| `jianwei` / `scripts` | CLI、安装与启动入口 |
| `tests` | 本步功能与错误路径测试 |
| `config` / `knowledge` / `schemas` | 实际应用配置、知识与生成的 Schema |
| `docs/specification` | 交接规格，描述目标能力，不能视为已实现清单 |
| `reference` | 原始 CPU 机制验证快照，明确使用合成数据 |

下一步为 **P0.2 视频/特征接入**，输入格式和数据需求见分步计划。每步在查看和确认后继续，不把页面演示计为工业效果验收。

浏览器验收可另行安装 `requirements-browser.txt`，使用已安装的 Chromium。启动 API 和独立静态 Demo 服务后执行 `python tests/browser_smoke.py`；可通过 `--chromium`、`--url`、`--demo` 指定自己的路径/地址。独立静态服务命令：`python -m http.server 8001 --bind 127.0.0.1 --directory apps/web/dist-demo`。

模型契约修改后运行 `.venv/bin/python -m scripts.export_schemas` 同步前端 Schema，测试会检查生成结果一致。仓库根目录的 `MANIFEST.sha256` 是原始交接包清单，不是本产品仓库的完整性清单。
