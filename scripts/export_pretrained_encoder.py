#!/usr/bin/env python3
"""Export the SHA-pinned official torchvision R3D-18 checkpoint, CPU only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np

from packages.encoders import pretrained as recipe
from packages.encoders.onnx_cpu import load_encoder


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def official_checkpoint(path, download=False):
    path = Path(path)
    if not path.is_file():
        if not download:
            raise RuntimeError("Official checkpoint missing; use --download or provide the pinned local checkpoint. No random fallback.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve environment proxy and TLS verification; atomically publish
        # only after the complete official bytes match the pinned SHA256.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
            temp = Path(target.name)
            try:
                with urllib.request.urlopen(recipe.CHECKPOINT_URL, timeout=60) as response:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        target.write(block)
                target.close()
                if sha256(temp) != recipe.CHECKPOINT_SHA256:
                    raise RuntimeError("Official checkpoint SHA256 mismatch")
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
    if sha256(path) != recipe.CHECKPOINT_SHA256:
        raise RuntimeError("Official checkpoint SHA256 mismatch; export refused")
    return path


def validation_clips(video):
    """Two distinct real source intervals, for conversion diagnostics only."""
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(video)], timeout=30))["streams"][0]
    width, height = info["width"], info["height"]
    clips, raw = [], []
    for start in (0, 30):
        data = subprocess.check_output(["ffmpeg", "-v", "error", "-threads", "1", "-ss", str(start),
            "-i", str(video), "-vf", "fps=8", "-frames:v", "16", "-an", "-sn", "-dn",
            "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], timeout=60)
        frames = np.frombuffer(data, dtype=np.uint8).reshape(16, height, width, 3)
        raw.append(frames)
        clips.append(recipe.clip_tensor(frames, list(range(16)), (0, 0, 1, 1), 112))
    return np.concatenate(clips), raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "artifacts/models/r3d_18-b3b3357e.pth")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/models/r3d18-kinetics400-v1.onnx")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/delivery/p0.3.1/encoder/export.json")
    parser.add_argument("--video", type=Path, default=ROOT / "datasets/video_samples/garment_quality/videos/test.mp4")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    checkpoint = official_checkpoint(args.checkpoint, args.download)
    import torch
    import torchvision
    from torchvision.models.video import r3d_18, R3D_18_Weights
    if torch.__version__ != recipe.TORCH_VERSION or torchvision.__version__ != recipe.TORCHVISION_VERSION:
        raise RuntimeError("Install the pinned CPU export dependencies from requirements-pretrained.txt")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    network = r3d_18(weights=None)
    network.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True), strict=True)
    network.fc = torch.nn.Identity()
    network.eval().requires_grad_(False)

    class FrozenFeatureEncoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.network = network
            self.register_buffer("rgb_mean", torch.tensor(recipe.MEAN).view(1, 1, 3, 1, 1))
            self.register_buffer("rgb_std", torch.tensor(recipe.STD).view(1, 1, 3, 1, 1))

        def forward(self, clips):
            return self.network(((clips - self.rgb_mean) / self.rgb_std).permute(0, 2, 1, 3, 4))

    model = FrozenFeatureEncoder().eval().requires_grad_(False)
    clips, raw = validation_clips(args.video)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with torch.inference_mode():
        expected = model(torch.from_numpy(clips)).numpy()
        torch.onnx.export(model, torch.from_numpy(clips[:1]), str(args.output),
            input_names=["clips"], output_names=["features"], opset_version=17,
            dynamic_axes={"clips": {0: "N"}, "features": {0: "N"}},
            do_constant_folding=True, dynamo=False)
    onnx_sha = sha256(args.output)
    encoder = load_encoder(args.output, onnx_sha, 512, 112, 2)
    obtained = encoder.encode(clips)
    reloaded = load_encoder(args.output, onnx_sha, 512, 112, 2).encode(clips)
    conversion_error = float(np.max(np.abs(expected - obtained)))
    reload_error = float(np.max(np.abs(obtained - reloaded)))
    distance = float(np.linalg.norm(obtained[0] - obtained[1]))
    preprocessing_error = 0.0
    official = R3D_18_Weights.KINETICS400_V1.transforms()
    for index, frames in enumerate(raw):
        tensor = torch.from_numpy(frames.copy()).permute(0, 3, 1, 2).float() / 255
        with torch.inference_mode():
            reference = official(tensor).numpy()
        actual = ((clips[index] - np.asarray(recipe.MEAN, dtype=np.float32)[None, :, None, None]) /
                  np.asarray(recipe.STD, dtype=np.float32)[None, :, None, None]).transpose(1, 0, 2, 3)
        preprocessing_error = max(preprocessing_error, float(np.max(np.abs(reference - actual))))
    passed = conversion_error < 1e-4 and reload_error == 0 and distance > 1e-3 and preprocessing_error < 1e-4
    report = {
        "schema": "jianwei-pretrained-export-v1", "passed": passed,
        "architecture": "torchvision.models.video.r3d_18; fc replaced by Identity",
        "training_dataset": "Kinetics-400", "weights_enum": "R3D_18_Weights.KINETICS400_V1",
        "checkpoint_url": recipe.CHECKPOINT_URL, "checkpoint_sha256": recipe.CHECKPOINT_SHA256,
        "checkpoint_bytes": checkpoint.stat().st_size, "onnx_sha256": onnx_sha,
        "onnx_bytes": args.output.stat().st_size, "onnx_opset": 17,
        "source_revision": "torchvision v0.22.1",
        "source_url": "https://github.com/pytorch/vision/blob/v0.22.1/torchvision/models/video/resnet.py",
        "license": {"implementation": "BSD-3-Clause", "url": "https://github.com/pytorch/vision/blob/v0.22.1/LICENSE",
                    "weights_notice": "Torchvision pretrained models may have separate dataset-derived terms; BSD implementation license is not a grant of rights to all Kinetics source videos.",
                    "notice_url": "https://github.com/pytorch/vision/tree/v0.22.1#pre-trained-models"},
        "frozen": True, "training": False, "requires_grad_parameters": sum(p.requires_grad for p in model.parameters()),
        "input_contract": ["N", 16, 3, 112, 112], "input_dtype": "float32 RGB [0,1]",
        "output_contract": ["N", 512], "output_dtype": "float32", "device": "CPU",
        "preprocessing_version": recipe.PREPROCESSING_VERSION,
        "preprocessing": {"float_before_resize": True, "resize_hw": [128, 171], "interpolation": "bilinear",
            "align_corners": False, "antialias": False, "center_crop_hw": [112, 112],
            "mean": recipe.MEAN, "std": recipe.STD, "normalization_in_onnx": True},
        "dependencies": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "numpy", "onnx", "onnxruntime")},
        "validation": {"video_sha256": sha256(args.video), "intervals_seconds": [[0, 2], [30, 32]],
            "sampling": "FFmpeg fps=8, sixteen frames per interval; conversion diagnostics, not independent research sources",
            "framework_vs_onnx_max_abs_error": conversion_error, "framework_vs_onnx_tolerance": 1e-4,
            "official_preprocessing_max_abs_error": preprocessing_error, "official_preprocessing_tolerance": 1e-4,
            "reload_max_abs_error": reload_error, "distinct_clip_l2_distance": distance,
            "feature_std": float(obtained.std()), "features_finite": bool(np.isfinite(obtained).all()),
            "elapsed_seconds": time.perf_counter() - started},
        "limitations": ["Kinetics pretrained representation is not garment action truth.",
            "One source is insufficient for independent train/calibration/test; no accuracy or generalization claim."]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not passed:
        raise RuntimeError(f"Conversion validation failed: {report['validation']}")
    print(json.dumps({"passed": passed, "onnx_sha256": onnx_sha, "report": str(args.report), "validation": report["validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
