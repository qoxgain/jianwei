-- SQLite reference schema only. Production uses tenant-scoped ORM migrations.
-- These are richer contract tables, not the single-process demo table names.
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS knowledge_versions (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, scene_id TEXT NOT NULL,
  content_hash TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, approved_by TEXT,
  UNIQUE(tenant_id,scene_id,content_hash)
);
CREATE TABLE IF NOT EXISTS evidence_items (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, episode_id TEXT NOT NULL,
  source_hash TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  uri TEXT NOT NULL, quality REAL NOT NULL, retention_until TEXT,
  CHECK(start_ms>=0 AND end_ms>start_ms), CHECK(quality>=0 AND quality<=1)
);
CREATE TABLE IF NOT EXISTS observation_revisions (
  id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
  episode_id TEXT NOT NULL, action_id TEXT, quality_tier TEXT NOT NULL,
  evidence_id TEXT NOT NULL REFERENCES evidence_items(id), supersedes_id TEXT,
  knowledge_version TEXT NOT NULL REFERENCES knowledge_versions(id),
  source TEXT NOT NULL, available_at TEXT NOT NULL, reason TEXT NOT NULL,
  CHECK(quality_tier IN ('U','C','W','G'))
);
CREATE TABLE IF NOT EXISTS forecast_records (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, episode_id TEXT NOT NULL,
  input_max_pts_ms INTEGER NOT NULL, issued_at_pts_ms INTEGER NOT NULL,
  target_start_pts_ms INTEGER NOT NULL,target_end_pts_ms INTEGER NOT NULL,
  probability_json TEXT NOT NULL, model_version TEXT NOT NULL,
  memory_version TEXT NOT NULL,knowledge_version TEXT NOT NULL,
  data_origin TEXT NOT NULL,created_at TEXT NOT NULL,
  CHECK(input_max_pts_ms<=issued_at_pts_ms),
  CHECK(issued_at_pts_ms<=target_start_pts_ms),CHECK(target_end_pts_ms>target_start_pts_ms)
);
CREATE TABLE IF NOT EXISTS forecast_outcomes (
  id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL REFERENCES forecast_records(id),
  observation_revision_id TEXT NOT NULL REFERENCES observation_revisions(id),
  valid_for_scoring INTEGER NOT NULL,reason TEXT NOT NULL,metric_json TEXT,
  UNIQUE(forecast_id,observation_revision_id)
);
CREATE TABLE IF NOT EXISTS memory_versions (
  id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,scene_id TEXT NOT NULL,
  feature_space_id TEXT NOT NULL,knowledge_version TEXT NOT NULL,
  sample_manifest_hash TEXT NOT NULL,parent_id TEXT,status TEXT NOT NULL,
  artifact_uri TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_registry (
  id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,family TEXT NOT NULL,
  weights_hash TEXT NOT NULL,config_hash TEXT NOT NULL,dataset_hash TEXT NOT NULL,
  feature_space_id TEXT NOT NULL,parent_id TEXT,status TEXT NOT NULL,artifact_uri TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_runs (
  id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES model_registry(id),
  champion_id TEXT NOT NULL REFERENCES model_registry(id),dataset_hash TEXT NOT NULL,
  protocol_hash TEXT NOT NULL,metrics_uri TEXT NOT NULL,decision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_records (
  id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,scope TEXT NOT NULL,
  version_tuple_json TEXT NOT NULL,state TEXT NOT NULL,eval_id TEXT REFERENCES evaluation_runs(id),
  expected_previous_release TEXT,rollback_to TEXT,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,actor TEXT NOT NULL,
  action TEXT NOT NULL,target_id TEXT NOT NULL,before_hash TEXT,after_hash TEXT,
  trace_id TEXT NOT NULL,created_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,
  UNIQUE(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS evidence_episode_idx ON evidence_items(tenant_id,episode_id,start_ms);
CREATE INDEX IF NOT EXISTS forecasts_episode_idx ON forecast_records(tenant_id,episode_id,issued_at_pts_ms);
