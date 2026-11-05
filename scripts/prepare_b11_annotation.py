"""Produce a private source-only original-PTS packet; never includes predictions."""
import argparse,json
from pathlib import Path
from packages.config.loader import load_bundle
from packages.local_motion.extraction import admission,geometry_check,sha
from packages.ingestion.video import _run
from packages.knowledge.service import content_hash
from packages.local_motion.annotation import validate_source


def prepare(config,output):
    bundle=load_bundle(config);path=admission(bundle);s=bundle.config.local_motion;geometry_check(path,bundle.config.ingestion,s)
    raw=json.loads(_run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-select_streams','v:0','-show_frames',
        '-show_entries','frame=best_effort_timestamp_time,pkt_duration_time,duration_time','-of','json',str(path)],bundle.config.ingestion.probe_timeout_seconds))['frames']
    starts=[round(float(f['best_effort_timestamp_time'])*1000) for f in raw]
    last_duration=round(float(raw[-1].get('duration_time',raw[-1].get('pkt_duration_time','0')))*1000)
    if last_duration<=0:raise ValueError('Last frame duration unavailable; do not guess frame timing')
    frames=[{'index':i,'pts_ms':t,'end_ms':starts[i+1] if i+1<len(starts) else t+last_duration} for i,t in enumerate(starts)]
    result={'schema':'b11-annotation-source-v1','evidence_class':s.evidence_class,
        'source':{'asset_id':s.source_id,'sha256':s.source_sha256,'bytes':path.stat().st_size,'width':s.display_width,'height':s.display_height,
            'first_pts_ms':starts[0],'last_pts_end_ms':frames[-1]['end_ms']},'frames':frames,
        'annotation_protocol':'source-only full PTS; no algorithm, candidate, response or run included',
        'clock_mapping':'browser_currentTime_plus_first_pts; verify observed mediaTime; gaps/nonmonotonic PTS rejected'}
    result['descriptor_hash']=content_hash(result);validate_source(result)
    if sha(path)!=s.source_sha256:raise ValueError('Source changed during PTS indexing')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--draft-output',type=Path);a=p.parse_args()
    r=prepare(a.config,a.output);print(json.dumps({'status':'prepared_no_human_labels','frames':len(r['frames']),'descriptor_hash':r['descriptor_hash']}))
    if a.draft_output:
        from packages.local_motion.annotation import new_document
        with a.draft_output.open('x') as stream:stream.write(json.dumps(new_document(r),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
