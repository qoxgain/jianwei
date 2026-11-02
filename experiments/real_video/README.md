# Retrospective Real-Video Learning

本流程与正式研究任务、数据库和封存消费记录分开；只接受catalog里原有的九段开发资料以及`unassigned`真实冻结特征，不修改原清单，不承认独立采集会话。参数固定在隔离system.yaml，experiment.yaml只指定目录、保护间隔和实验范围。

固定采样：clip2s、stride0.5s、16帧，history8，预测0.5/2/5s，目标容差仍1000ms。每源单独拟合归一化和PCA16，不拼接不同视角/特征空间。模型使用主配置参数：hidden32、Adam20epochs、batch64、lr0.001、ridge_l2=1、seed23，不根据留出表现调整。

- A：整个视频同一批窗口训练/评价，只有拟合证据。
- B：以原始时长50%/75%为边界，train=[0,50%)、validation=[50%+2s,75%)、holdout=[75%+2s,end)。先排除跨区间片段，再按episode/时间域构建窗口，检查无共享片段及输入/目标原始时间覆盖。每区间至少8窗口，不足则blocked，不缩短历史、不修改跨度。
- C：需要实际证据核实独立采集、同视角可比性。目前不执行；七工位不同，服装两片关系未核实。全部资料已用于历史开发，不作正式封存测试。

复用`packages.temporal.models`的真实PCA、Ridge、TCN反向和优化器，另设打乱目标Ridge/TCN、未训练TCN、训练均值与Persistence。选模只读validation，选择记录先保存再计算holdout指标。只报告特征MSE、归一化MSE、余弦、每跨度/窗口的误差与权重实际变化。匿名簇只是train-only k-means回放；不计算语义状态NLL、动作准确率、统计显著性或窗口bootstrap。

```bash
# 安装依赖、下载/验证正式冻结模型及LFS素材，见原README；不修改根配置。
.venv/bin/python -m scripts.prepare_video_dataset --research --output-dir artifacts/exploratory/runtime --encoder-report artifacts/encoder-export.json
.venv/bin/python -m scripts.validate_video_dataset --config artifacts/exploratory/runtime/config/system.yaml --output artifacts/exploratory/extraction --skip-default-limit
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m scripts.run_exploratory_real_video --config experiments/real_video/experiment.yaml
```

提取缺失返回2，可加`--wait-for-extraction`有界等待；数值异常保留failure.json，退出1，不调整条件消除失败。输出包括每源A/B报告、实际权重NPZ、完整512维预测数组、源与配置/代码依赖哈希、划分与随机种子。仅数值制品可交付；工业原片或派生画面不随Demo上传。现有前端新增只读“真实视频学习实验”，仅展示实际记录，本机原片须SHA匹配后回查，不上传。

解释时必须同时看Persistence、Ridge、初始TCN、训练均值与打乱目标对照。训练损失下降或胜过Persistence本身不能证明利用了时序关系。正式blocked保持不变；探索性结果即使正向也不是独立科研验收。
