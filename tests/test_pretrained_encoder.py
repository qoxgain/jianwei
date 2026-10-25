"""Official preprocessing parity and missing/corrupt checkpoint fail-closed gates."""
from pathlib import Path

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.encoders import pretrained
from scripts.export_pretrained_encoder import official_checkpoint


def test_missing_official_checkpoint_cannot_fall_back(tmp_path):
    with pytest.raises(RuntimeError, match="No random fallback"):
        official_checkpoint(tmp_path / "missing.pth")


def test_corrupt_official_checkpoint_is_rejected_before_loading(tmp_path):
    path = tmp_path / "wrong.pth"
    path.write_bytes(b"not the official checkpoint")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        official_checkpoint(path)


@pytest.mark.parametrize("size,frames", [(224, 16), (112, 8), (112, 17)])
def test_wrong_official_input_recipe_is_rejected(size, frames):
    with pytest.raises(ContractError) as error:
        pretrained.clip_tensor(np.zeros((frames, 16, 16, 3), dtype=np.uint8), list(range(frames)), (0, 0, 1, 1), size)
    assert error.value.code == "ENCODER_PREPROCESSING_INVALID"


@pytest.mark.parametrize("shape,roi", [((480, 640), (0, 0, 1, 1)), ((97, 133), (.1, .2, .8, .9)), ((1, 1), (0, 0, 1, 1))])
def test_numpy_preprocessing_matches_pinned_official_transform(shape, roi):
    torch = pytest.importorskip("torch")
    torchvision = pytest.importorskip("torchvision")
    from torchvision.models.video import R3D_18_Weights
    pixels = np.random.default_rng(123).integers(0, 256, size=(16, *shape, 3), dtype=np.uint8)
    actual = pretrained.clip_tensor(pixels, list(range(16)), roi, 112)
    height, width = shape
    left, top = int(roi[0] * width), int(roi[1] * height)
    right, bottom = max(left + 1, int(np.ceil(roi[2] * width))), max(top + 1, int(np.ceil(roi[3] * height)))
    crop = pixels[:, top:bottom, left:right]
    official = R3D_18_Weights.KINETICS400_V1.transforms()(torch.from_numpy(crop.copy()).permute(0, 3, 1, 2).float() / 255).numpy()
    actual_normalized = ((actual[0] - np.asarray(pretrained.MEAN, dtype=np.float32)[None, :, None, None]) /
                         np.asarray(pretrained.STD, dtype=np.float32)[None, :, None, None]).transpose(1, 0, 2, 3)
    assert actual.dtype == np.float32 and actual.shape == (1, 16, 3, 112, 112)
    np.testing.assert_allclose(actual_normalized, official, atol=1e-4, rtol=0)


def test_official_preprocessing_respects_frame_store_budget():
    class RefusingStore:
        shape = (16, 480, 640, 3)
        def require_budget(self, indices, *, extra_bytes):
            assert extra_bytes > 16 * 112 * 112 * 3 * 4
            raise ContractError("VIDEO_DECODE_SIZE_LIMIT", "test memory budget")
        def __getitem__(self, key):
            raise AssertionError("must reject budget before allocating source frames")
    with pytest.raises(ContractError) as error:
        pretrained.clip_tensor(RefusingStore(), list(range(16)), (0, 0, 1, 1), 112)
    assert error.value.code == "VIDEO_DECODE_SIZE_LIMIT"
