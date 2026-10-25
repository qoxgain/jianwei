"""Package measured single-video exploration with its unchanged source footage."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from packages.config.loader import PROJECT_ROOT
from packages.ingestion.pipeline import file_hash
from scripts.preview_real_video import render_html


def package():
    evidence = PROJECT_ROOT/'docs/delivery/p0.3.1'
    report = json.loads((evidence/'preview/report.json').read_text())
    source = PROJECT_ROOT/'examples/test.mp4'
    if file_hash(source) != report['source_hash']:
        raise ValueError('Source changed; refusing to package mismatched footage')
    if file_hash(evidence/'video/vectors.npy') != report['feature_cache']['sha256']:
        raise ValueError('Feature cache changed')
    files = {
        'index.html': render_html(report, 'test.mp4').encode(),
        'report.json': (evidence/'preview/report.json').read_bytes(),
        'manifest.json': (evidence/'video/manifest.json').read_bytes(),
        'vectors.npy': (evidence/'video/vectors.npy').read_bytes(),
        'encoder-export.json': (evidence/'encoder/export.json').read_bytes(),
        'torchvision-LICENSE': (evidence/'encoder/torchvision-LICENSE').read_bytes(),
        'test.mp4': source.read_bytes(),
        'README.txt': (
            '见微 P0.3.1 · 单视频匿名探索\n\n'
            '解压到同一目录后打开 index.html，点击“查看片段”回查原视频。\n'
            '如果浏览器限制本地文件，可在解压目录运行 python -m http.server 8080 --bind 127.0.0.1，\n'
            '再打开 http://127.0.0.1:8080 。也可在页面选择同目录 test.mp4，按 SHA256 核验原文件。\n\n'
            '这是官方冻结 R3D-18 产生的实际 117×512 特征与单源回顾性匿名探索。\n'
            '仅覆盖完整固定窗口 0–60 秒；60–118.5 秒尾部窗口不完整，未纳入。\n'
            '没有动作真值、没有独立训练/校准/测试、没有动作准确率。仍需独立采集录像。\n'
            '源视频由用户提供，保持原始字节；预训练权重不包含在本包中。\n'
            '模型来源和许可记录见 encoder-export.json；特征缓存为 allow_pickle=False 的 NumPy 数组。\n'
        ).encode(),
    }
    files['SHA256.json'] = (json.dumps({name:hashlib.sha256(content).hexdigest()
        for name,content in files.items()},ensure_ascii=False,indent=2)+'\n').encode()
    destination = PROJECT_ROOT/'demo/jianwei-p0.3.1-video-demo.zip'
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name,content)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        hashes = json.loads(archive.read('SHA256.json'))
        assert all(hashlib.sha256(archive.read(name)).hexdigest()==digest for name,digest in hashes.items())
    print(f'{destination.relative_to(PROJECT_ROOT)}: {destination.stat().st_size} bytes; all packaged hashes verified')


if __name__ == '__main__':
    package()
