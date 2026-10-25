# P0.3 无标注时序研究

这是冻结特征上的 CPU 研究工作台。无需注册动作或 SOP；输出匿名状态、候选变化点和未来特征预测。视觉编码器不训练，不生成业务动作真值，不激活模型。

## 使用流程

1. 按 README 启动本地 API，切到“时序研究”。点击“准备合成研究示例”，等待八个导入完成。
2. 点击“用训练 / 校准分区建立候选”。四个 train 来源拟合标准化、PCA、聚类、变化阈值、转移矩阵、ridge 和两层因果 TCN；两个 calibration 来源仅比较候选。
3. 查看实际参数变化、校准误差与候选模型，再显式点击“读取封存 test，运行独立评估”。两个 test 来源不重新拟合任何参数，也不改变选模。
4. 下载完整报告、数值权重和预测/观测日志。所有结果仍为候选，人工知识和 SOP 不变化。

真实数据先在“数据接入”选择**整个原始视频**的分区。一个来源不得在 train/calibration/test 间拆分；仅改文件名、截片或切换场景不能形成独立证据。至少 2 个 train、1 个 calibration、1 个 test 是软件运行下限，不是统计充分性保证。独立评估还应按采集会话、人员和日期核查相关性。

所有来源必须在同一租户、场景、视角、特征空间和数据类型下。未分区、仅声明未核验的来源、质量缺口、不同时间域/周期不能参与有效配对。外部 NPZ 的指纹核验不能证明该编码器实际产生了向量；报告保留这个限制。

## 算法与指标

- train 内标准化和 PCA；原空间预测误差用 train 标准差归一化。记录保留方差与 train 重建误差。
- 固定对照：最后特征保持、匿名簇经验转移、ridge、因果 TCN，以及仅在 train 中打乱未来目标的 ridge 负对照。
- TCN 是 NumPy 实现的独立小模型：两层因果卷积、膨胀率 1/2、7 个窗口感受野，真实反向传播和 Adam。`learning` 配置控制训练；不宣称复现完整论文。
- 以 calibration **按来源等权**的归一化特征 MSE 选模。保留全部 test 基线，TCN 不保证胜出；不为改善 test 改种子。
- 匿名状态 NLL/Brier 使用 train 聚类，距离 softmax 未经概率校准。下一不同簇与固定 horizon 预测分开统计；它们均不是语义动作/事件准确率。
- 变化点是回顾性的候选分段，同时保留均匀分段对照。没有人工边界就不计算分段 F1。

P0.3 的协议是因果**离线回放**。每条预测先 flush/fsync 写入日志，然后读取相应未来目标，观测记录引用预测 hash。目标区间在历史最大结束 PTS 之后；不称为在线系统。

## API、CLI 与持久化

`/api/scenes/{scene_id}/temporal/` 下提供 `inputs`、`example`、`train`、`evaluate`、`runs`、`runs/{id}`、`runs/{id}/retry` 和产物下载。请求只携带已登记 ID，不能指定任意文件路径或租户。训练请求：

```json
{"import_ids":["train-import-1","train-import-2","calibration-import-1"]}
```

评估请求：

```json
{"model_run_id":"completed-train-run","import_ids":["test-import-1"],"sealed_test":true}
```

保存 JSON 后，可停止占用同一目录的 API 工作进程，再运行：

```bash
.venv/bin/python -m jianwei research --kind train --scene anonymous_observation --request train.json
.venv/bin/python -m jianwei research --kind evaluate --scene anonymous_observation --request evaluate.json
```

具体 CLI 参数顺序见 `python -m jianwei research --help`。任务采用 SQLite + 单进程文件锁；重启恢复未完成任务。配置、知识或算法变化会阻塞旧任务，要求创建新快照。失败重试创建新产物目录。它不是多节点调度或生产权限平台。

Alembic `0004_temporal` 只新增研究任务表。迁移核查脚本验证旧 ID、hash、YAML、JSON、源分区与重复执行不变。生产使用前仍需由运维备份自己的数据库。

## 可复现实验

```bash
.venv/bin/python -m scripts.capture_temporal_evidence --output-dir artifacts/p03-reproduction
.venv/bin/python -m scripts.verify_temporal_migration --output artifacts/migration.json
.venv/bin/python -m scripts.validate_real_video --output-dir artifacts/video-review
```

四场景复用同样八个独立合成来源，不是 32 个视频。按内容 hash 固定数值拟合顺序，避免随机导入 ID 改变训练结果；新数据库的任务 ID 和记录时间仍会不同。锁定的依赖、参数及相同硬件环境下检查权重重现，不承诺跨 BLAS/硬件逐字节相同。

`validate_real_video` 默认先记录原配置的失败，再在临时配置中允许 512 MiB 解码，最后用解析式 RGB 均值 ONNX 完成管线诊断。它不修改正式配置，且**不是预训练视觉感知模型**。全部原视频保持 `unassigned`。可通过 `--config /path/to/config/system.yaml` 使用已经配置好的真实冻结 ONNX（本地 demo profile、file_01 来源）；该路径不应用诊断覆盖。
