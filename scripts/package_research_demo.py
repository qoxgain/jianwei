"""Package the built P0.3.5 workbench and numeric evidence without media."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from packages.config.loader import PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT/'docs/demo/p0.3.5')
    args = parser.parse_args()
    built = PROJECT_ROOT/'apps/web/dist-demo/index.html'
    evidence = PROJECT_ROOT/'docs/delivery/p0.3.5'
    files = {'index.html': built.read_bytes()}
    for name in ('research-audit.json', 'verification.json', 'protocol-browser.json'):
        files['evidence/'+name] = (evidence/name).read_bytes()
    for name in ('model.json', 'weights.npz'):
        files['evidence/synthetic-model/'+name] = (evidence/'synthetic-model'/name).read_bytes()
    files['研究协议说明.md'] = (PROJECT_ROOT/'docs/RESEARCH-PROTOCOL.md').read_bytes()
    files['使用说明.txt'] = '''见微 P0.3.5 研究协议与独立会话验收

解压后使用 Chrome、Edge 或 Firefox 打开 index.html。
在“无标签观察 → 时序研究”查看新增的已执行合成研究记录、会话指标和区间。
可以切换指标、显示模型、下载报告；更换显示项不改变校准时选定的候选。
四场景旧记录仍保留；缺少新统计的旧报告会明确提示。

离线页面不训练、不读取服务器或封存特征、不确认现场记录。
随附合成统计只验证工程机制，不代表真实动作识别、SOP 合规或学习效果。
evidence/synthetic-model 中的候选权重已经校验并重载，仅供机制复现，不自动激活。
预注册协议的当前服务器配置、采集记录及真实研究准入需启动本地 API。
本包 evidence/research-audit.json 是对现有真实清单的元数据核对：9 个来源、10 个特征版本，真实研究仍 blocked。

若浏览器限制本地文件，在解压目录运行：
python -m http.server 8080 --bind 127.0.0.1
然后在自己的电脑打开 http://127.0.0.1:8080（这不是公网服务）。

源代码和完整 API 启动说明：
https://github.com/qoxgain/jianwei/tree/codex/p0-1-foundation
'''.encode('utf-8')
    hashes = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
              for name, raw in sorted(files.items())}
    files['manifest.json'] = (json.dumps({'stage': 'P0.3.5', 'media_included': False,
        'real_effect_verified': False, 'files': hashes}, ensure_ascii=False, indent=2)+'\n').encode()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output/'jianwei-p0.3.5-demo.zip'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            item = zipfile.ZipInfo(name, (2026, 11, 2, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, raw)
    print(json.dumps({'path': str(target.relative_to(PROJECT_ROOT)),
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'bytes': target.stat().st_size, 'index_html_sha256': hashes['index.html']['sha256']}))


if __name__ == '__main__':
    main()
