export type Action = {
  id: string; name: string; aliases: string[]; description: string;
  status: 'draft' | 'approved'; risk_level: 'general' | 'critical';
  observable_cues: string[]; negative_cues: string[]; confusable_with: string[];
  completion_rule: { name: 'visible_motion_over_interval' | 'manual_review_only'; min_visible_seconds: number; require_object_contact: boolean };
  duration_prior_seconds: number[] | null;
  positive_examples: string[]; negative_examples: string[]; prototype_refs: string[];
};
export type Edge = { from_action: string; to_action: string };
export type Knowledge = {
  schema_version: '3.0'; knowledge_version: string; scene_id: string;
  approval_status: 'draft' | 'approved'; approved_by: string | null;
  actions: Action[];
  sop: { id: string; version: string; ordering: 'strict' | 'partial' | 'unordered'; nodes: { action_id: string; required: boolean; min_count: number; max_count: number; role: string; group: string }[]; edges: Edge[]; repeatable_edges: Edge[] };
  curated_memory: { op: string; target_id: string; action_id: string | null; related_action_id: string | null; reason: string }[];
};
export type Diff = { added: string[]; removed: string[]; changed: { id: string; fields: string[] }[]; sop_changed: boolean; metadata_changed: string[] };
export type Preview = { valid: boolean; errors: { path: string; message: string }[]; hash: string | null; diff: Diff | null; impact: string[]; data: Knowledge | null };
export type Version = { id: string; hash: string; version: string; status: 'baseline' | 'candidate'; base_hash: string | null; created_at: string; diff: Diff; yaml?: string };
export type Profile = { id: string; name: string; mode: string; static_valid: boolean; runtime_ready: boolean; checks: { code: string; status: 'passed' | 'blocked' | 'not_run'; message: string }[]; blockers: { code: string; message: string }[]; config_hash?: string; checked_at?: string };
export type Bootstrap = {
  project: { name: string; version: string; stage: string; title: string };
  active_profile: string; config_hash: string; profiles: Profile[];
  knowledge: { yaml: string; hash: string; version: string; data: Knowledge };
  versions: Version[]; capabilities: Record<string, boolean>; blockers: { code: string; message: string }[];
};
