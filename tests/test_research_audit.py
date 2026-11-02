"""Recorded development metadata stays separate from real study evidence."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.config.loader import load_bundle
from packages.contracts.dataset import DatasetManifest
from packages.temporal import engine
from scripts import audit_research_protocol as audit_module
from tests.test_dataset_provenance import source, dataset


ROOT = Path(__file__).resolve().parents[1]
SCENE = 'anonymous_observation'
HISTORICAL = ROOT/'docs/delivery/p0.3.3'
DENSE = ROOT/'docs/delivery/p0.3.4/garment_test_short/manifest.json'


def write_manifest(tmp_path, manifest, name='manifest.json'):
    path = tmp_path/name
    path.write_text(json.dumps(manifest))
    return path


def recorded():
    return json.loads(DENSE.read_text())


def test_nine_sources_ten_versions_remain_blocked_without_feature_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('metadata audit read vectors or trained')
    monkeypatch.setattr(np, 'load', forbidden)
    monkeypatch.setattr(engine, 'train', forbidden)
    paths = [*sorted(HISTORICAL.glob('*/manifest.json')), DENSE]
    originals = [p.read_bytes() for p in paths]
    bundle = load_bundle()
    result = audit_module.audit(bundle, SCENE, paths, sampling=True)
    assert result['input_manifest_count'] == 10
    assert result['feature_version_count'] == 10
    assert result['unique_source_count'] == 9
    assert result['reviewed_capture_session_count'] == 0
    assert result['status'] == 'blocked'
    assert result['research_effect'] == 'not_evaluated'
    assert not result['training_admission_checked'] and not result['training_executed']
    assert not result['feature_values_read'] and not result['p04_admitted']
    versions = [v for group in result['sources'] for v in group['versions']]
    assert sorted(v['sampling']['causal_windows'] for v in versions) == [0]*9+[16]
    dense = next(v for v in versions if v['sampling']['causal_windows'] == 16)
    assert dense['feature_space']['encoder_sha'] == '2979019509005056778db9f462e9eed212c40f02d5e5bf1d6fc9e06caf2ebab8'
    assert {'capture_identity.physical_camera_id', 'capture_identity.stable_view_id',
            'acquisition.capture_session_id', 'acquisition.captured_at', 'split'} <= set(dense['missing_fields'])
    assert [p.read_bytes() for p in paths] == originals
    assert audit_module.audit(bundle, SCENE, reversed(paths), sampling=True) == result


def test_repeated_manifest_is_not_counted_as_another_version():
    result = audit_module.audit(load_bundle(), SCENE, [DENSE, DENSE])
    assert result['input_manifest_count'] == 2
    assert result['unique_source_count'] == result['feature_version_count'] == 1
    version = result['sources'][0]['versions'][0]
    assert version['sampling'] is None and version['sampling_status'] == 'not_requested'


@pytest.mark.parametrize('sealed_in', ['same_manifest', 'other_version', 'registry'])
def test_sealed_source_never_runs_sampling_diagnostics(tmp_path, monkeypatch, sealed_in):
    manifest = recorded()
    # Malformed time metadata makes accidental diagnostic calls fail visibly.
    manifest['rows'] = manifest['feature_batches'] = manifest['episodes'] = 'sealed-uninspected'
    bundle = load_bundle()
    paths = []
    if sealed_in == 'same_manifest':
        manifest['split'] = 'test'
    elif sealed_in == 'other_version':
        second = deepcopy(manifest)
        second['split'] = 'test'
        paths.append(write_manifest(tmp_path, second, 'sealed.json'))
    else:
        item = source(4, 'test')
        item['source_sha256'] = item['root_source_sha256'] = manifest['source_hash']
        bundle.config.temporal.dataset_manifest = DatasetManifest.model_validate(dataset([item]))
    paths.append(write_manifest(tmp_path, manifest))
    monkeypatch.setattr(audit_module, 'analyze', lambda *args: pytest.fail('test metadata reached sampler'))
    result = audit_module.audit(bundle, SCENE, paths, sampling=True)
    assert all(v['sampling'] is None and v['sampling_status'] == 'sealed_test_not_inspected'
               for group in result['sources'] for v in group['versions'])
    assert not result['test_sampling_diagnostics_executed']


@pytest.mark.parametrize('key,value', [('tenant_id','different_tenant'),('scene_id','workshop_safety')])
def test_scope_mismatch_is_rejected_before_output(tmp_path, key, value):
    manifest = recorded(); manifest[key] = value
    with pytest.raises(ContractError) as error:
        audit_module.audit(load_bundle(), SCENE, [write_manifest(tmp_path, manifest)])
    assert error.value.code == 'TEMPORAL_SCOPE_MISMATCH'


@pytest.mark.parametrize('wire', ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
                                 '{"split":"test","split":"unassigned"}', '[]'])
def test_ambiguous_or_nonfinite_json_is_rejected(tmp_path, wire):
    path = tmp_path/'invalid.json';path.write_text(wire)
    with pytest.raises(ContractError) as error:
        audit_module.audit(load_bundle(), SCENE, [path])
    assert error.value.code == 'RESEARCH_AUDIT_MANIFEST'


def test_metadata_budget_bounds_the_actual_read(tmp_path):
    path = tmp_path/'large.json'
    with path.open('wb') as stream:
        stream.truncate(audit_module.MAX_MANIFEST_BYTES+1)
    with pytest.raises(ContractError) as error:
        audit_module.audit(load_bundle(), SCENE, [path])
    assert error.value.code == 'RESEARCH_AUDIT_BUDGET'


def test_output_preserves_inputs_knowledge_and_different_prior_evidence(tmp_path):
    bundle = load_bundle()
    result = audit_module.audit(bundle, SCENE, [DENSE])
    for output in [bundle.path, DENSE, ROOT/'knowledge/scenes/anonymous_observation.yaml']:
        original = output.read_bytes()
        with pytest.raises(ContractError) as error:
            audit_module.write_report(bundle, [DENSE], output, result)
        assert error.value.code == 'RESEARCH_AUDIT_OVERWRITE'
        assert output.read_bytes() == original
    alias = tmp_path/'knowledge-alias.json'
    alias.symlink_to(ROOT/'knowledge/scenes/anonymous_observation.yaml')
    with pytest.raises(ContractError):
        audit_module.write_report(bundle, [DENSE], alias, result)
    output = tmp_path/'report.json'
    audit_module.write_report(bundle, [DENSE], output, result)
    audit_module.write_report(bundle, [DENSE], output, result)
    original = output.read_bytes()
    with pytest.raises(ContractError):
        audit_module.write_report(bundle, [DENSE], output, {**result, 'status': 'invented'})
    assert output.read_bytes() == original


def test_cli_returns_blocked_with_a_reviewable_report(tmp_path):
    output = tmp_path/'audit.json'
    assert audit_module.main(['--manifest', str(DENSE), '--output', str(output)]) == 2
    result = json.loads(output.read_text())
    assert result['status'] == 'blocked' and result['unique_source_count'] == 1
    assert result['protocol_preview']['status'] == 'blocked'
