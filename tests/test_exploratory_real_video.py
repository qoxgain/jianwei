"""Boundary/counterexample tests only; synthetic fixtures are never effect evidence."""
from copy import deepcopy
from pathlib import Path
import json
from types import SimpleNamespace
import numpy as np
import pytest

from packages.config.loader import load_bundle
from packages.config.errors import ContractError
from packages.exploratory.experiment import partitions,within,load_source,metrics
from packages.temporal.data import Sequence,window_refs
from packages.temporal.models import tcn_initialize,tcn_forward

ROOT=Path(__file__).resolve().parents[1]


def sequence():
    return Sequence({'source_hash':'fabricated_source','discarded_clips':[]},None,[SimpleNamespace(pts_start_ms=i*500,pts_end_ms=i*500+2000,
        split='unassigned',time_domain_id='fixture') for i in range(400)],list(range(400)))


def test_split_precedes_window_build_and_no_raw_interval_or_target_is_shared():
    seq=sequence();before=list(seq.rows);bundle=load_bundle()
    spans=partitions(201500,2000,'B');sets={};bounds={}
    for name,span in spans.items():
        pool=within([seq],span);windows,_=window_refs(pool,bundle.config.learning,bundle.config.temporal)
        assert windows
        sets[name]={r for s in pool for r in s.rows}
        bounds[name]=[(s.batches[0].pts_start_ms,s.batches[-1].pts_end_ms) for s in pool]
        for w in windows:
            s=pool[w.sequence]
            assert span[0]<=s.batches[w.history[0]].pts_start_ms
            assert all(w.issued_ms<s.batches[j].pts_start_ms<s.batches[j].pts_end_ms<=span[1] for j in w.targets)
    assert not sets['train']&sets['holdout'] and not sets['validation']&sets['holdout']
    assert bounds['train'][-1][1]+2000<=bounds['validation'][0][0]
    assert seq.rows==before  # Never rewrite formal metadata or source splits.


@pytest.mark.parametrize('guard',[0,500,1999])
def test_guard_cannot_be_relaxed(guard):
    with pytest.raises(ValueError):partitions(100000,guard,'B')


def test_a_is_explicit_resubstitution_and_short_b_stays_insufficient():
    assert len({tuple(x) for x in partitions(20440,2000,'A').values()})==1
    seq=sequence();bundle=load_bundle();spans=partitions(20440,2000,'B')
    assert any(not window_refs(within([seq],v),bundle.config.learning,bundle.config.temporal)[0] for v in spans.values())


@pytest.mark.parametrize('field,value',[('split','test'),('split','train'),('data_origin','synthetic_features'),('source_hash','f'*64)])
def test_sealed_or_unallowlisted_sources_fail_before_feature_read(tmp_path,monkeypatch,field,value):
    m=json.loads((ROOT/'docs/delivery/p0.3.4/garment_test_short/manifest.json').read_text())
    asset={'sha256':m['source_hash']};m[field]=value
    (tmp_path/'manifest.json').write_text(json.dumps(m))
    monkeypatch.setattr(np,'load',lambda *a,**kw:pytest.fail('Scope failure must precede any feature read'))
    with pytest.raises(ContractError) as e:load_source(tmp_path,asset,load_bundle(),m['feature_space']['encoder_sha'])
    assert e.value.code=='EXPLORATORY_SCOPE'


def test_mse_cosine_are_raw_feature_metrics_and_zero_baseline_is_defined():
    target=np.array([[[1.,2.],[3.,4.]]]);same=metrics(target,target,np.ones(2))
    assert same['feature_mse']==[0.,0.] and np.allclose(same['feature_cosine'],[1,1])
    shifted=metrics(target+1,target,np.ones(2)*2)
    assert shifted['feature_mse']==[1.,1.] and shifted['normalized_feature_mse']==[.25,.25]


def test_tcn_configured_history_beyond_seven_is_not_used_counterexample():
    rng=np.random.default_rng(23);x=rng.normal(size=(2,16,4));parameters=tcn_initialize(4,8,12,23)
    changed=x.copy();changed[:,:-7]+=100
    assert np.array_equal(tcn_forward(x,parameters),tcn_forward(changed,parameters))
