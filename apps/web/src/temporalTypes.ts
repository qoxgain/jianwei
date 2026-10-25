export type TemporalInput={id:string;split:'unassigned'|'train'|'calibration'|'test';source_hash:string;feature_space_id:string;view_id:string;data_origin:string;source_verification:string;eligible_rows:number};
export type Metrics={windows:number;independent_sources:number;source_macro_mse:Record<string,number>;metrics:Record<string,{normalized_feature_mse:number[];original_feature_cosine:number[];anonymous_state_nll:number[];anonymous_state_brier:number[]}>};
export type TemporalReport={stage:string;kind:'train'|'evaluate';data_origin:string;snapshot:{hash:string;source_counts:Partial<Record<'train'|'calibration'|'test',number>>;feature_space_id:string};model?:{selected_model:string;state:string;weights_sha256:string;cluster_version:string};selected_model?:string;calibration?:Metrics;test?:Metrics;train_pairs?:number;training?:{initial_loss:number;epoch_losses:number[];parameter_delta_l2:number};projection?:{dimension:number;explained_variance_ratio:number};next_anonymous_state:{nll:number|null;top1:number|null;observed_transitions:number;censored_windows:number};anonymous_discovery:{segments:unknown[];uniform_segments:unknown[]};semantic_accuracy:null;auto_activated:false;test_read?:boolean;refit_on_test?:boolean};
export type TemporalRun={id:string;scene_id:string;kind:'train'|'evaluate';status:'queued'|'running'|'succeeded'|'blocked'|'failed';created_at:string;updated_at:string;error:{code:string;message:string}|null;report:TemporalReport|null;demo_reference?:boolean};
export type TemporalExample={data_origin:'synthetic_features';train_import_ids:string[];test_import_ids:string[];notice:string};
export function inputEligible(row:TemporalInput){return row.eligible_rows>0&&['verified','synthetic'].includes(row.source_verification)&&row.split!=='unassigned';}
export function partitionSelection(inputs:TemporalInput[],ids:string[],kind:'train'|'evaluate'){
 const rows=ids.map(id=>inputs.find(r=>r.id===id));if(!rows.length||rows.some(r=>!r||!inputEligible(r)))throw new Error('请等待有效导入完成并选择研究数据');
 const present=rows as TemporalInput[],splits=new Set(present.map(r=>r.split));
 if(kind==='train'&&(!splits.has('train')||!splits.has('calibration')||splits.has('test')))throw new Error('训练需要train和calibration，不能包含test');
 if(kind==='evaluate'&&(splits.size!==1||!splits.has('test')))throw new Error('独立评估只接受test');
 if(new Set(present.map(r=>r.source_hash)).size!==present.length)throw new Error('请选择不同原始视频，避免重复来源');
 if(new Set(present.map(r=>`${r.feature_space_id}:${r.view_id}:${r.data_origin}`)).size!==1)throw new Error('特征空间、视角或来源类型不一致');
 return present.map(r=>r.id);
}
