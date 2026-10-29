# 见微 · 多模态自学习感知平台

当前实现 **P0.3.3 真实素材接入与 SOP 人工复核**，九段真实录像已分别完成正式冻结编码器提取与匿名探索；独立学习验收仍 blocked。服装SOP、电子装配、无SOP安全事件、无标签观察使用同一套场景API和页面；动作、状态、事件、流程节点分别定义。产品版本为V3.0，配置与知识契约为3.1。

- [本步任务](docs/plans/P0.3.3-real-data.md) · [下一步独立研究门槛](docs/plans/P0.3.3-next.md) · [P0.1.1最终任务书](docs/plans/P0.1.1-final.md)
- [本次交付、测试与边界](docs/delivery/P0.3.3.md) · [九视频验收 Demo 下载](docs/demo/p0.3.3/jianwei-p0.3.3-demo.zip) · [P0.3.2 历史交付](docs/delivery/P0.3.2.md) · [P0.3.1 历史交付](docs/delivery/P0.3.1.md)
- [研究路线与阶段依赖](docs/ROADMAP.md) · [P0.2衔接契约](docs/P0.2-CONTRACTS.md)
- [四场景离线演示使用说明](demo/README.md)

已支持有界 MP4/NPZ 上传、持久异步任务、实际视频解码、周期/时间域验证、特征缓存与可选冻结 ONNX CPU 编码器。四场景均可运行随附合成示例。真实编码器未配置时，视频保留解码清单并明确 blocked。新增匿名聚类、变化点、未来特征预测、真实可训练小型 TCN、独立封存评估及可重载权重。动作语义识别、在线 SOP 和生产鉴权尚未实现，现场学习效果未验证。

## 运行

Python 3.12、Node 24、FFmpeg/FFprobe、Poppler 的 pdftotext（SOP原文提取）；依赖分别锁在 `requirements.lock` 和 `apps/web/package-lock.json`。

```bash
bash scripts/install.sh
# 可选：启用受信任服务器 ONNX 编码器时使用 bash scripts/install.sh --with-encoder
bash scripts/start.sh
```

默认浏览器访问本机 `http://127.0.0.1:8000`。当前仅开放单用户、回环地址的demo服务。候选存入SQLite，候选不自动激活。lab/site只做静态检查和前置条件诊断。

## 本批真实素材与 SOP

默认配置继续保留 64 MiB 上传上限。电子装配场景的“SOP 人工复核”提供注册原片/PDF回查、11章与7工位的多对多草稿、争议、批准/撤销和追加审计。HEVC使用有界本地H.264兼容预览，原片、原SHA和特征输入不变；派生画面不自动发布。

对九视频使用显式512 MiB隔离配置：

```bash
git lfs install --local
git lfs pull --include='datasets/video_samples/**/*.mp4' --exclude=''
.venv/bin/python -m pip install -r requirements-pretrained.txt --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python scripts/export_pretrained_encoder.py --download --report artifacts/encoder-export.json
.venv/bin/python -m scripts.prepare_video_dataset
.venv/bin/python -m scripts.validate_video_dataset
# 完成批量实测后，打开本机 http://127.0.0.1:18053
.venv/bin/python -m jianwei serve --config artifacts/p033-runtime/config/system.yaml --profile demo
```

选择“无标签观察 → 数据接入”查看9个实际导入、逐源匿名探索与代表片段；选择“电子装配 → SOP 人工复核”核对规范。配置工厂验证模型与已核验官方导出SHA一致，拒绝随机替代。报告在 `artifacts/p033`，正式交付的数字证据在 `docs/delivery/p0.3.3`。本轮2秒采样是CPU工程验收设置，不能支持现有0.5秒预测目标；可用因果训练对为0，详见后续研究门槛。

## 数据接入

新增现场素材统一放在 [datasets/video_samples](datasets/video_samples/README.md)。本轮完成素材整理与评估；工业接入、旋转与工位/SOP适配按 [P0.3.3任务书](docs/plans/P0.3.3-real-data.md) 继续开发。查看 [实际评估](docs/delivery/P0.3.3-data-review.md) 和 [Codex指令](docs/plans/P0.3.3-Codex.txt)。尚未宣称真实学习效果通过。

启动后进入“数据接入”，选择场景并点击“运行示例导入”。这是服务端真实执行的确定性合成 NPZ，不代表现场模型效果。上传自己的 MP4 使用 `file_01`，按场景填写上下文与完整周期。预计算特征协议、时钟锚点和来源核验见 [导入说明](docs/INGESTION.md)；冻结模型配置见 [编码器说明](docs/ENCODER.md)。

独立审阅包：[P0.3.2 Demo](demo/jianwei-p0.3.2-demo.zip)。HTML 展示已执行的四场景预检与历史合成研究记录；浏览器不训练或读取封存特征。真实来源预检、草稿导出和研究任务需要本机 API。研究使用步骤见 [时序研究说明](docs/TEMPORAL.md)。

