"""Experimental envelopes. Domain scenarios and proposals retain their own versions."""
from .contracts import obj, array, TEXT, NUM, INT, BOOL, NULL, SOLUTION, REPORT, SCENARIO, enum
from .contracts_v2 import SCENARIO_V2

CASE = obj(id=TEXT, instance=enum('bologna','modena'),method=enum('ordered','shuffled','ortools'),
           budget_s=enum(5,15),repetition=enum(0,1,2),seed={'anyOf':[INT,NULL]},
           time_weight=enum(0,.5,1),recording=BOOL,control=BOOL)
OBSERVATION = obj(elapsed_s=NUM,solution=SOLUTION,validation=REPORT,partial=BOOL)
RUN_V3 = dict(type='object',properties=dict(schema_version=dict(type='integer',const=3),
    kind=enum('experiment_run'),run_id=TEXT,case=CASE,status=enum('completed','cancelled','failed'),
    scenario={'anyOf':[SCENARIO,SCENARIO_V2]},original_scenario_hash=TEXT,scenario_hash=TEXT,
    objective=SCENARIO['properties']['objective'],solution={'anyOf':[SOLUTION,NULL]},
    validation={'anyOf':[REPORT,NULL]},observations=array(OBSERVATION),
    comparison_family=enum('time','time_fuel'),manifest_hash=TEXT,proof_status=enum('not_certified')),
    required=['schema_version','kind','case','status','termination_reason'],additionalProperties=True)
MANIFEST_V1 = dict(type='object',properties=dict(schema_version=dict(type='integer',const=1),
    kind=enum('experiment_manifest'),manifest_hash=TEXT,cases=array(CASE,minItems=102,maxItems=102),
    instances=array({'type':'object'},minItems=2,maxItems=2),main_cases=dict(const=84),control_cases=dict(const=18)),
    required=['schema_version','kind','manifest_hash','cases','instances','main_cases','control_cases'],additionalProperties=True)
REPORT_V1 = dict(type='object',properties=dict(schema_version=dict(type='integer',const=1),kind=enum('experiment_report'),
    manifest_hash=TEXT,completed=INT,total=dict(const=102),rows=array({'type':'object'}),fronts=array({'type':'object'})),
    required=['schema_version','kind','manifest_hash','completed','total','rows','fronts'],additionalProperties=True)

if __name__ == '__main__':
    import json
    from pathlib import Path
    for name,schema in [('run-v3',RUN_V3),('experiment-manifest-v1',MANIFEST_V1),('experiment-report-v1',REPORT_V1)]:
        Path(f'schemas/{name}.json').write_text(json.dumps({'$schema':'https://json-schema.org/draft/2020-12/schema',**schema},indent=2)+'\n')
