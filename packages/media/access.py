"""Resolve metadata and lineage before content I/O, including deferred streaming."""
import json,hashlib
from pathlib import Path
import anyio
from sqlalchemy import select
from starlette.responses import Response
from infra.file_gate import file_gate
from infra.models import ImportJob,SourceSplit,UploadedArtifact
from packages.config.errors import ContractError


def media_settings(bundle):
    from packages.contracts.media import MediaAccessSettings
    return getattr(bundle.config,'media_access',None) or MediaAccessSettings()

def media_gate(bundle):
    settings=media_settings(bundle)
    return file_gate(bundle.paths['datasets']/'.ordinary-media.lock',wait_timeout=settings.wait_timeout_seconds,poll_seconds=settings.poll_seconds,error_code='MEDIA_LEASE_TIMEOUT')


class MediaAccess:
    def __init__(self,bundle,database):self.bundle,self.database=bundle,database;self.gate=media_gate(bundle)
    def state(self,scene,path,source_hash,*,split=None):
        if scene not in self.bundle.scenes:raise ContractError('SCENE_NOT_FOUND','场景不存在')
        tenant=self.bundle.profile.tenant_id;related={source_hash};sealed=set();pending=set();cancelled=set();failed=set()
        if split=='test':sealed.add(source_hash)
        temporal=self.bundle.config.temporal;registry=temporal.dataset_manifest if temporal else None
        if registry is not None:
            if registry.tenant_id!=tenant:raise ContractError('MEDIA_SCOPE_MISMATCH','来源清单不属于当前租户')
            # Link roots, parents and sessions even if a child claims a different partition.
            edges=[]
            for s in registry.sources:
                group={s.source_sha256,s.root_source_sha256}
                if s.parent_source_sha256:group.add(s.parent_source_sha256)
                if s.capture_session_id:group.add('session:'+s.capture_session_id)
                edges.append(group)
                if s.split=='test':sealed.update(group)
        else:edges=[]
        with self.database.session() as session:
            sealed.update(session.scalars(select(SourceSplit.source_hash).where(SourceSplit.tenant_id==tenant,SourceSplit.split=='test')))
            jobs=session.scalars(select(ImportJob).where(ImportJob.tenant_id==tenant)).all()
            uploads=session.scalars(select(UploadedArtifact).where(UploadedArtifact.tenant_id==tenant)).all()
            by_id={a.id:a for a in uploads}
            for job in jobs:
                request=json.loads(job.request_json);manifest=json.loads(job.manifest_json) if job.manifest_json else {}
                hashes={manifest.get('source_hash')}
                for key in ('upload_id','source_upload_id'):
                    a=by_id.get(request.get(key))
                    if a and a.kind=='mp4':hashes.add(a.sha256)
                hashes.discard(None)
                if manifest.get('split')=='test':sealed.update(hashes)
                if (request.get('requested_split')=='test' and job.status in {'queued','running'} and not job.cancel_requested):pending.update(hashes)
                if request.get('requested_split')=='test':
                    if job.cancel_requested:cancelled.update(hashes)
                    elif job.status in {'failed','blocked'}:failed.update(hashes)
                edges.append(hashes)
        # Fixed point closes aliases of aliases, including cross-scene registrations.
        while True:
            before=(len(sealed),len(pending),len(cancelled),len(failed),len(related))
            for group in edges:
                if group&sealed:sealed.update(group)
                if group&related:related.update(group)
                if group&pending:pending.update(group)
                if group&cancelled:cancelled.update(group)
                if group&failed:failed.update(group)
            if before==(len(sealed),len(pending),len(cancelled),len(failed),len(related)):break
        denied=bool(related&(sealed|pending))
        # Resolve symlink/hardlink aliases by metadata only, never fingerprint a sealed file.
        path=Path(path);resolved=path.resolve()
        try:stat=path.stat();inode=(stat.st_dev,stat.st_ino)
        except OSError:inode=None
        for a in uploads:
            if a.sha256 not in sealed|pending:continue
            candidate=self.bundle.paths['inbox']/'uploads'/hashlib.sha256(tenant.encode()).hexdigest()/(a.sha256+'.'+a.kind)
            if resolved==candidate.resolve():denied=True;break
            try:
                s=candidate.stat()
                if inode is not None and inode==(s.st_dev,s.st_ino):denied=True;break
            except OSError:pass
        state=('sealed' if related&sealed else 'pending_seal' if related&pending else
               'cancelled' if related&cancelled else 'seal_failed' if related&failed else 'development')
        if denied and state not in {'sealed','pending_seal'}:state='sealed'
        return {'state':state,'ordinary_media_allowed':not denied,
                'existing_grant_policy':'finish_authorized_stream_before_formal_seal_commit'}

    def check(self,scene,path,source_hash,*,split=None):
        result=self.state(scene,path,source_hash,split=split)
        if not result['ordinary_media_allowed']:
            raise ContractError('MEDIA_SEALED_FORBIDDEN','封存或待封存来源及同源媒体不能通过普通查看入口访问')
        return result

    def response(self,inner,scene,path,digest,*,split=None,derived=None):
        def authorize():
            self.check(scene,path,digest,split=split)
            if derived is not None:self.check(scene,derived,digest,split=split)
        return GuardedResponse(inner,self.gate,authorize)


class GuardedResponse(Response):
    """Hold the same lease as source assignment from final admission through send."""
    def __init__(self,inner,gate,authorize):
        super().__init__(status_code=inner.status_code,headers=dict(inner.headers))
        self.inner,self.gate,self.authorize=inner,gate,authorize
    async def __call__(self,scope,receive,send):
        async with self.gate.async_hold():
            await anyio.to_thread.run_sync(self.authorize)
            await self.inner(scope,receive,send)
