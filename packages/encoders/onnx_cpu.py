"""Execute a trusted, self-contained ONNX inference graph with immutable weights.

The caller selects a server-configured model; uploaded encoder fingerprints must
never be passed here. NPZ ingestion only hashes those artifacts and has no reason
to import this optional runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
from pathlib import Path
import re
import math
import time
from threading import Event, Thread
from typing import Any

import numpy as np

from packages.config.errors import ContractError


def _positive_int(value: int) -> bool:
    return type(value) is int and value > 0


def _walk_messages(message):
    """Include tensors in nested graphs, sparse tensors and function attributes."""
    yield message
    for descriptor, value in message.ListFields():
        if descriptor.type != descriptor.TYPE_MESSAGE:
            continue
        if descriptor.label == descriptor.LABEL_REPEATED:
            for child in value:
                yield from _walk_messages(child)
        else:
            yield from _walk_messages(value)


def _check_graph(model, onnx) -> None:
    if model.training_info:
        raise ContractError("ENCODER_TRAINING_GRAPH", "编码器必须导出为冻结推理图，不得包含训练图")
    # Local/custom functions may invoke non-standard kernels. Keep the supported
    # export contract limited to standard ONNX inference operators.
    if model.functions or any(item.domain not in {"", "ai.onnx"} for item in model.opset_import):
        raise ContractError("ENCODER_CUSTOM_OP", "编码器仅支持标准 ONNX 推理算子")
    nondeterministic = {"RandomNormal", "RandomNormalLike", "RandomUniform", "RandomUniformLike", "Multinomial", "Bernoulli", "Dropout"}
    for message in _walk_messages(model):
        if isinstance(message, onnx.TensorProto) and (message.data_location == onnx.TensorProto.EXTERNAL or message.external_data):
            raise ContractError("ENCODER_EXTERNAL_DATA", "编码器须为单个自包含 ONNX 文件，不允许外部权重引用")
        if isinstance(message, onnx.NodeProto):
            if message.domain not in {"", "ai.onnx"}:
                raise ContractError("ENCODER_CUSTOM_OP", "编码器仅支持标准 ONNX 推理算子")
            if message.op_type in nondeterministic:
                raise ContractError("ENCODER_NONDETERMINISTIC", "编码器须关闭随机采样和 Dropout 后导出")
    try:
        onnx.checker.check_model(model)
    except Exception:
        raise ContractError("ENCODER_MODEL_INVALID", "ONNX 模型结构校验失败，请重新导出受支持的推理图") from None


def _shape_matches(actual: tuple[int, ...], declared: tuple[Any, ...]) -> bool:
    return len(actual) == len(declared) and all(
        not isinstance(expected, int) or expected == obtained
        for obtained, expected in zip(actual, declared)
    )


@dataclass(frozen=True)
class FrozenOnnxEncoder:
    """CPU inference only; no optimizer, mutable weight API or training method."""

    weights_sha: str
    feature_dim: int
    input_size: int
    input_shape: tuple[Any, ...]
    _session: Any = field(repr=False, compare=False)
    _run_options_factory: Any = field(default=None, repr=False, compare=False)

    def encode(self, clips: np.ndarray, *, deadline: float | None = None, check_cancelled=None) -> np.ndarray:
        """Encode RGB clips; optional absolute monotonic deadline covers this run.

        ORT's per-run termination flag cancels between native CPU operators, so
        an in-flight call also observes the import budget/cancellation. Joining
        the monitor ensures no callback survives this invocation. A termination
        request waits for the active native operator to return; it is not a hard
        operating-system kill of an arbitrary blocked custom kernel (which our
        graph contract forbids).
        """
        if deadline is not None and (not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
            raise ContractError("ENCODER_CONFIG_INVALID", "编码器截止时间必须是有限的单调时钟值")
        if not isinstance(clips, np.ndarray) or clips.dtype != np.dtype("float32"):
            raise ContractError("ENCODER_INPUT_INVALID", "编码器输入必须为 float32 RGB 数组")
        if clips.ndim != 5 or any(size <= 0 for size in clips.shape) or clips.shape[2:] != (3, self.input_size, self.input_size):
            raise ContractError("ENCODER_INPUT_INVALID", "编码器输入须满足 [N,T,3,H,W] 与配置尺寸，且各维非空")
        if not _shape_matches(clips.shape, self.input_shape):
            raise ContractError("ENCODER_INPUT_INVALID", "片段批次或帧数与模型导出尺寸不匹配")
        if not np.isfinite(clips).all() or np.min(clips) < 0 or np.max(clips) > 1:
            raise ContractError("ENCODER_INPUT_INVALID", "RGB 输入必须为 0 到 1 的有限数值")
        def check():
            if check_cancelled is not None:
                check_cancelled()
            if deadline is not None and time.monotonic() >= deadline:
                raise ContractError("ENCODER_TIMEOUT", "特征阶段超过配置时间限制")

        check()
        finished, interrupted = Event(), []
        monitor = None
        options = None
        if deadline is not None or check_cancelled is not None:
            factory = self._run_options_factory
            if factory is None:
                factory = importlib.import_module("onnxruntime").RunOptions
            options = factory()

            def watch():
                while not finished.wait(.05):
                    try:
                        check()
                    except Exception as error:
                        interrupted.append(error)
                        options.terminate = True
                        return

            monitor = Thread(target=watch, name="jianwei-encoder-deadline", daemon=True)
            monitor.start()
        failure = None
        try:
            inputs = {"clips": np.ascontiguousarray(clips)}
            features = (self._session.run(["features"], inputs, run_options=options)
                        if options is not None else self._session.run(["features"], inputs))[0]
        except Exception as error:
            failure = error
        finally:
            finished.set()
            if monitor is not None:
                monitor.join()
        if interrupted:
            raise interrupted[0]
        check()
        if failure is not None:
            raise ContractError("ENCODER_INFERENCE_FAILED", "冻结编码器执行失败，请检查导出图与输入约定") from None
        if not isinstance(features, np.ndarray) or features.dtype != np.dtype("float32") or features.shape != (clips.shape[0], self.feature_dim):
            raise ContractError("ENCODER_OUTPUT_INVALID", "编码器输出须为 float32 [N,D]，并匹配配置维度")
        if not np.isfinite(features).all():
            raise ContractError("ENCODER_OUTPUT_INVALID", "编码器输出包含 NaN 或 Inf，未写入特征缓存")
        return np.ascontiguousarray(features)


def load_encoder(weights_path: Path, expected_sha: str | None, feature_dim: int, input_size: int, threads: int) -> FrozenOnnxEncoder:
    """Load bytes once, fingerprint and validate them, then open a CPU session.

    ``weights_path`` must come from trusted server configuration, never an upload
    request. ``expected_sha=None`` still computes and reports the actual digest;
    supplying a digest additionally pins the operator-approved model.
    """
    if not all(_positive_int(value) for value in (feature_dim, input_size, threads)):
        raise ContractError("ENCODER_CONFIG_INVALID", "编码器维度、输入尺寸和 CPU 线程数必须为正整数")
    if expected_sha is not None and (not isinstance(expected_sha, str) or re.fullmatch(r"[a-f0-9]{64}", expected_sha) is None):
        raise ContractError("ENCODER_CONFIG_INVALID", "编码器预期 SHA256 格式无效")
    try:
        path = Path(weights_path)
        if not path.is_file():
            raise OSError
        model_bytes = path.read_bytes()
    except (OSError, TypeError, ValueError):
        raise ContractError("ENCODER_WEIGHTS_MISSING", "缺少配置指定的编码器权重；请安装受信任的自包含 ONNX 文件") from None
    digest = hashlib.sha256(model_bytes).hexdigest()
    if expected_sha is not None and digest != expected_sha:
        raise ContractError("ENCODER_HASH_MISMATCH", "编码器实际文件 SHA256 与配置不一致，已停止提取")
    try:
        onnx = importlib.import_module("onnx")
        ort = importlib.import_module("onnxruntime")
    except (ImportError, OSError):
        raise ContractError("ENCODER_DEPENDENCY_MISSING", "未安装 ONNX CPU 运行依赖；请安装 requirements-encoder.txt") from None
    try:
        # Loading from bytes does not resolve any external data. Reject references
        # before the runtime gets the same hashed bytes, not a mutable path.
        model = onnx.load_model_from_string(model_bytes)
    except Exception:
        raise ContractError("ENCODER_MODEL_INVALID", "无法读取 ONNX 模型，请检查导出文件") from None
    _check_graph(model, onnx)
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 4
        session = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
    except Exception:
        raise ContractError("ENCODER_MODEL_INVALID", "ONNX CPU 无法加载该推理图，请检查算子版本与导出约定") from None
    inputs, outputs = session.get_inputs(), session.get_outputs()
    valid = (
        session.get_providers() == ["CPUExecutionProvider"]
        and len(inputs) == 1 and len(outputs) == 1
        and inputs[0].name == "clips" and outputs[0].name == "features"
        and inputs[0].type == outputs[0].type == "tensor(float)"
        and len(inputs[0].shape) == 5 and inputs[0].shape[2:] == [3, input_size, input_size]
        and len(outputs[0].shape) == 2 and outputs[0].shape[1] == feature_dim
        and all(not isinstance(size, int) or size > 0 for size in inputs[0].shape + outputs[0].shape)
    )
    if not valid:
        raise ContractError("ENCODER_SHAPE_MISMATCH", "模型须仅接受 clips:float32[N,T,3,H,W] 并输出 features:float32[N,D]，尺寸须与配置一致")
    return FrozenOnnxEncoder(digest, feature_dim, input_size, tuple(inputs[0].shape), session, ort.RunOptions)
