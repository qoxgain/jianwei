# 冻结 ONNX CPU 编码器

P0.3.1 已集成官方 R3D-18，来源、下载导出和实际数值验收见 [正式冻结编码器](PRETRAINED-ENCODER.md)。下文是通用 ONNX 接口。

`packages.encoders.load_encoder` 执行服务器配置指定的自包含 ONNX 推理图。NPZ 导入不需要 ONNX 或 GPU；为 NPZ 上传的编码器指纹文件仅用于计算实际 SHA256，不会执行，也不能证明该权重确实生成了外部向量。

```bash
.venv/bin/python -m pip install -r requirements-encoder.txt
.venv/bin/python -m pytest tests/test_encoder.py -q
```

可选运行依赖与传递依赖固定在 `requirements-encoder.txt`。默认安装脚本不下载预训练模型。缺文件、缺可选依赖、SHA 不匹配或导出格式不受支持都会返回明确的 `ContractError`，不得回退为随机特征。

## 模型导出约定

- 模型须由部署者可信地获取和配置。上传端点接收的任意模型文件不得交给运行适配器。
- 单输入 `clips`：`float32 [N,T,3,H,W]`；RGB，数值范围为 0 到 1。H、W 必须是相同的静态尺寸，并等于配置的 `input_size`。N、T 可为静态正整数或动态维。
- 单输出 `features`：`float32 [N,D]`；D 必须为静态维度，等于配置的 `feature_dim`。运行时再校验真实输出的行数、维度和有限值。
- 适配器不做隐藏的颜色转换、归一化或 pooling。视频管线负责按配置采样和调整空间尺寸；模型所需均值/标准差归一化及 pooling 必须包含在导出图中，其更改也会改变权重文件 SHA。
- 仅支持标准 ONNX 算子、自包含权重与 CPUExecutionProvider。导出为 eval/inference 模式，移除 Dropout、随机采样、训练图、自定义算子和本地函数。External data 引用在初始化运行时之前递归拒绝。
- 可选依赖采用 ONNX 1.17.0、ONNX Runtime 1.20.1；测试导出使用 IR 10、opset 13。高于运行时支持范围的版本会明确拒绝。

## 调用与来源证据

```python
from pathlib import Path
from packages.encoders import load_encoder

encoder = load_encoder(
    weights_path=Path(server_configured_path),
    expected_sha=configured_sha_or_none,
    feature_dim=configured_dimension,
    input_size=configured_size,
    threads=configured_cpu_threads,
)
vectors = encoder.encode(clips)
actual_encoder_sha = encoder.weights_sha
```

参数来自 `system.yaml` 的服务端配置。文件仅读取一次；对同一份 bytes 计算 SHA256、解析 ONNX 和创建推理会话，因此加载后文件被替换不会改变已加载会话的模型身份。提供 `expected_sha` 时还会检查部署者固定的哈希。NPZ 只提供模型名称而没有实际文件时，不能生成已核验的编码器 SHA。

该接口没有训练入口或优化器。`tests/test_encoder.py` 实际执行一个确定性的 ReduceMean + Add 小型计算图，验证输出、文件 SHA、导出约定、有限值检查和外部数据拒绝。这个图仅为解析与计算流程测试，不是视觉语义模型，不代表动作识别、时序学习或现场效果。真实模型权重与真实数据评测仍需后续提供。

实际 MP4 导入要求 `encoder.adapter: onnx`，且必须设置非空 `encoder.weights_sha256`；缺少锁定 SHA 会 blocked。将 `profiles.demo.encoder_weights` 指向可信模型文件，`encoder.feature_dim` 与输出 D 一致，`ingestion.encoder_input_size` 与 H/W 一致。修改配置后重启并新建导入，旧任务配置及结果保留。暂不自动转换或下载 VideoMAE/V-JEPA2 权重。
