from copy import deepcopy
import numpy as np
from packages.config.loader import load_bundle
from scripts.plan_independent_collection import draft,review_registry
from tests.test_dataset_provenance import dataset,source


def test_draft_keeps_unknown_capture_evidence_unknown_and_unapproved():
    plan=draft(load_bundle(),'proposed_view')
    assert plan['draft_only'] and not plan['formal_admission'] and plan['protocol_still_required']
    assert len(plan['slots'])==5
    for slot in plan['slots']:
        assert slot['original_sha256'] is None and slot['capture_session_id'] is None
        assert slot['captured_at_with_timezone'] is None and slot['provenance_review']['status']=='pending'


def test_metadata_review_never_reads_test_or_approves_different_hashes(monkeypatch):
    monkeypatch.setattr(np,'load',lambda *a,**k:(_ for _ in ()).throw(AssertionError('read vectors')))
    raw=dataset();report=review_registry(raw)
    assert not report['pixels_or_feature_values_read'] and not report['formal_admission']
    assert all(not p['independence_inferred_from_different_hashes'] for p in report['pairs'])
    assert all(p['attention']=='human_review_required' for p in report['pairs'])


def test_transcode_is_still_one_session_not_a_new_independent_source():
    original=source(1);child=deepcopy(original)
    child.update(source_sha256='f'*64,parent_source_sha256=original['source_sha256'],relation='transcode')
    report=review_registry(dataset([original,child]))
    assert report['pairs'][0]['same_root'] and report['pairs'][0]['same_session']
    assert report['pairs'][0]['attention']=='same_lineage_or_session'


def test_same_camera_same_timestamp_with_different_session_ids_needs_review():
    a,b=source(1),source(2);b['captured_at']=a['captured_at']
    pair=review_registry(dataset([a,b]))['pairs'][0]
    assert pair['capture_time_identity_needs_review'] and pair['attention']=='capture_time_identity_conflict'
    assert not pair['independence_inferred_from_different_hashes']
