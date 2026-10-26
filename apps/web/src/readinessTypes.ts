export type ReadinessIssue={code:string;message:string};
export type ReadinessGate={status:'blocked'|'metadata_ready';import_ids:string[];snapshot_hash:string|null;blockers:ReadinessIssue[]};
export type ReadinessReport={stage:string;scene_id:string;metadata_only:boolean;feature_files_opened:boolean;test_values_read:boolean;training_executed:boolean;jobs_created:boolean;report_hash:string;config_hash:string;dataset_manifest_hash:string|null;can_submit_train:boolean;can_submit_evaluate:boolean;note:string;
 partitions:Record<string,{imports:number;source_files:number;reviewed_independent_groups:number;complete_episodes:number;eligible_rows:number;causal_windows:number;sources_with_causal_windows:number;minimum_sources:number|null}>;
 records:{import_id:string;source_hash:string|null;split:string;data_origin:string|null;missing_records:string[];issues:ReadinessIssue[]}[];
 train:ReadinessGate;evaluation:ReadinessGate};
export type DatasetDraft={draft_only:boolean;configuration_written:boolean;manifest:unknown;lineage_needs_confirmation:string[];notice:string};
