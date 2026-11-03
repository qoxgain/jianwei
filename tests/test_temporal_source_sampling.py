"""Source and episode fairness uses fabricated timestamps, never field effects."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from packages.config.loader import load_bundle
from packages.config.errors import ContractError
from packages.temporal.data import Sequence, window_refs
from packages.temporal import engine
from tests.test_research_protocol import study, trained_row, evaluation_request, SCENE
from infra.models import AuditEvent, TemporalRun
from sqlalchemy import select, func


def sequence(source, episode='cycle', length=60):
    return Sequence({'source_hash': source, 'discarded_clips': []}, None,
        [SimpleNamespace(pts_start_ms=i*500, pts_end_ms=i*500+2000, split='test',
            time_domain_id='clock', episode_id=episode, entity_id=None) for i in range(length)],list(range(length)))


@pytest.mark.parametrize('sources,budget',[(1,8),(2,8),(8,8),(9,9),(25,32)])
def test_every_source_has_a_window_and_entire_budget_is_used(sources,budget):
    b=load_bundle();seq=[sequence(str(i)) for i in range(sources)];coverage={}
    settings=b.config.temporal.model_copy(update={'max_pairs_per_split':budget})
    windows,rejected=window_refs(seq,b.config.learning,settings,coverage=coverage)
    assert {w.sequence for w in windows}==set(range(sources))
    assert len(windows)==budget
    assert coverage['test']['source_coverage']==1.
    assert rejected['resource_subsampled']==coverage['test']['candidate_windows']-budget


def test_nine_sources_with_eight_budget_blocks_instead_of_omitting_last():
    b=load_bundle();coverage={}
    with pytest.raises(ContractError) as error:
        window_refs([sequence(str(i)) for i in range(9)],b.config.learning,
            b.config.temporal.model_copy(update={'max_pairs_per_split':8}),coverage=coverage)
    assert error.value.code=='TEMPORAL_WINDOW_BUDGET'
    assert coverage['test']['required_sources']==9 and coverage['test']['selected_windows']==0


def test_episode_minimum_then_source_fairness_and_stable_order():
    b=load_bundle();settings=b.config.temporal.model_copy(update={'max_pairs_per_split':8})
    seq=[sequence('A',str(i)) for i in range(3)]+[sequence('B')];coverage={}
    windows,_=window_refs(seq,b.config.learning,settings,coverage=coverage)
    assert {w.sequence for w in windows}==set(range(4))
    assert {s['source_hash']:s['selected_windows'] for s in coverage['test']['sources']}=={'A':4,'B':4}
    reversed_seq=list(reversed(seq));again,_=window_refs(reversed_seq,b.config.learning,settings)
    def identity(items,refs):
        return sorted((items[w.sequence].manifest['source_hash'],items[w.sequence].batches[0].episode_id,w.issued_ms) for w in refs)
    assert identity(seq,windows)==identity(reversed_seq,again)


def test_more_episodes_than_budget_blocks_even_with_one_source():
    b=load_bundle()
    with pytest.raises(ContractError,match='预算'):
        window_refs([sequence('A',str(i)) for i in range(9)],b.config.learning,
            b.config.temporal.model_copy(update={'max_pairs_per_split':8}))


def test_zero_window_source_cannot_disappear_from_complete_source_statistics():
    b=load_bundle();seq=[sequence('A'),sequence('B',length=8)]
    windows,_=window_refs(seq,b.config.learning,b.config.temporal)
    with pytest.raises(ContractError) as error:
        engine.validate_windows(seq,windows,b.config.temporal,{'test'})
    assert error.value.code=='TEMPORAL_SOURCE_COVERAGE'


def test_formal_budget_failure_precedes_reservation_and_vector_reads(study,monkeypatch):
    service,train,_=study;trained_row(service,train)
    # Nine valid episodes over two independent sources; the model's fixed limit
    # is eight. Admission must reject before any sealed identity is consumed.
    import packages.temporal_jobs.service as module
    b=service.bundle
    seq=[sequence('A',str(i)) for i in range(8)]+[sequence('B')]
    monkeypatch.setattr(module,'metadata_sequences',lambda *args: seq if args[0]['source_hash'].endswith('4') else [])
    with service.database.session() as session:
        row=session.get(TemporalRun,'fixture_model')
        import json
        report=json.loads(row.report_json);report['model']['settings']['max_pairs_per_split']=8
        row.report_json=json.dumps(report)
    monkeypatch.setattr(module,'load_sequences',lambda *a:pytest.fail('admission read test vectors'))
    with pytest.raises(ContractError) as error:service.enqueue(SCENE,evaluation_request(),'evaluate')
    assert error.value.code=='TEMPORAL_WINDOW_BUDGET'
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent))==0
        assert session.scalar(select(func.count()).select_from(TemporalRun).where(TemporalRun.kind=='evaluate'))==0
