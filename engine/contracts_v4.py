"""M4 portable references; scenario/proposal versions remain unchanged."""
import copy
from .contracts import INT, TEXT, NUM, BOOL, SOLUTION, REPORT, enum, array, obj
from .contracts_v3 import CASE, RUN_V3, MANIFEST_V1, REPORT_V1

CASE_V2=copy.deepcopy(CASE)
CASE_V2['properties'].update(instance=TEXT,method=enum('ordered','shuffled','learned'),repetition=enum(0,1))
RUN_V4=copy.deepcopy(RUN_V3)
RUN_V4['properties'].update(schema_version=dict(const=4),case=CASE_V2,scenario_ref=TEXT,checkpoint_hash={'anyOf':[TEXT,{'type':'null'}]})
MANIFEST_V2=copy.deepcopy(MANIFEST_V1)
MANIFEST_V2['properties'].update(schema_version=dict(const=2),cases=array(CASE_V2,minItems=336,maxItems=336),
    instances=array({'type':'object'},minItems=8,maxItems=8),main_cases=dict(const=288),control_cases=dict(const=48))
REPORT_V2=copy.deepcopy(REPORT_V1)
REPORT_V2['properties'].update(schema_version=dict(const=2),total=dict(const=336))

REAL={'type':'number'}
FEATURE_SCHEMA_V1=obj(version=dict(const=1),names=array(TEXT,minItems=30,maxItems=30),normalization=TEXT)
DATASET_ENTRY=obj(id=TEXT,city=enum('bologna','modena'),seed=INT,split=enum('training','validation','test'),
                  path=TEXT,scenario_hash=TEXT,layout_hash=TEXT)
DATASET_V1=obj(schema_version=dict(const=1),kind=enum('learned_dataset'),entries=array(DATASET_ENTRY,minItems=40,maxItems=40),
               source_sha256=TEXT,constructed=BOOL,scope=TEXT,dataset_hash=TEXT)
SAMPLE_ROW=obj(index=INT,features=array(REAL,minItems=30,maxItems=30),improvement=BOOL,gain=NUM)
SAMPLE_GROUP=obj(solution=SOLUTION,validation=REPORT,rows=array(SAMPLE_ROW))
SAMPLES_V1=dict(type='object',properties=dict(schema_version=dict(const=1),kind=enum('learned_samples'),
    scenario_id=TEXT,split=enum('training','validation'),scenario_hash=TEXT,method=enum('ordered','shuffled'),
    time_weight=enum(0,.5,1),seed=dict(const=10),feature_schema=FEATURE_SCHEMA_V1,groups=array(SAMPLE_GROUP,maxItems=3),
    status=enum('completed','failed'),samples_hash=TEXT,duration_s=NUM),
    required=['schema_version','kind','scenario_id','split','scenario_hash','method','time_weight','seed',
              'feature_schema','groups','status','samples_hash','duration_s'],additionalProperties=True)
MODEL_LAYER=obj(weight=array(array(REAL)),bias=array(REAL))
POLICY_V1=obj(schema_version=dict(const=1),kind=enum('learned_policy'),architecture=array(INT,minItems=4,maxItems=4),
              feature_schema=FEATURE_SCHEMA_V1,layers=array(MODEL_LAYER,minItems=3,maxItems=3),
              metadata={'type':'object'},model_hash=TEXT)

if __name__=='__main__':
    import json
    from pathlib import Path
    for name,schema in [('run-v4',RUN_V4),('experiment-manifest-v2',MANIFEST_V2),('experiment-report-v2',REPORT_V2),
                        ('learned-dataset-v1',DATASET_V1),('learned-samples-v1',SAMPLES_V1),('learned-policy-v1',POLICY_V1)]:
        Path(f'schemas/{name}.json').write_text(json.dumps({'$schema':'https://json-schema.org/draft/2020-12/schema',**schema},indent=2)+'\n')
