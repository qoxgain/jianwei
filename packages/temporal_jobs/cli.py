"""Explicit CLI requests run through the same durable service as the HTTP API."""
import json
from pathlib import Path
import time
from pydantic import ValidationError
from infra.database import Database
from packages.config.errors import ContractError
from packages.config.loader import assert_demo_service, database_url
from packages.contracts.temporal import TrainRequest, EvaluationRequest
from packages.ingest_jobs.service import ImportService
from .service import TemporalService


def run(bundle,args):
    assert_demo_service(bundle)
    path=Path(args.request)
    try:
        if path.stat().st_size>262144:raise ContractError('REQUEST_TOO_LARGE','研究请求文件超出256KiB')
        raw=json.loads(path.read_text())
        request=(TrainRequest if args.kind=='train' else EvaluationRequest).model_validate(raw)
    except (OSError,ValueError,ValidationError):raise ContractError('REQUEST_INVALID','研究请求JSON不符合严格契约') from None
    database=Database(database_url(bundle));database.migrate(bundle.profile.tenant_id,bundle.config.active_scene)
    imports=ImportService(bundle,database);service=TemporalService(bundle,database,imports)
    try:
        service.start();job=service.enqueue(args.scene,request,args.kind)
        while job['status'] in {'queued','running'}:
            time.sleep(.1);job=service.get(args.scene,job['id'])
        print(json.dumps(job,ensure_ascii=False,indent=2))
        return 0 if job['status']=='succeeded' else 2
    finally:service.close();database.close()
