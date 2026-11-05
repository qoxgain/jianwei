"""Package only the reviewed workbench, synthetic curves and industrial aggregates."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from scripts.check_public_research_artifacts import inspect_file

ROOT=Path(__file__).resolve().parents[1]
DEMO=ROOT/'docs/demo/p0.3.7-b1'
ARCHIVE=DEMO/'jianwei-p0.3.7-b1-demo.zip'


def sha(body):return hashlib.sha256(body).hexdigest()


def verify():
    inspect_file(ARCHIVE)
    outer=json.loads((DEMO/'manifest.json').read_text())
    assert sha(ARCHIVE.read_bytes())==outer['archive_sha256']
    with zipfile.ZipFile(ARCHIVE) as z:
        assert z.testzip() is None
        manifest=json.loads(z.read('manifest.json'))
        assert set(z.namelist())==set(manifest['files'])|{'manifest.json'}
        for name,record in manifest['files'].items():assert sha(z.read(name))==record['sha256']
        for name,value in manifest['reviewed_source_sha256'].items():assert sha((ROOT/name).read_bytes())==value
        assert z.read('index.html')==(DEMO/'index.html').read_bytes()
        assert z.read('industrial-aggregate.json')==(ROOT/'apps/web/src/data/localMotionStatistics.json').read_bytes()
        assert manifest['industrial_frames_included'] is False and manifest['embeddings_included'] is False
    return {'passed':True,'archive_sha256':outer['archive_sha256'],'build_sha256':sha((DEMO/'index.html').read_bytes()),
        'public_scope':'synthetic executed curves and aggregate industrial statistics; local file association only','b2_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    if DEMO.exists():raise FileExistsError('Do not overwrite a historical delivery')
    sources=json.loads((ROOT/'apps/web/browser-files.json').read_text())['files']
    snapshot={name:sha((ROOT/name).read_bytes()) for name in sources}
    readme=('见微 P0.3.7-B.1 局部动态研究\n'
        '解压后在此目录执行 python -m http.server 18071 --bind 127.0.0.1\n'
        '浏览器打开 http://127.0.0.1:18071/index.html，选择左侧“局部动态研究”。这不是公网托管服务。\n'
        '工业汇总来自真实服装开发录像执行，背景被衣物进入，未证明动作边界效果。\n'
        '默认曲线来自实际生成素材执行，明确标记合成演示，不是工业识别结果。\n'
        '不包含任何工业视频、帧图、逐窗口工业曲线、特征向量或权重。\n'
        '完整工业回查需在授权工作区导入私有 real-report.json 并选择原始 test.mp4；SHA一致才播放，不上传。\n'
        'ROI修改是草稿，旧曲线不重算；人工记录独立导出。没有完整边界真值，不能计算真实F1。\n'
        'B.2及正式P0.4未启动；操作与复现命令见仓库 docs/delivery/P0.3.7-B.1.md。\n')
    files={'index.html':(ROOT/'apps/web/dist-demo/index.html').read_bytes(),
        'static/brand-mark.svg':(ROOT/'apps/web/dist-demo/static/brand-mark.svg').read_bytes(),
        'industrial-aggregate.json':(ROOT/'apps/web/src/data/localMotionStatistics.json').read_bytes(),
        '使用说明.txt':readme.encode()}
    manifest={'stage':'P0.3.7-B.1','industrial_frames_included':False,'embeddings_included':False,'b2_started':False,
        'reviewed_source_sha256':snapshot,'files':{n:{'sha256':sha(v),'bytes':len(v)} for n,v in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    DEMO.mkdir(parents=True)
    (DEMO/'index.html').write_bytes(files['index.html'])
    with zipfile.ZipFile(ARCHIVE,'w',zipfile.ZIP_DEFLATED) as z:
        for name,body in sorted(files.items()):
            entry=zipfile.ZipInfo(name,(2026,11,5,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;z.writestr(entry,body)
    (DEMO/'manifest.json').write_text(json.dumps({'archive_sha256':sha(ARCHIVE.read_bytes()),'files':manifest['files']},indent=2)+'\n')
    print(json.dumps(verify()))


if __name__=='__main__':main()
