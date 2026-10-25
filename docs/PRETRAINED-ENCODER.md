# P0.3.1 正式冻结视频编码器

选择 torchvision `R3D_18_Weights.KINETICS400_V1`，移除分类头后输出 512 维视频表征。所有骨干参数冻结，`eval()`，不执行训练。它是 Kinetics-400 预训练模型；匿名相似片段不等于服装动作类别，也不能据此声称动作准确率。

来源固定为 torchvision `v0.22.1`，官方权重地址：<https://download.pytorch.org/models/r3d_18-b3b3357e.pth>。原始文件 SHA256：`b3b3357ead25631ec9c57362ff2128a92d0427e01e2cd184951a44380c3f2e9d`。架构源码：<https://github.com/pytorch/vision/blob/v0.22.1/torchvision/models/video/resnet.py>。

实现代码为 BSD-3-Clause，原文保存在 [torchvision-LICENSE](delivery/p0.3.1/encoder/torchvision-LICENSE)。官方仓库同时说明预训练模型可能有源训练数据衍生的独立条款；实现许可不能代表全部 Kinetics 视频的权利许可。本阶段用于研发验证，未声称已完成商业发布的全部数据权利审查。保留[官方说明](https://github.com/pytorch/vision/tree/v0.22.1#pre-trained-models)供后续使用决策。

## 可复现导出

在仓库根目录、现有 Python 3.12 虚拟环境中执行：

```bash
.venv/bin/python -m pip install -r requirements-pretrained.txt --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python scripts/export_pretrained_encoder.py --download
```

导出工具固定 CPU PyTorch `2.7.1+cpu`、torchvision `0.22.1+cpu`、ONNX `1.17.0`、ONNX Runtime `1.20.1`。保留环境的代理与证书验证。只有显式 `--download` 会联网；运行 API 不会自动下载。也可通过 `--checkpoint` 指定同 SHA 的本地权重。缺少权重、SHA 不符或导出验证失败均报错，不使用随机骨干替代。

输出 `artifacts/models/r3d18-kinetics400-v1.onnx` 和 `docs/delivery/p0.3.1/encoder/export.json`。大型 checkpoint/ONNX 位于忽略目录，不提交 Git。导出器会严格加载官方完整 `state_dict`，移除分类头后保存单个自包含、CPU、opset 17 推理图，不含外部权重、Dropout 或随机算子。

## 输入与配置

输入 `float32[N,16,3,112,112]` RGB `[0,1]`，输出 `float32[N,512]`。在配置 ROI 内，先转浮点 RGB，再按 torchvision 官方张量规则以 bilinear、`align_corners=False`、`antialias=False` 缩放至 128×171，居中裁剪 112×112。归一化和 `N,T,C,H,W → N,C,T,H,W` 嵌入 ONNX 图；不能在调用端再做一次归一化。

归一化均值 `[0.43216,0.394666,0.37645]`，标准差 `[0.22803,0.22145,0.216989]`。NumPy 运行时预处理与官方 `weights.transforms()` 的浮点输入路径对照验证，无需安装 PyTorch 才能运行 API。解码前缩放、ROI、采样和该预处理版本仍共同进入特征空间哈希。

在隔离的 `system.yaml` 验收快照中配置：

```yaml
encoder:
  adapter: onnx
  weights_sha256: 36408854debf56e529e1fdafceb0d2dc27e03e230ef4fe5eb034828779f8f141
  feature_dim: 512
  preprocessing_version: torchvision-r3d18-kinetics400-v1
  pooling: global_avg_pool
  checkpoint_revision: torchvision-v0.22.1-R3D_18_Weights.KINETICS400_V1
profiles:
  demo:
    encoder_weights: ../models/r3d18-kinetics400-v1.onnx
```

`ingestion.encoder_input_size` 设置为 `112`，`video.frames_per_clip` 保持 `16`。`test.mp4` 验收快照将 `ingestion.decode_width` 显式设为 `640`，保留原图后再按官方规则缩放；这是模型契约变更，不是为了通过内存限额而静默减小原默认 224 输入。路径相对实际配置的项目根解析，以导入验证脚本保存的配置为准。主配置缺少模型时继续明确阻塞。

以上为覆盖字段，并非完整配置。用脚本从主配置创建完整隔离快照，再运行实际 API 导入：

```bash
.venv/bin/python -m scripts.prepare_pretrained_config --output-dir artifacts/p031-real
.venv/bin/python -m scripts.validate_pretrained_video --config artifacts/p031-real/config/system.yaml --output-dir artifacts/p031-video-evidence
.venv/bin/python -m scripts.preview_real_video --manifest artifacts/p031-video-evidence/manifest.json --vectors artifacts/p031-video-evidence/vectors.npy --video examples/test.mp4 --config artifacts/p031-real/config/system.yaml --encoder-report docs/delivery/p0.3.1/encoder/export.json --output-dir artifacts/p031-preview
```

打开生成的 `artifacts/p031-preview/index.html`，选择原始 `examples/test.mp4` 即可点击代表片段回查。脚本不修改主配置，拒绝覆盖不同的已有配置或知识文件。

## 已执行证据

[export.json](delivery/p0.3.1/encoder/export.json) 记录了官方来源、全部哈希、依赖、输入输出、冻结状态及实际验证值。使用 `test.mp4` 的 0–2 秒和 30–32 秒片段验证转换数值、不同片段向量非恒定与重载一致。该采样仅用于模型转换诊断，不构成两个独立来源；完整导入、原始 PTS 和正式片段时序以真实视频验收报告为准。

框架/ONNX 及官方预处理最大绝对误差均须小于 `1e-4`，重载差须为 `0`，不同片段 L2 距离须大于 `1e-3`。导出报告通过不代表真实学习效果通过；只有一个独立视频，真实 train/calibration/test 仍不得启动。
