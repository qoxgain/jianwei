import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from config_doctor import load_bundle,read_yaml
from contracts import SystemConfig,Knowledge,MemoryPatch,TeacherResult,Forecast
from learning_core import MemoryStore,RidgePredictor,validate_manifest,compare_versions

class ConfigurationTests(unittest.TestCase):
    def test_all_profiles(self):
        for p in ['demo','lab','site']:
            cfg,kn,_=load_bundle(ROOT/'config/system.yaml',p)
            self.assertEqual(cfg.active_scene,kn.scene_id)
    def test_unknown_parameter_rejected(self):
        raw=read_yaml(ROOT/'config/system.yaml');raw['learning']['auto_trian']=True
        with self.assertRaises(ValueError): SystemConfig.model_validate(raw)
    def test_foundation_model_update_rejected(self):
        raw=read_yaml(ROOT/'config/system.yaml');raw['teacher']['frozen']=False
        with self.assertRaises(ValueError): SystemConfig.model_validate(raw)
    def test_live_mock_rejected(self):
        raw=read_yaml(ROOT/'config/system.yaml');raw['profiles']['site']['sources'][0]['type']='synthetic'
        with self.assertRaises(ValueError): SystemConfig.model_validate(raw)
    def test_missing_action_reference(self):
        raw=read_yaml(ROOT/'knowledge/labels.yaml');raw['sop']['edges'][0]['to_action']='unknown'
        with self.assertRaises(ValueError): Knowledge.model_validate(raw)
    def test_undeclared_sop_cycle(self):
        raw=read_yaml(ROOT/'knowledge/labels.yaml');raw['sop']['edges'].append({'from_action':'check_surface','to_action':'check_hem'})
        with self.assertRaises(ValueError): Knowledge.model_validate(raw)
    def test_memory_patch_schema(self):
        MemoryPatch.model_validate(read_yaml(ROOT/'knowledge/memory_patch.example.yaml'))
    def test_duplicate_yaml_key(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.yaml';p.write_text('a: 1\na: 2\n')
            with self.assertRaises(ValueError): read_yaml(p)
    def test_forecast_cannot_see_future(self):
        raw=json.loads((ROOT/'examples/forecast.json').read_text());raw['input_max_pts_ms']=3000
        with self.assertRaises(ValueError): Forecast.model_validate(raw)
    def test_teacher_cannot_confirm_without_evidence(self):
        raw=json.loads((ROOT/'examples/teacher_result.json').read_text());raw['observed_action_id']='check_pocket'
        with self.assertRaises(ValueError): TeacherResult.model_validate(raw)

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=MemoryStore(Path(self.temp.name)/'m.sqlite',['a','b'],'space',1.,.8)
    def tearDown(self): self.store.close();self.temp.cleanup()
    def put(self,i,action='a',**kw):
        args=dict(event_id=f'e{i}',episode='ep1',seq=i,action=action,vector=[1.,0.],quality=1.,source='independent_visual',evidence=f'ev{i}',pts_ms=(i+1)*1000,space_id='space',split='train');args.update(kw)
        return self.store.observe(**args)
    def test_forecast_is_not_training_truth(self):
        self.assertEqual(self.put(0,source='prediction'),'quarantined')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM prototypes').fetchone()[0],0)
    def test_teacher_only_is_quarantined(self): self.assertEqual(self.put(0,source='teacher_only'),'quarantined')
    def test_uncertainty_breaks_transition_chain(self):
        self.put(0);self.put(1,quality=.2);self.put(2,'b')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM transitions').fetchone()[0],0)
    def test_duplicate_event_is_idempotent(self):
        self.put(0);self.put(1,'b');self.assertEqual(self.put(1,'b'),'duplicate')
        self.assertEqual(self.store.db.execute('SELECT n FROM transitions').fetchone()[0],1)
    def test_holdout_never_enters_memory(self):
        with self.assertRaises(ValueError): self.put(0,split='test')
    def test_same_dim_new_space_rejected(self):
        with self.assertRaises(ValueError): self.put(0,space_id='different_space')
    def test_episode_boundary_no_edge(self):
        self.put(0);self.put(1,'b',episode='ep2')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM transitions').fetchone()[0],0)
    def test_missing_sequence_no_edge(self):
        self.put(0);self.put(2,'b')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM transitions').fetchone()[0],0)
    def test_future_observation_corrects_distribution(self):
        before=self.store.probabilities('a')['b'];self.put(0);self.put(1,'b')
        self.assertGreater(self.store.probabilities('a')['b'],before)
    def test_out_of_order_requires_replay(self):
        self.put(2)
        with self.assertRaises(ValueError): self.put(1)
    def test_memory_retrieval_is_evidence_not_probability(self):
        self.put(0,vector=[1.,0.]);self.put(1,'b',vector=[0.,1.])
        r=self.store.retrieve([0.,1.],'space')
        self.assertEqual(r[0]['action_id'],'b')
        self.assertEqual(r[0]['status'],'candidate_only')

class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cfg,_,_=load_bundle(ROOT/'config/system.yaml')
        self.base={'macro_f1':.8,'per_class_f1':{'a':.8,'b':.8},'per_class_gold_n':{'a':100,'b':100},'coverage':.95,'false_alarms_per_hour':.1,'p95_ms':100,'gain_ci_lower':.01,'dataset_hash':'same','protocol_id':'same'}
    def gate(self,c): return compare_versions(self.base,c,self.cfg.evaluation)
    def test_per_class_regression_blocks_better_mean(self):
        c=copy.deepcopy(self.base);c['macro_f1']=.84;c['per_class_f1']={'a':.72,'b':.96}
        self.assertFalse(self.gate(c)['passed'])
    def test_missing_gold_blocks_release(self):
        c=copy.deepcopy(self.base);c['macro_f1']=.84;c['per_class_gold_n']['b']=2
        self.assertFalse(self.gate(c)['passed'])
    def test_missing_metric_blocks_release(self):
        c=copy.deepcopy(self.base);del c['coverage'];self.assertFalse(self.gate(c)['passed'])
    def test_incomparable_test_set_blocks_release(self):
        c=copy.deepcopy(self.base);c['macro_f1']=.84;c['dataset_hash']='another'
        self.assertFalse(self.gate(c)['passed'])
    def test_nan_cannot_pass_gate(self):
        c=copy.deepcopy(self.base);c['macro_f1']=float('nan');self.assertFalse(self.gate(c)['passed'])
    def test_metrics_pass_does_not_deploy(self):
        c=copy.deepcopy(self.base);c['macro_f1']=.84;c['per_class_f1']={'a':.84,'b':.84}
        r=self.gate(c);self.assertTrue(r['passed']);self.assertEqual(r['deployment'],'not_implemented')
    def test_source_split_leakage(self):
        a={'sample_id':'a','episode_id':'e1','source_video_id':'v1','source_hash':'hash','split':'train','start_ms':0,'end_ms':1000,'data_origin':'synthetic'}
        b={**a,'sample_id':'b','episode_id':'e2','source_video_id':'v2','split':'test'}
        with self.assertRaises(ValueError): validate_manifest([a,b])
    def test_small_head_trains_without_labels(self):
        rng=np.random.default_rng(31);x=rng.normal(size=(100,4));w=rng.normal(size=(4,4));y=x@w
        head=RidgePredictor(.01).fit(x[:80],y[:80]);pred=head.predict(x[80:])
        self.assertLess(float(np.mean((pred-y[80:])**2)),.001)

if __name__=='__main__': unittest.main()
