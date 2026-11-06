"""Bounded replay of ONE unchanged cancellation test, on generated data only.

A completed diagnosis is not a pass of every trial; preserve reproduced risks.
"""
import argparse,hashlib,json,multiprocessing,os,subprocess,sys,tempfile,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/'tests/test_p037_a1_media.py'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def victim(output):
    from tests.test_p037_a1_media import test_cancelled_and_exceptional_file_send_release_file_and_lease
    output=Path(output);output.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    result={'case':'test_cancelled_and_exceptional_file_send_release_file_and_lease','case_sha256':digest(CASE),'criterion_changed':False}
    try:
        test_cancelled_and_exceptional_file_send_release_file_and_lease(output)
        result.update(status='passed',fd_check_failed=False)
    except BaseException as error:
        frames=traceback.extract_tb(error.__traceback__);last=frames[-1]
        fd_failure=type(error) is AssertionError and 'proc/self/fd' in (last.line or '')
        result.update(status='failed',fd_check_failed=fd_failure,error_type=type(error).__name__,failure_location={'name':last.name,'line':last.lineno})
        (output/'traceback-private.txt').write_text(traceback.format_exc())
    result['elapsed_seconds']=time.monotonic()-started;save(output/'result.json',result)


def r3d_load(model,ready,stop,receipt):
    import numpy as np
    from packages.encoders.onnx_cpu import load_encoder
    from packages.local_motion.extraction import frozen_encoder_sha
    started=time.monotonic();iterations=0
    try:
        sha=frozen_encoder_sha(digest(model),'synthetic_engineering');encoder=load_encoder(Path(model),sha,512,112,2)
        x=np.random.default_rng(11).random((1,16,3,112,112),dtype=np.float32)
        encoder.encode(x);iterations+=1;Path(ready).touch()
        while not Path(stop).exists() and time.monotonic()-started<180:
            encoder.encode(x);iterations+=1
        save(receipt,{'status':'executed','frozen_encoder_sha256':sha,'encoder_threads':2,'iterations':iterations,'generated_tensor_only':True,'industrial_frames_read':0,'elapsed_seconds':time.monotonic()-started})
    except BaseException as e:
        save(receipt,{'status':'failed','type':type(e).__name__,'generated_tensor_only':True});raise


def run(output,repeats,model=None):
    output=Path(output).resolve()
    if not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Private new artifacts output required')
    output.mkdir(parents=True,exist_ok=False);records=[];load=None
    try:
        for mode in ['idle','r3d']:
            if mode=='r3d':
                if model is None or not Path(model).is_file():
                    records.append({'mode':mode,'status':'blocked','reason':'FROZEN_MODEL_NOT_AVAILABLE','trials_executed':0});continue
                ready=output/'load.ready';stop=output/'load.stop';receipt=output/'load.json'
                load=multiprocessing.get_context('spawn').Process(target=r3d_load,args=(str(model),str(ready),str(stop),str(receipt)))
                load.start();deadline=time.monotonic()+30
                while not ready.exists() and load.is_alive() and time.monotonic()<deadline:time.sleep(.02)
                if not ready.exists():raise RuntimeError('Generated R3D load did not become ready')
            for index in range(repeats):
                dest=output/f'{mode}-{index:02d}'
                try:
                    done=subprocess.run([sys.executable,'-m','scripts.stress_b2u1_media_cancel','--victim',str(dest)],cwd=ROOT,capture_output=True,text=True,timeout=30)
                    (output/f'{mode}-{index:02d}.log').write_text(done.stdout+done.stderr)
                    record=json.loads((dest/'result.json').read_text()) if done.returncode==0 else {'status':'worker_error','returncode':done.returncode}
                except subprocess.TimeoutExpired:record={'status':'timeout','timeout_seconds':30}
                records.append({'mode':mode,'trial':index,**record})
            if load:
                stop.touch();load.join(15)
                if load.is_alive():load.terminate();load.join(5)
                if load.exitcode!=0:raise RuntimeError('R3D load failed or exceeded cleanup bound')
                load=None
    finally:
        if load:
            (output/'load.stop').touch();load.join(10)
            if load.is_alive():load.terminate();load.join(5)
    failed=[r for r in records if r['status']=='failed'];unexpected=[r for r in records if r['status'] in {'worker_error','timeout'}]
    result={'schema':'b2u1-media-limited-stress-v1','case_sha256':digest(CASE),'repeats_per_mode':repeats,'records':records,
        'counts':{name:sum(r['status']==name for r in records) for name in ['passed','failed','blocked','worker_error','timeout']},
        'fd_failures':sum(r.get('fd_check_failed',False) for r in records),'all_executed_trials_passed':not failed and not unexpected,
        'risk_status':'independently_reproduced' if any(r.get('fd_check_failed') for r in failed) else 'not_reproduced_in_bounded_sample_original_risk_unconfirmed',
        'root_cause_established':False,'runtime_changed':False,'diagnosis_completed':not unexpected,'industrial_data_used':False}
    if (output/'load.json').exists():result['r3d_load']=json.loads((output/'load.json').read_text())
    save(output/'results.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);p.add_argument('--repeats',type=int,default=6);p.add_argument('--model',type=Path);p.add_argument('--victim',type=Path);a=p.parse_args()
    if a.victim:victim(a.victim);return
    if not a.output or not 1<=a.repeats<=12:p.error('Output and repeats in [1,12] required')
    r=run(a.output,a.repeats,a.model);print(json.dumps({k:r[k] for k in ['counts','fd_failures','risk_status','diagnosis_completed']}))
    if not r['diagnosis_completed']:raise SystemExit(2)


if __name__=='__main__':main()
