# 见微 V3.0 自学习优先研发交接包

版本日期：2026-10-15。目标读者：算法研发、后端研发、前端研发及交付人员。

本版把自学习从 V2 的后续增强改为第一阶段必须完成的研发主线。先验证视频时序学习、动作记忆和预测校正能否降低标注成本，再完成完整产品页面与多场景部署。大模型权重保持冻结；允许训练独立的轻量时序模型、动作学生模型和具备合格检测标签的目标检测器。

## 交付边界

这是可以交给 Codex 实施的规格、配置、数据契约和可运行参考工具。包内 CPU 示例演示冻结特征上的预测学习、独立未来观测校正、记忆入库及效果比较。示例输入是合成特征，不是工业视频；不代表真实视觉识别能力或生产性能。真实解码、编码器、自动标注、YOLO/TSM训练、后端和前端在任务书中明确列为待实现。

## 建议阅读顺序

1. `见微_V3.0_完整研发手册.md`：完整合订版。
2. `docs/01_PRODUCT_AND_SCOPE.md`：产品与实施范围。
3. `docs/02_SELF_LEARNING_ENGINE.md`：第一阶段学习机制、算法及错误隔离。
4. `docs/03_KNOWLEDGE_AND_MEMORY.md`：人工标签文件和自动记忆的配合。
5. `docs/07_CODEX_TASKBOOK.md`、`CODEX_FIRST_PROMPT.txt`：直接开发任务。

## 两个主要编辑入口

| 文件 | 内容 | 修改后的处理 |
|---|---|---|
| `config/system.yaml` | 所有运行、部署、采样、学习、评估、训练和发布参数；demo/lab/site环境配置 | 静态校验、运行预检、生成配置快照；环境类修改重启 |
| `knowledge/labels.yaml` | 动作ID、语义、正负证据、SOP约束、混淆关系、人工记忆修正 | 校验、差异预览、影响分析、验证后发布新知识版本 |

密钥通过 YAML 中 `env://变量名` 引用，只把密码留在部署环境。系统生成的记忆落数据库，支持导出；不把海量向量、观测流水和自动标签直接写回人工标签文件。`knowledge/memory_patch.example.yaml` 是人工修改机器记忆的可选补丁格式，不是第三份运行参数配置。

## 当前可运行

Python 3.11及以上，依赖见 `requirements.txt`。命令从交接包根目录执行。

```bash
python -m pip install -r requirements.txt
python tools/config_doctor.py --config config/system.yaml
python tools/self_learning_demo.py --config config/system.yaml
python -m unittest discover -s tests -v
```

示例输出目录由 `config/system.yaml` 中选中环境的 `data_root` 与 `paths.demo_output` 决定。重复运行使用独立运行目录，不覆盖旧实验。请查看生成的 `report.json`、`memory.sqlite` 和 `prediction_log.jsonl`。

## Codex实施入口

在新代码仓库根目录放入本包内容，将 `CODEX_FIRST_PROMPT.txt` 发送给 Codex。首轮完成 **P0 自学习技术闭环**，包括真实视频的接入接口和独立评测；无实际视频时提供清晰的数据接入方式，不能用合成数据替代真实验收。

不再沿用 V2 的“P0先做完整大屏、P7才开始学习”顺序。本包是自包含 V3 规格，不需要混用 V2 的旧配置或任务编号。
