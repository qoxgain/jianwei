"""Regenerate JSON Schemas and the P0 API contract from local models."""
import json
import re
from pathlib import Path
import yaml
from contracts import SystemConfig,Knowledge,MemoryPatch,TeacherResult,Forecast

ROOT=Path(__file__).resolve().parents[1]

def obj(fields,required=None):
    return {'type':'object','additionalProperties':False,'properties':fields,'required':list(fields) if required is None else required}

def main():
    d=ROOT/'schemas';d.mkdir(exist_ok=True)
    for name,model in [('system',SystemConfig),('labels',Knowledge),('memory_patch',MemoryPatch),('teacher_result',TeacherResult),('forecast',Forecast)]:
        schema=model.model_json_schema();schema['$schema']='https://json-schema.org/draft/2020-12/schema'
        (d/(name+'.schema.json')).write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding='utf-8')
    s={'type':'string','minLength':1};arr={'type':'array','items':s,'minItems':1}
    job=obj({'job_id':s,'status':{'type':'string','enum':['PENDING','RUNNING','DONE','FAILED','SKIPPED']},'status_url':s})
    error=obj({'code':s,'message':s,'trace_id':s})
    specs=[
      ('post','/learning/ingestions',obj({'asset_ids':arr,'scene_id':s,'split':{'type':'string','enum':['train','calibration','dev','test']},'manifest_id':s})),
      ('post','/learning/jobs',obj({'dataset_version':s,'task':{'type':'string','enum':['extract','segment','temporal_head','tsm','yolo_detector','evaluate']},'config_hash':s})),
      ('post','/knowledge/validate',obj({'base_version':s,'knowledge':{'$ref':'../schemas/labels.schema.json'}})),
      ('post','/knowledge/releases',obj({'validated_revision':s,'base_version':s})),
      ('post','/memory/patches',{'$ref':'../schemas/memory_patch.schema.json'}),
      ('post','/observations/{id}/feedback',obj({'decision':{'type':'string','enum':['confirm','reject','relabel','unknown']},'correct_action_id':{'type':['string','null']},'evidence_ids':arr,'reason':s})),
      ('post','/evaluations/compare',obj({'candidate_id':s,'champion_id':s,'dataset_hash':s,'protocol_id':s})),
      ('post','/models/releases',obj({'candidate_id':s,'evaluation_id':s,'policy_version':s,'expected_current_release':s})),
      ('post','/models/rollback',obj({'release_id':s,'target_release_id':s,'reason':s})),
      ('get','/jobs/{job_id}',None),('get','/memory/snapshots/{version}',None),('get','/predictions',None),('get','/learning/clusters',None)
    ]
    paths={}
    for method,path,body in specs:
        parameters=[{'name':v,'in':'path','required':True,'schema':s} for v in re.findall(r'\{(.*?)\}',path)]
        if method=='post': parameters.append({'name':'Idempotency-Key','in':'header','required':True,'schema':s})
        else:
            parameters.extend([{'name':'cursor','in':'query','schema':{'type':'string'}},{'name':'limit','in':'query','schema':{'type':'integer','minimum':1,'maximum':200,'default':50}}])
        op={'operationId':method+'_'+re.sub('[^a-zA-Z0-9]+','_',path).strip('_'),'parameters':parameters,'security':[{'bearerAuth':[]}],
            'responses':{'202' if method=='post' else '200':{'description':'Asynchronous job or query result; implementation required','content':{'application/json':{'schema':job if method=='post' else {'type':'object'}}}},
             'default':{'description':'Structured error','content':{'application/json':{'schema':error}}}}}
        if body: op['requestBody']={'required':True,'content':{'application/json':{'schema':body}}}
        if path=='/knowledge/validate':
            op['responses']['200']=op['responses'].pop('202');op['responses']['200']['description']='Validation only; no production writes'
            op['responses']['200']['content']['application/json']['schema']=obj({'valid':{'type':'boolean'},'errors':{'type':'array','items':s},'validated_revision':{'type':['string','null']},'impact_summary':{'type':'object'}})
        paths[path]={method:op}
    api={'openapi':'3.1.0','info':{'title':'见微 V3 P0 核心学习API契约','version':'3.0.0','description':'This is a specification, not an implemented server.'},'servers':[{'url':'/api/v1'}],'paths':paths,'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}}}}
    (ROOT/'contracts/openapi.yaml').write_text(yaml.safe_dump(api,allow_unicode=True,sort_keys=False),encoding='utf-8')
    print('Generated 5 JSON Schemas and P0 OpenAPI contract.')

if __name__=='__main__': main()
