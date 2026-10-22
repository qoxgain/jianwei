"""Append normalized projections without touching immutable legacy rows."""
import json
import yaml
from sqlalchemy import text
from packages.contracts.models import Knowledge
from packages.migrations.legacy_v3 import adapt_knowledge
from packages.knowledge.service import content_hash

def record_legacy_mappings(connection):
    rows = connection.execute(text('SELECT id, content_hash, data_json FROM knowledge_versions WHERE id NOT IN (SELECT version_id FROM legacy_knowledge_mappings)')).mappings()
    for row in rows:
        raw = json.loads(row['data_json'])
        if raw.get('schema_version') != '3.0': continue
        normalized, mapping = adapt_knowledge(Knowledge.model_validate(raw))
        connection.execute(text('''INSERT INTO legacy_knowledge_mappings
            (version_id, old_hash, normalized_hash, normalized_json, normalized_yaml, node_map_json)
            VALUES (:id, :old, :new, :data, :yaml, :nodes)'''), {
                'id': row['id'], 'old': row['content_hash'], 'new': content_hash(normalized.model_dump()),
                'data': json.dumps(normalized.model_dump(), ensure_ascii=False, sort_keys=True),
                'yaml': yaml.safe_dump(normalized.model_dump(), allow_unicode=True, sort_keys=False),
                'nodes': json.dumps(mapping, ensure_ascii=False, sort_keys=True)})
