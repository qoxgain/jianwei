"""Read a small, bounded numeric NPZ dialect without pickle or implicit objects."""
from pathlib import Path
import math
import zipfile
import numpy as np
from packages.config.errors import ContractError

MEMBERS = {'features.npy', 'pts_ms.npy', 'durations_ms.npy'}

def read_npz(path: Path, settings, dimension: int):
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 3 or {i.filename for i in members} != MEMBERS:
                raise ContractError('NPZ_MEMBERS', 'NPZ 只能包含 features、pts_ms、durations_ms 三个数组，且不得重复')
            if sum(i.file_size for i in members) > settings.max_expanded_bytes:
                raise ContractError('NPZ_SIZE_LIMIT', 'NPZ 解压大小超限，请减少样本或维度')
            shapes = {}
            for item in members:
                if item.flag_bits & 1:
                    raise ContractError('NPZ_ENCRYPTED', '不接受加密 NPZ')
                with archive.open(item) as member:
                    version = np.lib.format.read_magic(member)
                    if version == (1, 0):
                        shape, order, dtype = np.lib.format.read_array_header_1_0(member, max_header_size=10000)
                    elif version == (2, 0):
                        shape, order, dtype = np.lib.format.read_array_header_2_0(member, max_header_size=10000)
                    else:
                        raise ContractError('NPZ_HEADER', 'NPY 仅支持版本 1.0 或 2.0')
                    if dtype.hasobject or dtype.fields is not None or dtype.subdtype is not None or order:
                        raise ContractError('NPZ_DTYPE', '不接受对象、复合类型或 Fortran 顺序数组')
                    if item.filename == 'features.npy':
                        valid = len(shape) == 2 and dtype.kind == 'f' and dtype.itemsize in (4, 8)
                        valid = valid and shape[1] == dimension
                    else:
                        valid = len(shape) == 1 and dtype.kind in 'iuf' and dtype.itemsize <= 8
                    if not valid:
                        raise ContractError('NPZ_SHAPE_DTYPE', 'features 必须是维度匹配的二维 float32/64；时间数组必须是一维数值')
                    if not shape[0] or shape[0] > settings.max_rows:
                        raise ContractError('NPZ_ROW_LIMIT', 'NPZ 样本数为空或超过限制')
                    expected = math.prod(shape) * dtype.itemsize
                    if expected > settings.max_expanded_bytes or expected + member.tell() != item.file_size:
                        raise ContractError('NPZ_HEADER_SIZE', 'NPY 头声明大小与实际成员大小不符或超过限制')
                    shapes[item.filename] = shape
            if len({v[0] for v in shapes.values()}) != 1:
                raise ContractError('NPZ_ROW_MISMATCH', '特征与 PTS、持续时间的行数不一致')
        with np.load(path, allow_pickle=False, max_header_size=10000) as data:
            features = np.asarray(data['features'])
            pts = np.asarray(data['pts_ms'])
            duration = np.asarray(data['durations_ms'])
            if not all(np.isfinite(v).all() for v in (features, pts, duration)):
                raise ContractError('NPZ_NONFINITE', '数组包含 NaN 或 Inf')
            if np.any(pts < 0) or np.any(duration <= 0) or np.any(pts > 2**53 - 1) or np.any(duration > settings.max_duration_seconds * 1000):
                raise ContractError('NPZ_TIME', 'PTS 必须非负且持续时间必须是限制内正数')
            if np.any(pts != np.floor(pts)) or np.any(duration != np.floor(duration)):
                raise ContractError('NPZ_TIME', '时间必须是整数毫秒')
            # Copies detach every array from the ZIP handle. No float32 downcast overflow.
            return features.copy(), pts.astype(np.int64), duration.astype(np.int64)
    except ContractError:
        raise
    except (OSError, ValueError, TypeError, EOFError, zipfile.BadZipFile, RuntimeError, OverflowError):
        raise ContractError('NPZ_INVALID', '无法读取合法的数值 NPZ，请按模板重新导出') from None
