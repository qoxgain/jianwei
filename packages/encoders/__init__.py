"""Optional frozen encoders. Importing this module does not import ONNX Runtime."""

from .onnx_cpu import FrozenOnnxEncoder, load_encoder

__all__ = ["FrozenOnnxEncoder", "load_encoder"]
