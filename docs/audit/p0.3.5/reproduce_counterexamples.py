import json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'jianwei-audit'))
from packages.config.loader import load_bundle
from packages.temporal import engine,protocol
from packages.temporal.data import Sequence,window_refs
from packages.ingestion.pipeline import file_hash
bundle=load_bundle();settings=bundle.config.temporal.model_copy(update={'max_pairs_per_split':8})
seqs=[Sequence({'source_hash':str(i),'discarded_clips':[]},None,[SimpleNamespace(pts_start_ms=j*500,pts_end_ms=j*500+2000,split='test',time_domain_id='fixture') for j in range(50)],list(range(50))) for i in range(9)]
windows,rejected=window_refs(seqs,bundle.config.learning,settings)
assert len(windows)==8 and {w.sequence for w in windows}==set(range(8))
a=engine.code_hash();b=protocol._bindings(bundle,'anonymous_observation')
def changed(path):
 return 'f'*64 if Path(path).name=='pipeline.py' else file_hash(path)
with patch('packages.temporal.engine.file_hash',changed),patch('packages.temporal.protocol.file_hash',changed):
 assert engine.code_hash()==a and protocol._bindings(bundle,'anonymous_observation')==b
result={'baseline':'0bf8fbab9b986940eb8d24a92a7435db06f03ea3','counterexamples':[
 {'id':'AUD-01','case':'9 eligible sequences, max_pairs=8','selected_sequences':sorted({w.sequence for w in windows}),'omitted':[8],'rejected':rejected,'note':'earlier sequences receive quota; last entire source omitted before complete-source session statistics'},
 {'id':'AUD-02','case':'change hash oracle for ingestion/pipeline.py without editing source','code_hash_unchanged':True,'protocol_bindings_unchanged':True,'note':'sampling/quality orchestration implementation absent from frozen dependency hashes'}]}
Path(__file__).with_name('counterexamples.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
