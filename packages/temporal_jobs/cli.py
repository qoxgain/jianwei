"""Explicit CLI requests run through the same durable service as the HTTP API."""
import json
from pathlib import Path
import time
from pydantic import ValidationError
from infra.database import Database
from packages.config.errors import ContractError
from packages.config.loader import assert_demo_service, database_url
from packages.contracts.temporal import TrainRequest, EvaluationRequest, ReadinessRequest
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


def check(bundle,args):
    """Read an existing local database, without starting workers or migrations."""
    from sqlalchemy import inspect
    from sqlalchemy.engine import make_url
    assert_demo_service(bundle)
    try:
        path=Path(args.request)
        if path.stat().st_size>262144:raise ContractError('REQUEST_TOO_LARGE','研究请求文件超出256KiB')
        request=ReadinessRequest.model_validate(json.loads(path.read_text()))
    except (OSError,ValueError,ValidationError):
        raise ContractError('REQUEST_INVALID','研究预检请求JSON不符合严格契约') from None
    url=make_url(database_url(bundle))
    if url.get_backend_name()=='sqlite' and (not url.database or not Path(url.database).is_file()):
        raise ContractError('RESEARCH_DATABASE_MISSING','请先启动应用并完成数据导入')
    database=Database(str(url))
    try:
        if not {'import_jobs','temporal_runs','source_splits'}<=set(inspect(database.engine).get_table_names()):
            raise ContractError('RESEARCH_DATABASE_MISSING','请先启动应用并完成数据导入')
        service=TemporalService(bundle,database,ImportService(bundle,database))
        report=service.readiness(args.scene,request)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['can_submit_train'] or report['can_submit_evaluate'] else 2
    finally:database.close()
