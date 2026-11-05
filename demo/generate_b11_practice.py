"""Standalone synthetic annotation practice: Python + FFmpeg, no model weights.

Generates rectangles, never reads industrial media. Output stays on your device.
The source descriptor uses only integers/ASCII, so sorted compact JSON is also
RFC8785 canonical for this specific payload (not a general JCS implementation).
"""
import argparse,hashlib,json,subprocess
from pathlib import Path


def generate(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);video=output/'generated.mp4'
    command=['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','160x120','-r','16','-i','pipe:0',
        '-an','-c:v','libx264','-threads','1','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-y',str(video)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for i in range(192):
            image=bytearray([100])*(160*120*3);active=2<=i/16<5 or 7<=i/16<10;x=20+(i%32 if active else 8)
            for y in range(32,62):image[(y*160+x)*3:(y*160+x+20)*3]=bytes([220,55,30])*20
            for y in range(34,60,4):
                for col in range(x,x+20,4):image[(y*160+col)*3:(y*160+col+1)*3]=bytes([240])*3
            if 5.5<=i/16<6.5:
                for y in range(120):image[(y*160+120)*3:(y*160+160)*3]=bytes([60+(i%4)*35])*120
            process.stdin.write(image)
        process.stdin.close();error=process.stderr.read()
        if process.wait(30):raise RuntimeError(error.decode()[:2000])
    finally:
        if process.poll() is None:process.kill();process.wait()
    probe=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time,duration_time,pkt_duration_time','-of','json',str(video)],capture_output=True,check=True,timeout=30)
    raw=json.loads(probe.stdout)['frames'];starts=[round(float(f['best_effort_timestamp_time'])*1000) for f in raw];frames=[]
    if len(raw)!=192:raise ValueError('Generated frame count mismatch')
    for i,f in enumerate(raw):
        duration=float(f.get('duration_time',f.get('pkt_duration_time','0')))
        if duration<=0:raise ValueError('Frame duration required, not guessed')
        end=round((float(f['best_effort_timestamp_time'])+duration)*1000)
        if i+1<len(raw) and abs(end-starts[i+1])>1:raise ValueError('Unexpected generated PTS gap')
        frames.append({'index':i,'pts_ms':starts[i],'end_ms':starts[i+1] if i+1<len(raw) else end})
    source={'schema':'b11-annotation-source-v1','evidence_class':'synthetic_engineering','source':{'asset_id':'b11_practice_generated',
        'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'bytes':video.stat().st_size,'width':160,'height':120,'first_pts_ms':frames[0]['pts_ms'],'last_pts_end_ms':frames[-1]['end_ms']},
        'frames':frames,'annotation_protocol':'generated rectangle practice, no industrial human truth or algorithm predictions',
        'clock_mapping':'actual ffprobe original PTS; browser_currentTime_plus_first_pts'}
    source['descriptor_hash']=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    (output/'source.json').write_text(json.dumps(source,indent=2)+'\n')
    print(json.dumps({'status':'synthetic_practice_only','video':str(video),'source':str(output/'source.json'),'human_industrial_f1':None}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('b11-practice'));a=p.parse_args();generate(a.output)
