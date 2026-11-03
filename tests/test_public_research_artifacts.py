import json,zipfile
from pathlib import Path
import pytest
from scripts.check_public_research_artifacts import inspect_file,inspect_json


def test_feature_or_weight_arrays_are_not_scalar_statistics(tmp_path):
    path=tmp_path/'private.npz';path.write_bytes(b'fixture')
    with pytest.raises(ValueError):inspect_file(path)
    with pytest.raises(ValueError):inspect_json({'vectors':[[float(i) for i in range(512)]]})
    inspect_json({'feature_mse':[.1,.2,.3],'train_raw_feature_mse':[.2]*101})


def test_demo_archive_cannot_hide_a_numerical_package(tmp_path):
    path=tmp_path/'demo.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('nested/weights.npz',b'fixture')
    with pytest.raises(ValueError):inspect_file(path)


def test_display_sampling_keeps_real_endpoints_without_mutating_full_precision_reports():
    from scripts.publish_p036_statistics import display_traces
    original=[{'issued_ms':i*500,'prediction_mse':{'ridge':[i/7]}} for i in range(80)]
    before=json.dumps(original)
    displayed=display_traces(original)
    assert len(displayed)==20
    assert displayed[0]['issued_ms']==0 and displayed[-1]['issued_ms']==39500
    assert all(p['issued_ms'] in {v['issued_ms'] for v in original} for p in displayed)
    displayed[0]['prediction_mse']['ridge'][0]=42
    assert json.dumps(original)==before
    assert display_traces([])==[]
