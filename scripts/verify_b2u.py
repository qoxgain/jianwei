"""Real executed state/query counterchecks; output scalar evidence only."""
import argparse,copy,json
from pathlib import Path
import numpy as np
from packages.config.loader import load_bundle
from packages.config.errors import ContractError
from packages.unlabeled.engine import query
from packages.unlabeled.memory import load_state,validate_registry
from packages.local_motion.extraction import write,sha


def equal(a,b):
    if isinstance(a,np.ndarray):return isinstance(b,np.ndarray) and np.array_equal(a,b)
    if isinstance(a,dict):return a.keys()==b.keys() and all(equal(v,b[k]) for k,v in a.items())
    if isinstance(a,list):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a==b


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',type=Path,required=True);p.add_argument('--updated',type=Path,required=True)
    p.add_argument('--query-config',type=Path,required=True);p.add_argument('--real-parent',type=Path);p.add_argument('--real-config',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cfg=load_bundle(a.query_config);budget=cfg.config.ingestion.max_expanded_bytes
    parent=load_state(a.parent,budget);updated=load_state(a.updated,budget)
    assert equal(parent['branches'],updated['branches'][:-1]),'Parent models changed'
    old=validate_registry(json.loads((a.parent/'registry-private.json').read_text()));new=validate_registry(json.loads((a.updated/'registry-private.json').read_text()))
    assert new['parent_version']==old['version'] and new['entries'][:len(old['entries'])]==old['entries']
    assert updated['branches'][-1]['source']['sha256']!=parent['source']['sha256']
    outcomes=[]
    def blocked(name,config,parent_path,expected):
        try:query(parent_path,config,a.output/name)
        except ContractError as e:assert e.code==expected,(name,e.code);outcomes.append({'case':name,'code':e.code,'passed':True})
        else:raise AssertionError('Expected admission refusal: '+name)
    changed=copy.deepcopy(cfg);changed.config.unlabeled.noise_sigma+=.001
    blocked('frozen-parameters',changed,a.parent,'UNLABELED_QUERY_CONFIG_CHANGED')
    # The same actual source cannot become a new source merely by running again.
    blocked('repeated-source',cfg,a.updated,'UNLABELED_SAME_ROOT')
    if a.real_config:
        assert a.real_parent;real=load_bundle(a.real_config)
        blocked('unverified-field-source',real,a.real_parent,'UNLABELED_INDEPENDENCE_UNKNOWN')
    for d in [a.parent,a.updated]:
        r=json.loads((d/('report-private.json' if d==a.parent else 'new-candidate-study-private.json')).read_text())
        for m in r['methods']:
            if m['status']!='computed':continue
            assert m['shadow_gate']['frozen_before_replay']
            if not m['shadow_gate']['passed']:assert m['shadow_gate']['active_prototypes']==0
            for proto in m['prototypes']:
                spans=sorted(proto['occurrences'],key=lambda v:v['start_ms'])
                assert len(spans)==proto['support_count'] and all(x['end_ms']<=y['start_ms'] for x,y in zip(spans,spans[1:]))
    result={'passed':True,'parent_branches_unchanged':True,'old_registry_entries_unchanged':True,'distinct_source_roots':True,
        'separate_fit_groups':len(updated['branches']),'shadow_entries_added':new['new_entries'],'admission_counterexamples':outcomes,
        'original_field_independence_validated':False,'real_precision':None,'real_recall':None,'real_f1':None,'production_updated':False}
    write(a.output/'results.json',result);print(json.dumps(result))


if __name__=='__main__':main()