P0.3.2 的四场景 API/离线浏览器验收、展开预检的手机布局和原视频回查已在 [GitHub CI](https://github.com/qoxgain/jianwei/actions/runs/40255750288) 通过；[实际截图与结果](docs/delivery/p0.3.2/browser/ci.json)保留被测代码 SHA，交付 Demo 与 CI 页面构建字节一致。

用户上传的 `datasets/video_samples/garment_quality/videos/test.mp4` 已用官方 Kinetics-400 预训练 R3D-18 冻结模型处理：完整核验 2,844 帧，获得 117 个 512 维有效片段。查看 [单视频 Demo 下载](demo/jianwei-p0.3.1-video-demo.zip)、[实际截图](docs/delivery/p0.3.1/preview/desktop.png)和[完整报告](docs/delivery/p0.3.1/preview/report.json)。解压后打开 `index.html`，点击代表片段回查原视频。单源回顾性匿名探索不代表动作识别或独立学习效果。安装与复现见 [冻结编码器说明](docs/PRETRAINED-ENCODER.md)；真实研究须先完成 [采集来源审查](docs/DATASET-PROVENANCE.md)。

## 配置与知识

`config/system.yaml` 是唯一运行参数入口：环境、数据源、场景注册、学习参数与会话策略集中配置。`active_scene`只是默认展示；一个注册来源可以绑定多个场景，不复制原始来源身份。

| 人工知识文件 | 示例 |
|---|---|
| `knowledge/scenes/garment_sop.yaml` | 服装5个已定义动作和1个草案；款式、评分是场景扩展 |
| `knowledge/scenes/electronics_assembly.yaml` | 同一inspect动作对应两个不同流程节点 |
| `knowledge/scenes/workshop_safety.yaml` | 状态和事件规则，无SOP、无款号 |
| `knowledge/scenes/anonymous_observation.yaml` | actions/events为空、workflow=null |

原 `knowledge/labels.yaml` 保留为3.0兼容资料，不被自动改写。时序模型保持候选；机器语义记忆为后续能力，不自动覆盖人工知识。敏感值仍只使用 `env://NAME`，来源URI不发往浏览器。

## 兼容与迁移

旧3.0配置可只读加载；新场景接口提供正规化知识，原API保持默认场景兼容并标记deprecated。显式导出新文件：

```bash
python -m scripts.migrate_v30 --config tests/fixtures/v30/config/system.yaml --output-dir /tmp/jianwei-v31-export
```

实际使用时把 `--config` 替换为自己的旧配置。命令不覆盖原文件，保留旧场景/动作/来源ID，输出节点映射与新旧hash，并保持原有资源路径的物理指向。已有输出不同则拒绝覆盖。

启动自动执行 Alembic `0004_temporal`，在 P0.2 导入表基础上新增研究任务表，不改写知识与导入历史。旧SQLite迁移前生成 `.pre-p011.sqlite` 备份；旧版本ID、hash、YAML和JSON不变，新表示写入 `legacy_knowledge_mappings`。不能确定旧租户归属时停止迁移。详情见交付记录。

## 验证

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m unittest discover -s reference/tests -v
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run build:demo
.venv/bin/python -m scripts.package_demo
.venv/bin/python -m jianwei doctor --profile demo
.venv/bin/python -m jianwei doctor --profile lab
.venv/bin/python -m jianwei doctor --profile site
.venv/bin/python -m jianwei doctor --profile site --preflight
```

site预检缺少现场资源时返回2是预期阻塞，不是假就绪。浏览器验收安装 `requirements-browser.txt`，启动API和静态服务后运行 `tests/browser_smoke.py` 与 `tests/browser_scenes.py`；P0.1.1 浏览器门槛已补齐；P0.2 的实际浏览器结果见历史交付记录；P0.3 四场景研究页面的 API/离线、桌面/手机、重试及下载已实际通过，[截图与结果](docs/delivery/p0.3.1-browser/results.json)已补齐。可运行 `python tests/browser_temporal.py --serve-isolated` 复现；单视频播放器用 `python tests/browser_video_exploration.py` 验收。

## 开发目录

| 路径 | 职责 |
|---|---|
| `packages/contracts/models.py` | 严格3.0兼容契约 |
| `packages/contracts/scenes.py` / `configuration.py` | 3.1通用知识与场景配置 |
| `packages/temporal` / `packages/temporal_jobs` | 有界训练、独立评估、数值权重和持久研究任务 |
| `packages/contracts/learning.py` | Episode、FeatureSpace、观测/预测/规范与记忆契约，无运行引擎 |
| `packages/migrations/legacy_v3.py` | 显式只读适配 |
| `packages/config` / `packages/knowledge` | 配置加载、知识校验与语义差异 |
| `apps/api` / `apps/web` | 场景API与通用工作台 |
| `infra` | 作用域数据库、迁移、旧新映射 |
| `contracts/openapi.yaml` | 当前已实现API的生成快照 |
| `contracts/future-openapi.yaml` | 尚未实现的后续API草案 |
| `reference` | 原始合成机制参考，不代表真实模型效果 |

主线A的顺序为：公共底座 → 视频/特征 → 自监督时序 → 语义与状态记忆 → 少样本抗遗忘 → 主动局部感知。P0.3.2 将独立来源、采集审查和有效时间窗口的缺口显示在研究页面；独立录像与真实训练/校准/测试仍待验收，尚不进入 P0.4。
