"""Metadata/source-byte admission and optional PRIVATE technical preview only.

Different files do not prove independent captures. No action labels are made.
"""
import argparse,copy,json,subprocess
from pathlib import Path
from types import SimpleNamespace
from packages.config.loader import PROJECT_ROOT,load_bundle
from packages.local_motion.extraction import admission,write,sha
from packages.ingestion.video import video_geometry,geometry_filter
from scripts.audit_video_samples import probe_video


def admitted_bundle(base,asset):
    current=load_bundle(PROJECT_ROOT/'config/system.yaml')
    source=base.profile.sources[0].model_copy(update={'id':asset['asset_id'],'uri':str(PROJECT_ROOT/asset['path'])})
    config=base.config.model_copy(update={'local_motion':base.config.local_motion.model_copy(update={'source_id':asset['asset_id'],'source_sha256':asset['sha256'],'evidence_class':'development_exploration'}),'temporal':current.config.temporal})
    return SimpleNamespace(root=base.root,config=config,profile=base.profile.model_copy(update={'sources':[source]}))


def screen(path,media,dest,begin):
    stream={'width':media['coded_width'],'height':media['coded_height'],'sample_aspect_ratio':media['sample_aspect_ratio'] or '1:1','side_data_list':[{'rotation':media['rotation_degrees']}]}
    geometry=video_geometry(stream,640)
    filters=geometry_filter(geometry)+',fps=1,scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2,tile=4x3'
    subprocess.run(['ffmpeg','-v','error','-protocol_whitelist','file,pipe','-threads','1','-ss',str(begin),'-noautorotate','-i',str(path),'-t','12','-an','-sn','-dn','-vf',filters,'-frames:v','1','-threads','1',str(dest)],check=True,capture_output=True,timeout=60)
    return {'path':str(dest.relative_to(PROJECT_ROOT)),'sha256':sha(dest),'approx_start_seconds':begin,'duration_seconds':12,'sampling':'fps=1 representative preview, not original-frame action truth'}


def audit(catalog,base,output,previews=False,budget=None):
    output=Path(output).resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private new output required')
    output.mkdir(parents=True,exist_ok=False);records=[]
    for asset in catalog['assets']:
        if asset['kind']!='video':continue
        r={'asset_id':asset['asset_id'],'registered_station_label':asset.get('station_label'),'provenance':asset['provenance'],
            'metadata_creation_hint_is_capture_truth':False,'independence':'unverified','near_duplicate_review':'pending','action_boundaries_created':0}
        r['capture_metadata_missing']=[k for k in ['capture_session_id','camera_id','view_id','captured_at'] if not asset['provenance'].get(k)]
        try:
            # Existing sealed-root and registered-development guard BEFORE content.
            bundle=admitted_bundle(base,asset)
            if budget is not None:bundle.config=bundle.config.model_copy(update={'ingestion':budget.config.ingestion})
            path=admission(bundle);r.update(status='development_bytes_verified',source_sha256=asset['sha256'],bytes=path.stat().st_size)
            media=probe_video(path,30);r['technical_media']=media
            r['creation_time_hint_private']=asset.get('media',{}).get('creation_time_hint')
            r['capture_metadata_missing']=[k for k in ['capture_session_id','camera_id','view_id','captured_at'] if not asset['provenance'].get(k)]
            if previews:
                mid=max(0,min(media['duration_seconds']/2-6,media['duration_seconds']-12));early=min(8,max(0,media['duration_seconds']-12))
                r['private_previews']=[screen(path,media,output/(asset['asset_id']+'-'+name+'.png'),start) for name,start in [('early',early),('middle',mid)]]
            r['independent_transfer_allowed']=False
            # No independence is inferred even if all optional technical hints exist.
        except Exception as error:r.update(status='blocked_or_failed',error_code=getattr(error,'code',type(error).__name__),independent_transfer_allowed=False)
        records.append(r)
    groups={}
    for r in records:
        if 'source_sha256' in r:groups.setdefault(r['source_sha256'],[]).append(r['asset_id'])
    private={'schema':'b2u1-source-suitability-private-v1','records':records,'byte_duplicate_groups':[v for v in groups.values() if len(v)>1],
        'derivative_or_near_duplicate_relations':'unresolved; neither differing SHA nor sparse imagery proves independence','formal_test_data_consumed':False,'new_field_transfer_executed':False,'human_action_labels_required':False}
    write(output/'sources-private.json',private)
    summary={'schema':'b2u1-source-audit-aggregate-v1','catalog_videos':len(records),'development_bytes_verified':sum(r['status']=='development_bytes_verified' for r in records),
        'blocked_or_failed':sum(r['status']=='blocked_or_failed' for r in records),'unique_byte_identities':len(groups),'exact_byte_duplicate_groups':len(private['byte_duplicate_groups']),
        'sources_missing_capture_metadata':sum(bool(r.get('capture_metadata_missing')) for r in records),'verified_independent_pairs':0,
        'cross_session_transfer':'blocked_pending_capture_lineage_and_fixed_view_evidence','preview_scope':'private sparse technical screening, not action ground truth',
        'independence_not_inferred_from_filename_sha_or_creation_hint':True,'source_identifiers_locations_times_or_frames_included':False,'formal_test_data_consumed':False}
    write(output/'sources-aggregate.json',summary);return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--previews',action='store_true');p.add_argument('--ingestion-budget-config',type=Path);a=p.parse_args()
    c=json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text());budget=load_bundle(a.ingestion_budget_config) if a.ingestion_budget_config else None
    print(json.dumps(audit(c,load_bundle(a.config),a.output,a.previews,budget)))


if __name__=='__main__':main()
