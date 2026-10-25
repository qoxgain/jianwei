"""Pinned official R3D-18 input recipe; inference needs NumPy, not PyTorch.

The optional exporter alone imports torch. This module never downloads weights.
VideoClassification supports float RGB [0,1]; use that route to avoid an
additional uint8 rounding between resize and normalization.
"""
from __future__ import annotations

import numpy as np

from packages.config.errors import ContractError

PREPROCESSING_VERSION = "torchvision-r3d18-kinetics400-v1"
CHECKPOINT_URL = "https://download.pytorch.org/models/r3d_18-b3b3357e.pth"
CHECKPOINT_SHA256 = "b3b3357ead25631ec9c57362ff2128a92d0427e01e2cd184951a44380c3f2e9d"
TORCH_VERSION = "2.7.1+cpu"
TORCHVISION_VERSION = "0.22.1+cpu"
INPUT_SIZE = 112
FRAMES_PER_CLIP = 16
FEATURE_DIM = 512
MEAN = (0.43216, 0.394666, 0.37645)
STD = (0.22803, 0.22145, 0.216989)


def clip_tensor(pixels, indices, roi, input_size):
    """ROI, float RGB, bilinear 128x171 (no AA), center 112x112.

This is torchvision R3D_18_Weights.KINETICS400_V1.transforms() before
normalization/permutation, which are embedded in the frozen ONNX graph.
Coordinates use align_corners=False, the official tensor interpolation rule.
The preceding decode scale and ROI remain part of the feature-space hash.
"""
    if input_size != INPUT_SIZE or len(indices) != FRAMES_PER_CLIP:
        raise ContractError("ENCODER_PREPROCESSING_INVALID", "正式 R3D-18 输入须为 16 帧、112×112；请检查 system.yaml")
    height, width = pixels.shape[1:3]
    x1, y1, x2, y2 = roi
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = max(left + 1, int(np.ceil(x2 * width))), max(top + 1, int(np.ceil(y2 * height)))
    crop_h, crop_w = bottom - top, right - left
    if hasattr(pixels, "require_budget"):
        pixels.require_budget(indices, extra_bytes=(len(indices) * crop_h * crop_w * 3 +
            crop_h * crop_w * 3 * 8 + (len(indices) + 12) * INPUT_SIZE * INPUT_SIZE * 3 * 4))
    crop = pixels[np.asarray(indices), top:bottom, left:right, :]
    # Torchvision center_crop uses round(), including the odd 171-112 offset.
    ys = (np.arange(round((128 - INPUT_SIZE) / 2), round((128 - INPUT_SIZE) / 2) + INPUT_SIZE,
                    dtype=np.float32) + np.float32(.5)) * np.float32(crop.shape[1] / 128) - np.float32(.5)
    xs = (np.arange(round((171 - INPUT_SIZE) / 2), round((171 - INPUT_SIZE) / 2) + INPUT_SIZE,
                    dtype=np.float32) + np.float32(.5)) * np.float32(crop.shape[2] / 171) - np.float32(.5)
    ys = np.maximum(ys, 0); xs = np.maximum(xs, 0)
    y0 = ys.astype(np.int64); x0 = xs.astype(np.int64)
    y1 = np.minimum(y0 + 1, crop.shape[1] - 1); x1 = np.minimum(x0 + 1, crop.shape[2] - 1)
    wy = (ys - y0).astype(np.float32)[None, :, None, None]
    wx = (xs - x0).astype(np.float32)[None, None, :, None]
    result = np.empty((1, len(indices), 3, INPUT_SIZE, INPUT_SIZE), dtype=np.float32)
    # Convert one frame at a time; source-resolution float clips must not
    # multiply the memory needed by the bounded decoder.
    for index, frame in enumerate(crop):
        rgb = np.asarray(frame, dtype=np.float32)[None] / 255.0
        upper = rgb[:, y0[:, None], x0[None, :]] * (1 - wx) + rgb[:, y0[:, None], x1[None, :]] * wx
        lower = rgb[:, y1[:, None], x0[None, :]] * (1 - wx) + rgb[:, y1[:, None], x1[None, :]] * wx
        resized = upper * (1 - wy) + lower * wy
        result[0, index] = resized[0].transpose(2, 0, 1)
    return result
