from copy import deepcopy
import pytest
from pydantic import ValidationError
from packages.contracts.learning import FeatureSpace, FeatureBatch, Prediction, ActivityObservation, assert_future_pair, assert_compatible

def feature(start=0,end=10):
    space=FeatureSpace(contract_version='3.1',encoder_sha='a'*64,preprocessing_sha='b'*64,sampling_sha='c'*64,roi_policy_hash='d'*64,pooling='mean',dimension=512)
    return FeatureBatch(contract_version='3.1',tenant_id='tenant',scene_id='scene',episode_id='episode',source_id='camera',source_video_id='video',source_hash='e'*64,entity_id=None,pts_start_ms=start,pts_end_ms=end,context={},feature_space=space,feature_space_id=space.identifier(),quality=1.0,vector_uri='features/a.npz',data_origin='real_video',split='train')

@pytest.mark.parametrize('field', ['encoder_sha','preprocessing_sha','sampling_sha','roi_policy_hash','pooling','dimension'])
def test_same_dimension_is_not_a_feature_space(field):
    a=feature(); raw=a.model_dump(); raw['feature_space'][field] = 1024 if field=='dimension' else 'max' if field=='pooling' else 'f'*64
    with pytest.raises(ValidationError): FeatureBatch.model_validate(raw)
    raw['feature_space_id'] = FeatureSpace.model_validate(raw['feature_space']).identifier()
    b=FeatureBatch.model_validate(raw)
    with pytest.raises(ValueError): assert_compatible(a,b)

@pytest.mark.parametrize('field,value', [('episode_id','episode2'),('source_video_id','video2'),('source_hash','f'*64),('source_id','camera2'),('entity_id','person2'),('split','test')])
def test_training_pairs_cannot_cross_identity_or_split(field,value):
    a,b=feature(),feature(20,30)
    assert_future_pair(a,b,10)
    raw=b.model_dump(); raw[field]=value
    with pytest.raises(ValueError): assert_future_pair(a,FeatureBatch.model_validate(raw),10)

def test_forecast_is_strictly_causal_and_cannot_be_observation():
    raw=dict(contract_version='3.1',tenant_id='tenant',scene_id='scene',prediction_id='forecast',episode_id='episode',source_video_id='video',feature_space_id='a'*64,kind='empirical_forecast',prediction_kind='future_state',issued_at_pts_ms=10,input_max_pts_ms=10,target_start_pts_ms=20,target_end_pts_ms=30,distribution={'cluster_1':1.0},predictor_version='v1',knowledge_version=None,memory_version=None)
    Prediction.model_validate(raw)
    for patch in [{'input_max_pts_ms':11},{'target_start_pts_ms':10},{'target_end_pts_ms':20},{'kind':'visual_observation'},{'distribution':{'a':0.8}}]:
        with pytest.raises(ValidationError): Prediction.model_validate({**raw,**patch})
    with pytest.raises(ValidationError): ActivityObservation.model_validate(raw)
