# 见微 · 多模态自学习感知平台

当前实现 **P0.3 无标注时序研究工作台**。服装SOP、电子装配、无SOP安全事件、无标签观察使用同一套场景API和页面；动作、状态、事件、流程节点分别定义。产品版本为V3.0，配置与知识契约为3.1。

- [本步任务](docs/plans/P0.3.md) · [P0.1.1最终任务书](docs/plans/P0.1.1-final.md)
- [本次交付、测试与边界](docs/delivery/P0.3.md)
- [研究路线与阶段依赖](docs/ROADMAP.md) · [P0.2衔接契约](docs/P0.2-CONTRACTS.md)
- [四场景离线演示使用说明](demo/README.md)

已支持有界 MP4/NPZ 上传、持久异步任务、实际视频解码、周期/时间域验证、特征缓存与可选冻结 ONNX CPU 编码器。四场景均可运行随附合成示例。真实编码器未配置时，视频保留解码清单并明确 blocked。新增匿名聚类、变化点、未来特征预测、真实可训练小型 TCN、独立封存评估及可重载权重。动作语义识别、在线 SOP 和生产鉴权尚未实现，现场学习效果未验证。

## 运行

Python 3.12、Node 24、FFmpeg/FFprobe；依赖分别锁在 `requirements.lock` 和 `apps/web/package-lock.json`。

```bash
bash scripts/install.sh
# 可选：启用受信任服务器 ONNX 编码器时使用 bash scripts/install.sh --with-encoder
bash scripts/start.sh
```

默认浏览器访问本机 `http://127.0.0.1:8000`。当前仅开放单用户、回环地址的demo服务。候选存入SQLite，候选不自动激活。lab/site只做静态检查和前置条件诊断。

## 数据接入

启动后进入“数据接入”，选择场景并点击“运行示例导入”。这是服务端真实执行的确定性合成 NPZ，不代表现场模型效果。上传自己的 MP4 使用 `file_01`，按场景填写上下文与完整周期。预计算特征协议、时钟锚点和来源核验见 [导入说明](docs/INGESTION.md)；冻结模型配置见 [编码器说明](docs/ENCODER.md)。

独立审阅包：[P0.3 Demo](demo/jianwei-p0.3-demo.zip)。HTML 回放实际服务端生成的合成导入、训练和独立评估记录；实际任务需要本机 API。研究使用步骤见 [时序研究说明](docs/TEMPORAL.md)。

用户上传的 `examples/test.mp4` 已完成真实解码与诊断编码器接入核查。单视频不能证明泛化，正式视觉编码器尚待接入。下一步按 [P0.3.1真实视频任务书](docs/plans/P0.3.1-real-video.md) 开发。

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

site预检缺少现场资源时返回2是预期阻塞，不是假就绪。浏览器验收安装 `requirements-browser.txt`，启动API和静态服务后运行 `tests/browser_smoke.py` 与 `tests/browser_scenes.py`；P0.1.1 浏览器门槛已补齐；P0.2 的实际浏览器结果见历史交付记录；P0.3 新页面浏览器验收和截图仍待补齐，构建不能替代浏览器验收。

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

主线A的顺序为：公共底座 → 视频/特征 → 自监督时序 → 语义与状态记忆 → 少样本抗遗忘 → 主动局部感知。当前完成 P0.3 工程与合成机制验证，接下来按 P0.3.1 补真实编码器、独立录像和浏览器验收，再推进 P0.4。
