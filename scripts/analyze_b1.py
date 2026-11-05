"""Analyze private B.1 features and optionally apply a separate manual log."""
import argparse,json
from pathlib import Path
from packages.local_motion.analysis import analyze
from packages.local_motion.review import compile_review
from packages.local_motion.extraction import write


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--features',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--review',type=Path);a=p.parse_args()
    if a.output.exists():raise FileExistsError('Choose a new report path, preserve historical reports')
    report=analyze(a.features)
    if a.review:report['manual_review']=compile_review(report,json.loads(a.review.read_text()))
    write(a.output,report);print(json.dumps({'run_id':report['run_id'],'candidates':report['primary']['count'],'evaluation':report['boundary_evaluation']}))


if __name__=='__main__':main()
