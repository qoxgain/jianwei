"""P0.1.1 acceptance scenarios, including an entirely unlabeled startup."""
from pathlib import Path
import copy
import pytest
from pydantic import ValidationError
from packages.config.loader import load_bundle
from packages.config.yaml import parse_yaml
from packages.contracts.scenes import SceneKnowledge

ROOT = Path(__file__).resolve().parents[1]

def test_four_scenes_load_without_changing_default():
    bundle = load_bundle()
    assert len(bundle.scenes) == 4
    unknown, _ = bundle.current_knowledge('anonymous_observation')
    assert unknown.actions == [] and unknown.events == [] and unknown.workflow is None
    safety, _ = bundle.current_knowledge('workshop_safety')
    assert safety.workflow is None and safety.events
    assembly, _ = bundle.current_knowledge('electronics_assembly')
    assert [n.action_id for n in assembly.workflow.nodes].count('inspect') == 2
    assert bundle.config.active_scene == 'garment_sop'

def test_compliance_and_node_identity_are_distinct():
    raw = parse_yaml((ROOT / 'knowledge/scenes/electronics_assembly.yaml').read_text())
    SceneKnowledge.model_validate(raw)
    wrong = copy.deepcopy(raw); wrong['workflow'] = None
    with pytest.raises(ValidationError): SceneKnowledge.model_validate(wrong)
    wrong = copy.deepcopy(raw); wrong['workflow']['nodes'][1]['node_id'] = wrong['workflow']['nodes'][0]['node_id']
    with pytest.raises(ValidationError): SceneKnowledge.model_validate(wrong)

import json
import yaml
from packages.knowledge.service import parse_knowledge, preview
PARITY=json.loads((ROOT/'tests/fixtures/v31-parity.json').read_text())

@pytest.mark.parametrize('case',PARITY['cases'],ids=lambda c:c['name'])
def test_contract_parity_fixture(case):
    base=PARITY['baselines'][case['scene_id']]
    baseline=parse_knowledge(yaml.safe_dump(base),case['scene_id'],base['task_modes'])
    result=preview(yaml.safe_dump(case['value']),baseline,case['scene_id'],base['task_modes'])
    assert result['valid']==case['valid']
    assert result['hash']==case['hash']
    assert result['diff']==case['diff']

def test_implemented_openapi_matches_snapshot():
    from apps.api.main import create_app
    assert yaml.safe_load((ROOT/'contracts/openapi.yaml').read_text())==create_app().openapi()
