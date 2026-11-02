"""Reproduce byte/media and low-resolution overlap screening without publishing frames."""
import argparse,json,subprocess
from pathlib import Path
import numpy as np
from packages.config.loader import PROJECT_ROOT
from packages.exploratory.experiment import save
from scripts.audit_video_samples import verify_bytes,probe_video

def sample(path):
 r=subprocess.run(['ffmpeg','-v','error','-threads','2','-i',str(path),'-vf','fps=2,scale=32:24','-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True,timeout=600)
 return np.frombuffer(r.stdout,dtype=np.uint8).reshape(-1,768).astype(float)

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 if not args.output.resolve().is_relative_to(PROJECT_ROOT/'artifacts'):p.error('Use an isolated artifacts directory')
 assets=[a for a in json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())['assets'] if a['kind']=='video']
 rows=[]
 for a in assets:
  path=PROJECT_ROOT/a['path'];rows.append({'asset_id':a['asset_id'],'sha256':a['sha256'],'bytes':path.stat().st_size,'integrity':verify_bytes(path,a),'media':probe_video(path,300),'provenance':a['provenance'],'domain':a['domain'],'station_label':a['station_label']})
 save(args.output/'inventory.json',rows)
 pair=[next(a for a in assets if a['asset_id']==name) for name in ('garment_test','garment_test_short')]
 a,b=[sample(PROJECT_ROOT/x['path']) for x in pair]
 an=(a-a.mean(1,keepdims=True))/(a.std(1,keepdims=True)+1e-6);bn=(b-b.mean(1,keepdims=True))/(b.std(1,keepdims=True)+1e-6)
 distance=((an[:,None]-bn[None])**2).mean(-1)
 scores=[float(np.mean(np.diag(distance[offset:offset+len(b)]))) for offset in range(len(a)-len(b)+1)]
 best=int(np.argmin(scores))
 save(args.output/'garment-overlap.json',{'method':'2fps gray32x24 spatially standardized sequence MSE; screening only',
  'long_frames':len(a),'short_frames':len(b),'best_long_offset_seconds':best/2,'best_sequence_normalized_mse':scores[best],
  'per_frame_nearest_mse_mean':float(distance.min(0).mean()),'exact_sample_matches':int((distance.min(0)<1e-12).sum()),
  'independent_acquisition_verified':False,'conclusion':'No confirmed direct match; crop/transcode/retime or unsampled overlaps remain possible. No independence assertion; C not admitted.'})
 if any(r['integrity']['status']!='passed' for r in rows):return 2
 return 0

if __name__=='__main__':raise SystemExit(main())
