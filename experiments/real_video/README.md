# P0.3.6 回顾性真实视频稳健性研究

历史基线/审查：a194302（独立探索分支）；正式基线0bf8fba。先修AUD-01/AUD-02及数据库可靠性，再选择性迁入 experiment.py 中的分区、张量、指标和旧基线工具，没有合并整个分支。

原私有数值包在当前环境不可用，用户要求从原片重新提取。新目录使用正式冻结R3D-18，已复现历史ONNX SHA 2979019509005056778db9f462e9eed212c40f02d5e5bf1d6fc9e06caf2ebab8。新的生产依赖版本仍产生新FeatureSpace；不改写旧缓存，须核对各来源实际缓存SHA，不能仅凭模型相同假定缓存一致。

全部运行参数来自隔离 system.yaml：exploratory 配置显式启用，seeds固定11/23/47/71/101，原history8主比较及history16/32共同目标队列，guard2000ms，最少8窗口，跨度0.5/2/5秒和容差1000ms不变；Adam/epochs/hidden/L2取learning。根配置默认 disabled，不自动训练或访问正式数据。

固定50%/75%时间划分，先按原PTS排除跨界片段，再构建窗口。各视频单独拟合train归一化/PCA；validation只用于候选/epoch选择。选择文件和实际权重先保存、核对逐数组重载，再首次读取新运行的holdout特征值。过去的holdout已被观察，这里仍然只是回顾性开发，不能成为独立泛化证据。

保留Persistence、TrainMean、Ridge、InitialTCN、ShuffledRidge、ShuffledTCN、当前CausalTCN20epoch；新增直接预测的validation checkpoint控制、残差/变化量Ridge与TCN、特征一阶差分、按历史长度拓展的多尺度因果头。保留无历史、历史顺序打乱、目标打乱和常量变化量对照。多尺度初始dilation取配置，感受野不足时按固定翻倍规则拓展至覆盖所配置历史（最多32）；报告实际RF和有效历史，原两层对照仍仅7。打乱历史仅作用于有效旧历史，保留当前锚点，避免混入“删掉当前信息”的干扰。

所有模型在同一history队列的相同目标上比较；长历史比较内旧模型仍只看最后8段，新长历史头看全部。不同history队列的窗口数量可能变化，不能直接拿全8窗口队列的误差与16窗口子集误差宣称收益。不足保持blocked。短片B仍不放宽历史、保护间隔或最少窗口。

train/validation曲线均提供原512维MSE，另保留潜空间训练目标；运动分组阈值只从训练历史差分确定，不是动作真值。多种子是优化敏感性，不能当独立录像或构造总体置信区间。不引入未经验证的光流/ROI新编码器，避免把特征变化与小模型收益混淆。

```bash
# 先在新目录按正式导出报告准备隔离配置；修改该文件 exploratory.enabled=true。
.venv/bin/python -m scripts.prepare_video_dataset --research --output-dir artifacts/p036/runtime --encoder-report artifacts/p036/encoder-export.json --encoder-model artifacts/p036/models/r3d18-kinetics400-v1.onnx
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m scripts.extract_p036_features --config artifacts/p036/runtime/config/system.yaml --output artifacts/p036/features
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m scripts.run_p036_robustness --config artifacts/p036/runtime/config/system.yaml --features artifacts/p036/features --output artifacts/p036/learning
```

输出目录必须新建；每源报告、每种子实际权重、配置/代码/来源/队列SHA和选择记录均存本地artifacts。脚本只接受catalog九段unassigned真实来源，不导入或打开正式数据库。仅经过审查的标量统计可以另行生成公开Demo；特征、权重、预测向量和数据库均禁止公开提交。本轮还在执行阶段，最终结果见后续P0.3.6交付，不能把算法实现测试写成真实收益验收。
