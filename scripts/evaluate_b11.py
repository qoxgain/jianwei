"""Evaluate frozen predictions only after complete, sealed supplied annotation."""
import argparse,json,subprocess,hashlib
from pathlib import Path
from packages.local_motion.evaluation import evaluate
from packages.local_motion.extraction import sha,write

ROOT=Path(__file__).resolve().parents[1]


def evaluate_files(report_path,source_path,annotation_path,baseline_path,second_path=None):
    load=lambda p:json.loads(Path(p).read_text())
    sources=['packages/local_motion/annotation.py','packages/local_motion/evaluation.py','packages/local_motion/review.py','scripts/evaluate_b11.py','scripts/analyze_b1.py']
    identity={'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'source_sha256':{p:sha(ROOT/p) for p in sources},'protocol':'b11-fixed-b1-predictions-full-human-scope-v1'}
    return evaluate(load(source_path),load(report_path),load(annotation_path),report_file_sha256=sha(report_path),code_identity=identity,
        baseline=load(baseline_path),second=load(second_path) if second_path else None)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--annotations',type=Path,required=True)
    p.add_argument('--second-annotations',type=Path);p.add_argument('--baseline',type=Path,default=ROOT/'apps/web/src/data/localMotionStatistics.json');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError('New output required; preserve historical research')
    report=evaluate_files(a.report,a.source,a.annotations,a.baseline,a.second_annotations);write(a.output,report);print(json.dumps({'status':report['status'],'methods':len(report['methods'])}))


if __name__=='__main__':main()
