"""Draft future collection slots; never certify captures or inspect sealed video."""
import argparse
from pathlib import Path
import yaml
from packages.config.loader import load_bundle
from packages.config.yaml import parse_yaml
from packages.contracts.dataset import DatasetManifest


def draft(bundle,view):
    settings=bundle.config.temporal;protocol=settings.research_protocol
    counts={'train':settings.min_train_sources,'calibration':settings.min_calibration_sources,
            'test':max(settings.min_test_sources,protocol.min_independent_sessions if protocol else 2)}
    slots=[]
    for split,count in counts.items():
        for index in range(count):
            slots.append({'planned_slot':f'{split}_{index+1}','planned_split':split,
                'physical_camera_id':None,'stable_view_id':view,'capture_session_id':None,
                'captured_at_with_timezone':None,'date':None,'shift':None,'operator_pseudonym':None,
                'operation_instance_notes':None,'original_sha256':None,'root_sha256':None,
                'parent_sha256':None,'relation':None,'reviewed_view_intervals':[],
                'provenance_review':{'status':'pending'},'near_duplicate_review':{'status':'pending'}})
    return {'stage':'P0.3.6','draft_only':True,'formal_admission':False,'slots_are_not_an_approved_dataset_manifest':True,
        'registered_protocol_id':protocol.protocol_id if protocol else None,
        'protocol_still_required':protocol is None,'minimum_slots_only_not_statistical_sufficiency':counts,
        'existing_nine_development_videos_are_not_new_sealed_data':True,'slots':slots,
        'collection_guidance':['同一固定机位与稳定视角分别研究，原片保留完整采集会话，不靠切片制造独立样本。',
            '增加不同日期、班次、操作人员与重复动作实例；记录混杂因素，不能用文件数代替独立会话。',
            '采集前规划互斥的训练、校准、封存会话；封存录像不用于选择采样、阈值或模型。',
            '核对根/派生谱系和近重复关系后由有依据的负责人审核，未知字段保持空值。',
            '软件最小数量不保证统计充分性；根据研究问题规划更多独立会话。',
            '操作人员只记录必要匿名编码；规范、视频观测、预测和仪器结论分别留证。']}


def review_registry(manifest):
    registry=DatasetManifest.model_validate(manifest)
    if len(registry.sources)>100:raise ValueError('Review one bounded view/study with at most100 sources')
    pairs=[];reviews={frozenset((r.left_sha256,r.right_sha256)):r for r in registry.duplicate_reviews}
    for i,left in enumerate(registry.sources):
        for right in registry.sources[i+1:]:
            same_root=left.root_source_sha256==right.root_source_sha256
            same_session=left.capture_session_id is not None and left.capture_session_id==right.capture_session_id
            review=reviews.get(frozenset((left.source_sha256,right.source_sha256)))
            conflict=left.camera_id is not None and left.camera_id==right.camera_id and left.captured_at is not None and left.captured_at==right.captured_at and not same_session
            pairs.append({'left_sha256':left.source_sha256,'right_sha256':right.source_sha256,
                'same_root':same_root,'same_session':same_session,'same_view':left.view_id==right.view_id,
                'review_resolution':review.resolution if review else 'not_documented',
                'independence_inferred_from_different_hashes':False,
                'capture_time_identity_needs_review':conflict,
                'attention':'same_lineage_or_session' if same_root or same_session else 'capture_time_identity_conflict' if conflict else 'human_review_required' if not review else 'recorded_review_only'})
    return {'metadata_only':True,'pixels_or_feature_values_read':False,'formal_admission':False,
        'dataset_id':registry.dataset_id,'pairs':pairs,'automatic_independence_approval':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('config/system.yaml'))
    parser.add_argument('--view',default='pending_stable_view')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--review-manifest',type=Path)
    args=parser.parse_args()
    bundle=load_bundle(args.config)
    if args.review_manifest:
        with args.review_manifest.open() as source:raw=source.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:parser.error('Manifest metadata exceeds 8 MiB')
        data=review_registry(parse_yaml(raw))
    else:data=draft(bundle,args.view)
    protected=[bundle.path,*[bundle.root/s.knowledge_file for s in bundle.scenes.values()]]
    if args.review_manifest:protected.append(args.review_manifest)
    if args.output.resolve() in [p.resolve() for p in protected]:parser.error('Cannot overwrite input/configuration/knowledge')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:yaml.safe_dump(data,out,allow_unicode=True,sort_keys=False)


if __name__=='__main__':main()
