"""Build a media-free, weight-free P0.3.6 review archive from the verified build."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from packages.config.loader import PROJECT_ROOT
from scripts.check_public_research_artifacts import inspect_file


def digest(raw):return hashlib.sha256(raw).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=PROJECT_ROOT/'docs/demo/p0.3.6')
    args=p.parse_args();args.output=args.output.resolve()
    if not args.output.is_relative_to(PROJECT_ROOT):p.error('Use a repository-local review output directory')
    built=PROJECT_ROOT/'apps/web/dist-demo/index.html'
    evidence=PROJECT_ROOT/'docs/delivery/p0.3.6';browser=json.loads((evidence/'browser/results.json').read_text())
    if not browser['passed'] or browser['build_sha256']!=digest(built.read_bytes()):
        p.error('Browser evidence must match the exact current HTML build')
    files={'index.html':built.read_bytes()}
    for name in ('research/RESULTS.md','research/summary.json','research/per-horizon.csv',
                 'research/execution-provenance.json','research/cache-equivalence.json','browser/results.json'):
        files['evidence/'+name]=(evidence/name).read_bytes()
    files['使用说明.txt']='''见微 P0.3.6 · 真实视频稳健性研究

解压后打开 index.html，点击左侧“真实视频学习实验”。
默认显示 P0.3.6 五种子、18模型、8/16/32历史队列；另保留 P0.3.5 历史探索。
页面只展示已执行的标量统计，不会训练、上传文件或调用正式研究API。
可选视频、种子、模型、跨度和分区，查看损失、误差、实际/预测特征范数、变化量分组并导出统计。
表格在手机上可横向和纵向滚动。

已有原片的用户可选择“本机原片”，SHA匹配后在本机播放器回查预测/观测时刻。
本包没有视频、PDF、原片派生画面、512维特征、预测向量、模型权重或数据库。
HEVC兼容性由本机浏览器决定，未提供公开转码视频。

九段视频仅供回顾性开发。历史留出已经观察，多种子不是独立录像，正式研究与P0.4仍blocked。
残差模型未稳定优于简单基线；完整负结果见 evidence/research/RESULTS.md。

若浏览器限制本地文件，在解压目录运行：
python -m http.server 8080 --bind 127.0.0.1
在自己的电脑打开 http://127.0.0.1:8080。
源码：https://github.com/qoxgain/jianwei/tree/codex/p0-3-6-reliability-temporal-20261103
'''.encode()
    manifest={'stage':'P0.3.6','media_included':False,'feature_values_included':False,
        'weight_values_included':False,'formal_research_status':'blocked','p04_admitted':False,
        'files':{name:{'sha256':digest(raw),'bytes':len(raw)} for name,raw in sorted(files.items())}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    args.output.mkdir(parents=True,exist_ok=True);target=args.output/'jianwei-p0.3.6-demo.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,raw in sorted(files.items()):
            item=zipfile.ZipInfo(name,(2026,11,3,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,raw)
    inspect_file(target)
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        assert set(z.namelist())==set(files)
        for name,item in manifest['files'].items():assert digest(z.read(name))==item['sha256']
        assert z.read('index.html')==built.read_bytes()
    result={'path':str(target.relative_to(PROJECT_ROOT)),'bytes':target.stat().st_size,'sha256':digest(target.read_bytes()),
        'index_html_sha256':manifest['files']['index.html']['sha256'],'archive_integrity_verified':True,
        'matches_browser_tested_build':True,'media_features_weights_database_included':False}
    (args.output/'manifest.json').write_text(json.dumps({**result,'contents':manifest},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
