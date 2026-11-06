"""Generate only synthetic practice footage; no project/model dependencies.
Requires Python, numpy and FFmpeg. Codec versions may change the source SHA.
Never rebind an old report to different bytes; rerun the project pipeline then.
"""
import argparse,hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')

def generated_video(path,seed):
    width,height,fps,count=160,120,16,1024;rng=np.random.default_rng(seed)
    base=np.clip(90+rng.integers(-8,9,(height,width,3)),0,255).astype(np.uint8)
    command=['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r',str(fps),'-i','pipe:0',
        '-an','-c:v','libx264','-threads','1','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-y',str(path)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for index in range(count):
            phase=index%128;image=base.copy()
            if phase<32:x=24
            elif phase<64:x=24+(phase-32)
            elif phase<96:x=56-(phase-64)
            else:x=24
            y=32 if (index//128)%2==0 else 48
            image[y:y+25,x:x+20]=[215+(seed%7),55,30];image[y+2:y+23:4,x:x+20:4]=240
            if index%256>=192:image[:,120:]=50+(index%4)*25
            process.stdin.write(image.tobytes())
        process.stdin.close();process.stdin=None;_,error=process.communicate(timeout=30)
        if process.returncode:raise RuntimeError(error.decode()[:1000])
    finally:
        if process.poll() is None:process.kill();process.wait()
    record={'kind':'b2u-generated-motifs-v1','sha256':sha(path),'seed':seed,'frames':count,'fps':fps,'width':width,'height':height,
        'evidence_class':'synthetic_engineering','input_source_frames_read':0,'generator_sha256':sha(Path(__file__)),
        'generated_at':datetime.now(timezone.utc).isoformat(),'definition':'fresh seeded raster source; shared planted dynamics, not independent field footage'}
    write(path.with_suffix('.generated.json'),record);return record

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);record=generated_video(a.output/'generated.mp4',101)
    reference=Path(__file__).with_name('synthetic-study.json')
    if not reference.exists():reference=Path(__file__).resolve().parents[1]/'apps/web/src/data/unlabeledDemo.json'
    expected=json.loads(reference.read_text())['source']['sha256'] if reference.exists() else None
    print(json.dumps({'evidence_class':'synthetic_engineering','sha256':record['sha256'],'matches_shipped_report':record['sha256']==expected,
        'next_step':'Associate generated.mp4 in the zero-label lab if SHA matches; otherwise rerun the project synthetic pipeline and import its new report. Never alter the source hash in a report.'}))

if __name__=='__main__':main()
