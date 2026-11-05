"""Bounded real execution profiling, not a retrospective split of B.1's 1097s."""
import argparse,json,time,resource
from pathlib import Path
from unittest.mock import patch
from packages.config.loader import load_bundle
from packages.local_motion import extraction as x,motion
from packages.local_motion.analysis import analyze


def profile(config,output):
    bundle=load_bundle(config);measurements={};context={};rois={tuple(r.xyxy):r.id for r in bundle.config.local_motion.rois}
    def timed(name,function):
        def invoke(*args,**kw):
            start=time.perf_counter();cpu=time.process_time()
            try:return function(*args,**kw)
            finally:
                d=measurements.setdefault(name,{'calls':0,'wall_seconds':0.,'process_cpu_seconds':0.});d['calls']+=1;d['wall_seconds']+=time.perf_counter()-start;d['process_cpu_seconds']+=time.process_time()-cpu
        return invoke
    original_load=x.load_encoder;original_tensor=x.clip_tensor
    def load(*args,**kw):
        encoder=timed('encoder_load',original_load)(*args,**kw)
        class ObservedEncoder:
            def encode(self,*a,**k):return timed('r3d:'+context['roi'],encoder.encode)(*a,**k)
        return ObservedEncoder()
    def tensor(frames,indices,roi,size):context['roi']=rois[tuple(roi)];return timed('r3d_preprocessing',original_tensor)(frames,indices,roi,size)
    def io():
        return {k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines()) if k in {'read_bytes','write_bytes'}}
    cpu=time.process_time();wall=time.perf_counter();before_io=io();child=resource.getrusage(resource.RUSAGE_CHILDREN)
    with patch.object(x,'admission',timed('admission',x.admission)),patch.object(x,'geometry_check',timed('probe_geometry',x.geometry_check)),patch.object(x,'decode_video',timed('decode',x.decode_video)),patch.object(x,'load_encoder',load),patch.object(x,'clip_tensor',tensor),patch.object(motion,'gray_resize',timed('motion_preprocessing',motion.gray_resize)),patch.object(motion,'frame_difference',timed('frame_difference',motion.frame_difference)),patch.object(motion,'block_translation',timed('block_motion',motion.block_translation)):
        manifest=x.extract(bundle,output)
    analysis_start=time.perf_counter();analysis=analyze(output);report_seconds=time.perf_counter()-analysis_start
    after_child=resource.getrusage(resource.RUSAGE_CHILDREN);after_io=io()
    result={'schema':'b11-bounded-execution-profile-v1','evidence_class':manifest['evidence_class'],'config_hash':bundle.config_hash,
        'feature_recipe_hash':manifest['recipe_hash'],'profiler_sha256':x.sha(Path(__file__)),'measurement':'actual instrumented single execution; no extrapolation to entire B.1 or realtime',
        'sampled_duration_ms':bundle.config.local_motion.end_ms-bundle.config.local_motion.start_ms,'decoded_frames':manifest['quality']['decoded_frames'],
        'feature_rows':len(manifest['rows']),'r3d_vectors':sum(r['r3d_computed'] for r in manifest['rows']),
        'modules':measurements,'analysis_wall_seconds':report_seconds,'total_wall_seconds':time.perf_counter()-wall,
        'process_cpu_seconds':time.process_time()-cpu,'child_cpu_seconds':after_child.ru_utime+after_child.ru_stime-child.ru_utime-child.ru_stime,
        'peak_process_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'kernel_io_bytes':{k:after_io[k]-before_io[k] for k in before_io},'historical_full_run_breakdown':'not_recorded_cannot_reconstruct',
        'centered_smoothing_is_offline':True,'b2_started':False,'p04_admitted':False}
    x.write(Path(output)/'profile.json',result);x.write(Path(output)/'profile-analysis.json',analysis);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=profile(a.config,a.output);print(json.dumps(r))


if __name__=='__main__':main()
