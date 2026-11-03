"""Numerical finite differences/controlled histories, not field effect evidence."""
import numpy as np
import pytest
from packages.exploratory import heads
from packages.temporal.models import tcn_initialize,tcn_forward
from packages.config.loader import load_bundle


def test_multiscale_gradient_matches_numerical_derivative():
    rng=np.random.default_rng(1);x=rng.normal(size=(2,9,2));y=rng.normal(size=(2,4));d=[1,2,4]
    p=heads.initialize(2,3,4,3,d);_,g=heads.loss_gradient(x,y,p,d)
    for key in p:
        index=tuple(0 for _ in p[key].shape);old=p[key][index];epsilon=1e-6
        p[key][index]=old+epsilon;a=heads.loss_gradient(x,y,p,d)[0]
        p[key][index]=old-epsilon;b=heads.loss_gradient(x,y,p,d)[0];p[key][index]=old
        assert (a-b)/(2*epsilon)==pytest.approx(g[key][index],rel=1e-4,abs=1e-6)


def test_two_layer_head_matches_existing_initial_tcn_and_extended_head_uses_older_history():
    rng=np.random.default_rng(5);x=rng.normal(size=(2,16,3));old=tcn_initialize(3,4,6,11)
    p=heads.initialize(3,4,6,11,[1,2])
    np.testing.assert_allclose(heads.forward(x,p,[1,2]),tcn_forward(x,old),atol=1e-12)
    changed=x.copy();changed[:,2]+=10
    np.testing.assert_array_equal(heads.forward(x,p,[1,2]),heads.forward(changed,p,[1,2]))
    extended=heads.initialize(3,4,6,11,[1,2,4])
    assert not np.allclose(heads.forward(x,extended,[1,2,4]),heads.forward(changed,extended,[1,2,4]))


def test_zero_residual_is_exact_persistence_and_constant_shift_does_not_change_delta_inputs():
    rng=np.random.default_rng(1);x=rng.normal(size=(2,8,4));last=x[:,-1]
    p={'components':np.eye(4),'scale':np.ones(4),'mean':np.arange(4)}
    predicted=heads.decode(np.zeros((2,12)),p,last,3,True)
    np.testing.assert_array_equal(predicted,np.repeat(last[:,None],3,axis=1))
    np.testing.assert_allclose(heads.delta_input(x,'delta',1),heads.delta_input(x+10,'delta',1))
    shuffled=heads.delta_input(x,'delta_shuffled_history',3)
    np.testing.assert_array_equal(shuffled[:,-1],np.zeros_like(last))
    assert not np.array_equal(shuffled[:,:-1],heads.delta_input(x,'delta',3)[:,:-1])


def test_checkpoint_uses_validation_only_and_seed_is_reproducible():
    rng=np.random.default_rng(8);x=rng.normal(size=(12,8,2));y=rng.normal(size=(12,4))
    learning=load_bundle().config.learning.model_copy(update={'epochs':3,'hidden_dim':4,'batch_size':8})
    def score(p):return float(sum(np.square(v).sum() for v in p.values()))
    a,log=heads.fit(x,y,learning,11,[1,2],5,score=score,select_validation=True)
    b,again=heads.fit(x,y,learning,11,[1,2],5,score=score,select_validation=True)
    assert log==again and log['holdout_used_for_selection'] is False
    for key in a:np.testing.assert_array_equal(a[key],b[key])
    assert len(log['train_latent_mse'])==len(log['validation_raw_feature_mse'])==4


def test_baselines_share_targets_and_residual_selection_has_no_holdout_argument():
    from packages.exploratory.robustness import fit_seed,predict,HEADS
    from packages.temporal.models import fit_projection
    bundle=load_bundle()
    bundle.config.learning=bundle.config.learning.model_copy(update={'epochs':2,'hidden_dim':4,'batch_size':8})
    rng=np.random.default_rng(23);x=rng.normal(size=(12,8,6));y=rng.normal(size=(12,3,6))
    val_x=rng.normal(size=(8,8,6));val_y=rng.normal(size=(8,3,6))
    projection,_=fit_projection(x.reshape(-1,6),4)
    parameters,configs,logs=fit_seed(x,y,val_x,val_y,projection,bundle,11,8)
    predictions=predict(val_x,projection,parameters,configs,11,3)
    assert set(predictions)>={'persistence','train_mean','ridge','initial_tcn','shuffled_ridge','shuffled_tcn','causal_tcn'}
    assert all(p.shape==val_y.shape and np.isfinite(p).all() for p in predictions.values())
    assert logs['multiscale_delta_tcn']['effective_history_windows']==8
    assert logs['causal_tcn']['effective_history_windows']==7
    assert configs['delta_shuffled_history']['history']==7
    assert all(not log.get('holdout_used_for_selection',False) for log in logs.values())


def test_holdout_perturbation_changes_evaluation_but_not_weights_or_validation_selection(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from packages.temporal.data import Sequence
    from packages.exploratory import robustness
    from packages.exploratory.experiment import partitions
    import json
    bundle=load_bundle()
    bundle.config.learning=bundle.config.learning.model_copy(update={'epochs':2,'hidden_dim':4,'batch_size':16})
    bundle.config.exploratory=bundle.config.exploratory.model_copy(update={'seeds':[11],'history_lengths':[8]})
    rng=np.random.default_rng(12);vectors=rng.normal(size=(400,512))
    m={'source_hash':'a'*64,'discarded_clips':[],'domains':[{'start_ms':0,'end_ms':201500}],
        'eligible_rows':400,'feature_cache':{'sha256':'b'*64},'feature_space_id':'c'*64}
    seq=Sequence(m,vectors,[SimpleNamespace(pts_start_ms=i*500,pts_end_ms=i*500+2000,
        split='unassigned',time_domain_id='fixture',episode_id='fixture',entity_id=None) for i in range(400)],list(range(400)))
    monkeypatch.setattr(robustness,'load_source',lambda *args:(m,[seq]))
    original_tensors=robustness.tensors
    def guarded(pool,windows):
        if pool[0].batches[0].pts_start_ms>=partitions(201500,2000,'B')['holdout'][0]:
            assert (current[0]/'history-8/seed-11/selection-before-holdout.json').is_file()
        return original_tensors(pool,windows)
    monkeypatch.setattr(robustness,'tensors',guarded)
    (tmp_path/'manifest.json').write_text('{}')
    current=[tmp_path/'first']
    a=robustness.run_asset(tmp_path,{'asset_id':'fabricated','sha256':'a'*64},bundle,current[0])
    boundary=partitions(201500,2000,'B')['holdout'][0]
    vectors[np.arange(400)*500>=boundary]+=100
    current[0]=tmp_path/'changed'
    b=robustness.run_asset(tmp_path,{'asset_id':'fabricated','sha256':'a'*64},bundle,current[0])
    assert a['cohorts']['8']['runs'][0]['selected_by_validation']==b['cohorts']['8']['runs'][0]['selected_by_validation']
    with np.load(tmp_path/'first/history-8/seed-11/weights.npz') as x, np.load(tmp_path/'changed/history-8/seed-11/weights.npz') as y:
        for key in x.files:np.testing.assert_array_equal(x[key],y[key])
    assert a['cohorts']['8']['runs'][0]['splits']['holdout']['metrics']!=b['cohorts']['8']['runs'][0]['splits']['holdout']['metrics']
