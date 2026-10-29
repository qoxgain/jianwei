"""Read registered original media and single-source descriptive feature reports."""
import json
from packages.config.errors import ContractError
from packages.ingestion.pipeline import file_hash
from packages.temporal.exploration import explore


class ImportInspection:
    def __init__(self, imports):
        self.imports=imports
        self.verified={}

    def media(self, scene, identifier):
        service=self.imports;service.check_scene(scene)
        with service.database.session() as session:
            row=service._job(session,scene,identifier)
            request=json.loads(row.request_json)
            if request['data_origin']!='real_video':
                raise ContractError('IMPORT_MEDIA_UNAVAILABLE','合成示例没有原始视频')
            video_id=request['upload_id'] if request['format']=='mp4' else request.get('source_upload_id')
            if not video_id:raise ContractError('IMPORT_MEDIA_UNAVAILABLE','该导入未上传原始视频')
            artifact=service._artifact(session,video_id,'mp4');path=service._path(artifact)
            digest,size=artifact.sha256,artifact.size_bytes
        try:
            stat=path.stat();stamp=(path,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)
            if stat.st_size!=size:raise OSError
            if self.verified.get(stamp)!=digest:
                actual=file_hash(path);after=path.stat()
                if actual!=digest or (after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_ino)!=stamp[1:]:raise OSError
                if len(self.verified)>=200:self.verified.clear()
                self.verified[stamp]=digest
        except OSError:
            raise ContractError('UPLOAD_CORRUPTED','原始视频已丢失或改变，不能回用原引用') from None
        return path,digest

    def report(self, scene, identifier):
        job=self.imports.get(scene,identifier);manifest=job['manifest']
        if manifest is None:raise ContractError('IMPORT_MANIFEST_UNAVAILABLE','任务尚未生成可探索清单')
        # Admission precedes media fingerprinting: a sealed source must not be
        # opened even to produce a descriptive report that will later be rejected.
        if manifest.get('split') != 'unassigned':
            raise ContractError('EXPLORATION_SPLIT_FORBIDDEN','单源探索不能读取训练、校准或封存测试来源')
        if (manifest.get('status') != 'succeeded' or manifest.get('data_origin') != 'real_video'
                or manifest.get('source_verification') != 'verified'):
            raise ContractError('EXPLORATION_SOURCE_UNVERIFIED','需要成功导入并核验原视频的真实特征')
        bundle=self.imports.bundle
        if bundle.config.temporal is None:
            raise ContractError('TEMPORAL_DISABLED','当前配置未开启时序探索')
        path,_=self.media(scene,identifier)
        result=explore(manifest,self.imports.output_root/identifier/'vectors.npy',path,
                       bundle.config.temporal,bundle.config_hash)
        prefix=f'/api/scenes/{scene}/imports/{identifier}/media'
        result['media_url']=prefix
        preview_settings=getattr(bundle.config,'sop_review',None)
        result['preview_url']=prefix+'/preview' if preview_settings and preview_settings.preview_enabled else None
        return result

    def preview(self,scene,identifier):
        path,digest=self.media(scene,identifier)
        bundle=self.imports.bundle;settings=getattr(bundle.config,'sop_review',None)
        if settings is None:raise ContractError('PREVIEW_DISABLED','当前配置尚未启用本地兼容预览')
        from packages.sop.preview import preview_video
        return preview_video(path,digest,bundle,settings)
