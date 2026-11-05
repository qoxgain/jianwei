"""Publication must not accidentally copy high dimensional or private curves."""
import copy,json
from pathlib import Path
import pytest
from scripts.publish_b1_statistics import summarize


def executed():
    return json.loads((Path(__file__).resolve().parents[1]/'apps/web/src/data/localMotionDemo.json').read_text())


def test_private_curves_candidates_and_manual_details_do_not_escape_aggregate():
    r=executed();original=copy.deepcopy(r)
    r['manual_review']={'annotator':'PRIVATE_ANNOTATOR_SECRET'}
    r['arms'][0]['series'][0]['private_extra']='PRIVATE_EMBEDDING_SECRET'
    r['primary']['candidates'][0]['secret']='PRIVATE_CANDIDATE_SECRET'
    out=summarize(r);text=json.dumps(out)
    assert 'PRIVATE_' not in text and '"points"' not in text and '"candidates"' not in text
    assert out['primary']['count']==original['primary']['count']
    assert out['parameter_configurations']==sum(len(a['grid']) for a in r['arms'])
    assert out['boundary_evaluation']['f1'] is None
    assert all('candidates' not in g for a in out['arms'] for g in a['grid'])


def test_algorithm_agreement_cannot_be_exported_as_ground_truth_accuracy():
    out=summarize(executed())
    assert not {'f1','precision','recall'} & out['modality_agreement'].keys()
    assert out['human_industrial_boundaries']==0 and not out['formal_research_passed']


def test_formal_admission_cannot_be_inferred_from_exploration():
    r=executed();r['formal_research_passed']=True
    with pytest.raises(ValueError):summarize(r)
