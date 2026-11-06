"""Ship annotation tools and synthetic PTS only, with aggregate performance."""
import argparse,json,hashlib,zipfile
from pathlib import Path
from scripts.check_public_research_artifacts import inspect_file
from packages.local_motion.annotation import validate_source

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/demo/p0.3.7-b11';ARCHIVE=OUT/'jianwei-p0.3.7-b11-demo.zip'
sha=lambda b:hashlib.sha256(b).hexdigest()


def verify():
    inspect_file(ARCHIVE);outer=json.loads((OUT/'manifest.json').read_text());assert sha(ARCHIVE.read_bytes())==outer['archive_sha256']
    # Historical B.1.1 snapshot remains immutable after authorized B.2-U work.
    assert outer['archive_sha256']=='f839ff6fdfd70b4b10b8546ef2adf03a1b9d5a27422c8c2faa2bfe40bcea68ca'
    with zipfile.ZipFile(ARCHIVE) as z:
        assert z.testzip() is None;manifest=json.loads(z.read('manifest.json'))
        assert set(z.namelist())==set(manifest['files'])|{'manifest.json'}
        for n,r in manifest['files'].items():assert sha(z.read(n))==r['sha256']
        source=json.loads(z.read('synthetic-source.json'));validate_source(source);assert source['evidence_class']=='synthetic_engineering'
        assert z.read('index.html')==(OUT/'index.html').read_bytes()
        assert not manifest['industrial_frames_included'] and not manifest['industrial_annotation_included']
    return {'passed':True,'archive_sha256':outer['archive_sha256'],'html_sha256':sha((OUT/'index.html').read_bytes()),'industrial_human_f1':None,'b2_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    if a.verify_only:print(json.dumps(verify()));return
    if OUT.exists():raise FileExistsError('Choose a new delivery version; preserve historical Demo')
    source=json.loads((ROOT/'apps/web/src/data/annotationDemoSource.json').read_text());validate_source(source);assert source['evidence_class']=='synthetic_engineering'
    files={'index.html':(ROOT/'apps/web/dist-demo/index.html').read_bytes(),'static/brand-mark.svg':(ROOT/'apps/web/dist-demo/static/brand-mark.svg').read_bytes(),
        'synthetic-source.json':(ROOT/'apps/web/src/data/annotationDemoSource.json').read_bytes(),
        'performance-aggregate.json':(ROOT/'docs/delivery/p0.3.7-b11/performance.json').read_bytes(),
        'generate_practice.py':(ROOT/'demo/generate_b11_practice.py').read_bytes(),
        '人工操作清单.md':(ROOT/'docs/delivery/p0.3.7-b11/ANNOTATION-GUIDE.md').read_bytes(),
        '使用说明.txt':('解压后运行 python -m http.server 18072 --bind 127.0.0.1，打开 http://127.0.0.1:18072/index.html。\n'
            '选择“动作边界盲标注”，先读人工操作清单。默认源是实际生成素材的PTS，仅工程演示，不含视频或已完成工业真值。\n'
            '练习播放：安装Python及FFmpeg后运行 python generate_practice.py --output practice-output，载入practice-output/source.json并选择其中generated.mp4；不需要模型权重。\n'
            '真实标注需在授权环境生成仅含原始PTS的源描述，载入后选择本机原片，SHA核验通过才播放；不上传。\n'
            '新盲标人员使用未看过预测和他人标注的独立浏览器档案。盲法和人员身份是自报，无法用清理浏览器抹去既有知识。\n'
            '本工具不自动填人工边界；须全片审核、补漏检/尾部、记录未知/遮挡等，封存并导出JSON后运行CLI固定评价。\n'
            '缺真实人工标注时Precision/Recall/F1保持blocked。不同第二标注独立导入，分歧不自动融合为共识。\n'
            '不含工业原片、帧图、源PTS、逐窗口曲线、新工业特征、权重或标注；历史B.1汇总与合成曲线保留其证据类别。\n'
            '本机预览，不是公网托管。只完成B.1.1，不自动进入B.2或开放P0.4。\n').encode()}
    approved=json.loads((ROOT/'apps/web/browser-files.json').read_text())['files']
    for name,value in approved.items():assert sha((ROOT/name).read_bytes())==value
    manifest={'stage':'P0.3.7-B.1.1','industrial_frames_included':False,'industrial_annotation_included':False,'b2_started':False,
        'reviewed_source_sha256':approved,'files':{n:{'sha256':sha(v),'bytes':len(v)} for n,v in files.items()}}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();OUT.mkdir(parents=True);(OUT/'index.html').write_bytes(files['index.html'])
    with zipfile.ZipFile(ARCHIVE,'w',zipfile.ZIP_DEFLATED) as z:
        for name,body in sorted(files.items()):item=zipfile.ZipInfo(name,(2026,11,5,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,body)
    (OUT/'manifest.json').write_text(json.dumps({'archive_sha256':sha(ARCHIVE.read_bytes()),'files':manifest['files']},indent=2)+'\n');print(json.dumps(verify()))


if __name__=='__main__':main()
