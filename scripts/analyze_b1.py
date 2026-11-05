"""Analyze private B.1 features and optionally apply a separate manual log."""
import argparse,json
from pathlib import Path
from packages.local_motion.analysis import analyze
from packages.local_motion.review import compile_review
from packages.local_motion.extraction import write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True);group.add_argument('--features',type=Path);group.add_argument('--frozen-report',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--review',type=Path)
    p.add_argument('--source-descriptor',type=Path);p.add_argument('--second-review',type=Path)
    p.add_argument('--baseline',type=Path,default=Path(__file__).resolve().parents[1]/'apps/web/src/data/localMotionStatistics.json');a=p.parse_args()
    if a.output.exists():raise FileExistsError('Choose a new report path, preserve historical reports')
    if a.frozen_report:
        if not a.review or not a.source_descriptor:p.error('Frozen B.1.1 evaluation requires --review and --source-descriptor; never regenerate candidates')
        from scripts.evaluate_b11 import evaluate_files
        result=evaluate_files(a.frozen_report,a.source_descriptor,a.review,a.baseline,a.second_review)
        write(a.output,result);print(json.dumps({'status':result['status'],'candidate_run_id':result['candidate_run_id'],'human_evaluation':result['annotation']['eligible_for_evaluation']}));return
    if a.source_descriptor or a.second_review:p.error('B.1.1 source/second review requires --frozen-report')
    report=analyze(a.features)
    if a.review:report['manual_review']=compile_review(report,json.loads(a.review.read_text()))
    write(a.output,report);print(json.dumps({'run_id':report['run_id'],'candidates':report['primary']['count'],'evaluation':report['boundary_evaluation']}))


if __name__=='__main__':main()
