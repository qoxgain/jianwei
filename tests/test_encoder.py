"""Real CPU execution of an analytical test graph, not a vision benchmark."""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.encoders import load_encoder


@pytest.fixture
def onnx():
    # The base NPZ workflow intentionally does not install this optional extra.
    pytest.importorskip("onnxruntime")
    return pytest.importorskip("onnx")


def make_model(onnx, path: Path, bias: float = 0, *, input_name="clips", feature_dim=3, frames="T"):
    helper, tensor = onnx.helper, onnx.TensorProto
    graph = helper.make_graph(
        [helper.make_node("ReduceMean", [input_name], ["mean"], axes=[1, 3, 4], keepdims=0),
         helper.make_node("Add", ["mean", "bias"], ["features"])],
        "analytical_test_only",
        [helper.make_tensor_value_info(input_name, tensor.FLOAT, ["N", frames, 3, 4, 4])],
        [helper.make_tensor_value_info("features", tensor.FLOAT, ["N", feature_dim])],
        [helper.make_tensor("bias", tensor.FLOAT, [3], [bias] * 3)],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 10
    path.write_bytes(model.SerializeToString())
    return model


def assert_error(code, call):
    with pytest.raises(ContractError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_missing_weights_is_blocked_without_any_runtime(tmp_path):
    secret_path = tmp_path / "private-customer-folder" / "missing.onnx"
    error = assert_error("ENCODER_WEIGHTS_MISSING", lambda: load_encoder(secret_path, None, 3, 4, 1))
    assert "private-customer" not in str(error.detail())


@pytest.mark.parametrize("value", [0, -1, True, 1.2])
def test_invalid_thread_config_fails_before_loading(tmp_path, value):
    assert_error("ENCODER_CONFIG_INVALID", lambda: load_encoder(tmp_path / "model", None, 3, 4, value))


def test_hash_mismatch_is_checked_on_actual_bytes_before_runtime(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"not-a-model")
    assert_error("ENCODER_HASH_MISMATCH", lambda: load_encoder(path, "0" * 64, 3, 4, 1))


def test_missing_dependency_has_actionable_block_reason(tmp_path, monkeypatch):
    from packages.encoders import onnx_cpu
    path = tmp_path / "model.onnx"
    path.write_bytes(b"model")
    def missing(_name):
        raise ImportError("private-path-must-not-leak")
    monkeypatch.setattr(onnx_cpu.importlib, "import_module", missing)
    error = assert_error("ENCODER_DEPENDENCY_MISSING", lambda: load_encoder(path, None, 3, 4, 1))
    assert "requirements-encoder.txt" in error.message
    assert "private-path" not in str(error.detail())


def test_real_cpu_features_and_fingerprint_are_reproducible(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    make_model(onnx, path, 0.25)
    expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    encoder = load_encoder(path, expected_sha, 3, 4, 1)
    clips = np.arange(2 * 8 * 3 * 4 * 4, dtype=np.float32).reshape(2, 8, 3, 4, 4)
    clips /= clips.max()
    original = clips.copy()
    features = encoder.encode(clips)
    np.testing.assert_allclose(features, clips.mean(axis=(1, 3, 4)) + 0.25, rtol=1e-6)
    np.testing.assert_array_equal(encoder.encode(clips), features)
    np.testing.assert_array_equal(clips, original)
    assert features.shape == (2, 3) and features.dtype == np.float32
    assert encoder.weights_sha == expected_sha
    # Changing the file cannot alter a loaded session or its provenance.
    make_model(onnx, path, 0.75)
    replacement = load_encoder(path, None, 3, 4, 1)
    assert replacement.weights_sha != encoder.weights_sha
    np.testing.assert_array_equal(encoder.encode(clips), features)
    np.testing.assert_allclose(replacement.encode(clips), features + 0.5, rtol=1e-6)


@pytest.mark.parametrize("kind", ["wrong_dtype", "wrong_rank", "empty", "wrong_channels", "wrong_size", "nan", "inf", "negative", "over_one"])
def test_rejects_invalid_clip_inputs(onnx, tmp_path, kind):
    path = tmp_path / "mean.onnx"
    make_model(onnx, path)
    encoder = load_encoder(path, None, 3, 4, 1)
    valid = np.zeros((1, 2, 3, 4, 4), dtype=np.float32)
    inputs = {
        "wrong_dtype": valid.astype(np.float64), "wrong_rank": valid[0], "empty": valid[:0],
        "wrong_channels": valid[:, :, :2], "wrong_size": valid[:, :, :, :3],
    }
    if kind in inputs:
        invalid = inputs[kind]
    else:
        invalid = valid.copy()
        invalid.flat[0] = {"nan": np.nan, "inf": np.inf, "negative": -0.1, "over_one": 1.1}[kind]
    assert_error("ENCODER_INPUT_INVALID", lambda: encoder.encode(invalid))


def test_fixed_frame_count_is_enforced(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    make_model(onnx, path, frames=8)
    encoder = load_encoder(path, None, 3, 4, 1)
    assert_error("ENCODER_INPUT_INVALID", lambda: encoder.encode(np.zeros((1, 2, 3, 4, 4), dtype=np.float32)))


@pytest.mark.parametrize("expected_dim,size,input_name", [(2, 4, "clips"), (3, 8, "clips"), (3, 4, "video")])
def test_export_contract_must_match_configuration(onnx, tmp_path, expected_dim, size, input_name):
    path = tmp_path / "mean.onnx"
    make_model(onnx, path, input_name=input_name)
    assert_error("ENCODER_SHAPE_MISMATCH", lambda: load_encoder(path, None, expected_dim, size, 1))


def test_invalid_model_does_not_expose_runtime_error_or_path(onnx, tmp_path):
    path = tmp_path / "customer-private.onnx"
    path.write_bytes(b"invalid-protobuf")
    error = assert_error("ENCODER_MODEL_INVALID", lambda: load_encoder(path, None, 3, 4, 1))
    assert "customer-private" not in str(error.detail())


def test_external_weights_are_rejected_before_any_file_resolution(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    model = make_model(onnx, path)
    initializer = model.graph.initializer[0]
    initializer.ClearField("float_data")
    initializer.data_location = onnx.TensorProto.EXTERNAL
    initializer.external_data.add(key="location", value="../../private-weights.bin")
    path.write_bytes(model.SerializeToString())
    assert_error("ENCODER_EXTERNAL_DATA", lambda: load_encoder(path, None, 3, 4, 1))


def test_nested_external_tensor_is_also_rejected(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    model = make_model(onnx, path)
    tensor = onnx.TensorProto(name="external", data_type=onnx.TensorProto.FLOAT, dims=[3])
    tensor.data_location = onnx.TensorProto.EXTERNAL
    tensor.external_data.add(key="location", value="should-not-be-opened")
    model.graph.node.append(onnx.helper.make_node("Constant", [], ["unused"], value=tensor))
    path.write_bytes(model.SerializeToString())
    assert_error("ENCODER_EXTERNAL_DATA", lambda: load_encoder(path, None, 3, 4, 1))


def test_training_graph_is_rejected(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    model = make_model(onnx, path)
    model.training_info.add()
    path.write_bytes(model.SerializeToString())
    assert_error("ENCODER_TRAINING_GRAPH", lambda: load_encoder(path, None, 3, 4, 1))


def test_random_sampling_graph_is_rejected(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    model = make_model(onnx, path)
    model.graph.node.append(onnx.helper.make_node("RandomNormal", [], ["noise"], shape=[3]))
    path.write_bytes(model.SerializeToString())
    assert_error("ENCODER_NONDETERMINISTIC", lambda: load_encoder(path, None, 3, 4, 1))


def test_nonfinite_outputs_are_not_accepted(onnx, tmp_path):
    path = tmp_path / "mean.onnx"
    make_model(onnx, path, float("inf"))
    encoder = load_encoder(path, None, 3, 4, 1)
    assert_error("ENCODER_OUTPUT_INVALID", lambda: encoder.encode(np.zeros((1, 2, 3, 4, 4), dtype=np.float32)))
