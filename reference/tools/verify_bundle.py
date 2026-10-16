"""Run the bounded toolkit checks and save auditable results."""
import contextlib
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path
import yaml
from config_doctor import load_bundle,read_yaml,preflight
from contracts import Knowledge,MemoryPatch,TeacherResult,Forecast
from learning_core import validate_manifest
from self_learning_demo import run

ROOT=Path(__file__).resolve().parents[1]

def main():
    out=ROOT/'verification';out.mkdir(exist_ok=True)
    checks={}
    for profile in ['demo','lab','site']:
        cfg,kn,root=load_bundle(ROOT/'config/system.yaml',profile)
        checks['static_'+profile]='PASS'
    checks['live_local_preflight_expected_missing']=preflight(cfg,kn,root)
    checks['memory_patch']='PASS' if MemoryPatch.model_validate(read_yaml(ROOT/'knowledge/memory_patch.example.yaml')) else 'FAIL'
    TeacherResult.model_validate_json((ROOT/'examples/teacher_result.json').read_text())
    Forecast.model_validate_json((ROOT/'examples/forecast.json').read_text())
    checks['contract_examples']='PASS'
    rows=[json.loads(s) for s in (ROOT/'examples/dataset_manifest.jsonl').read_text().splitlines() if s]
    checks['dataset_manifest']=validate_manifest(rows)
    with sqlite3.connect(':memory:') as db:
        db.executescript((ROOT/'contracts/reference.sql').read_text())
        checks['sql_tables']=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    api=yaml.safe_load((ROOT/'contracts/openapi.yaml').read_text())
    checks['openapi_paths']=len(api['paths'])
    checks['json_schema_files']=len(list((ROOT/'schemas').glob('*.schema.json')))
    # Syntax/relative-reference check, not an OpenAPI linter or live API test.
    def visit(node):
        if isinstance(node,dict):
            if '$ref' in node and not node['$ref'].startswith('#'):
                assert (ROOT/'contracts'/node['$ref']).exists(),node['$ref']
            for value in node.values(): visit(value)
        elif isinstance(node,list):
            for value in node: visit(value)
    visit(api)
    proc=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,text=True,capture_output=True)
    (out/'unit_tests.txt').write_text(proc.stdout+proc.stderr,encoding='utf-8')
    if proc.returncode: raise RuntimeError('unit tests failed')
    checks['unit_tests']='PASS'
    checks['unit_test_output_sha256']=hashlib.sha256((out/'unit_tests.txt').read_bytes()).hexdigest()
    with contextlib.redirect_stdout(io.StringIO()): run_dir,report=run(ROOT/'config/system.yaml')
    (out/'demo_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'demo_prediction_log.jsonl').write_bytes((run_dir/'prediction_log.jsonl').read_bytes())
    predictions={};scored=0
    for line in (run_dir/'prediction_log.jsonl').read_text().splitlines():
        event=json.loads(line)
        if event['type']=='prediction':
            assert event['input_max_pts_ms']<=event['issued_at_pts_ms']<=event['target_start_pts_ms']<event['target_end_pts_ms']
            predictions[event['prediction_id']]=event
        else:
            prior=predictions[event['prediction_id']]
            assert event['available_at_pts_ms']>=prior['target_end_pts_ms'];scored+=1
    checks['causal_prediction_outcome_pairs']=scored
    checks['synthetic_feature_demo']='PASS'
    checks['real_video_evaluation']='NOT_RUN'
    checks['production_services']='NOT_IMPLEMENTED'
    record={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'toolkit_only','checks':checks,
            'runtime':{'python':sys.version.split()[0]},'release_claim':'No industrial accuracy claim; no production model activation.'}
    (out/'toolkit_verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
