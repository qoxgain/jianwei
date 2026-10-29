"""Registered original media and immutable SOP review routes."""
import re
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse, FileResponse
from packages.contracts.sop import RevisionRequest, DecisionRequest, EvidenceRecord

router = APIRouter(prefix='/api/scenes/{scene_id}/sop', tags=['sop-review'])


@router.get('')
def get_review(scene_id: str, request: Request):
    return request.app.state.sop.get(scene_id)


@router.post('/revisions', status_code=201)
def revise(scene_id: str, body: RevisionRequest, request: Request):
    return request.app.state.sop.revise(scene_id, body)


@router.post('/decisions')
def decide(scene_id: str, body: DecisionRequest, request: Request):
    return request.app.state.sop.decide(scene_id, body)


@router.post('/evidence', status_code=201)
def evidence(scene_id: str, body: EvidenceRecord, request: Request):
    return request.app.state.sop.record_evidence(scene_id, body)


@router.get('/media/{asset_id}')
@router.head('/media/{asset_id}', include_in_schema=False)
def media(scene_id: str, asset_id: str, request: Request):
    service = request.app.state.sop
    path, asset = service.media(scene_id, asset_id)
    size = asset['bytes']
    headers = {'Accept-Ranges': 'bytes', 'ETag': f'"{asset["sha256"]}"', 'Cache-Control': 'private, no-cache',
               'X-Content-Type-Options': 'nosniff'}
    mime = 'application/pdf' if asset['kind'] == 'normative_document' else 'video/mp4'
    start, end, status = 0, size-1, 200
    range_header = request.headers.get('range')
    if range_header and request.headers.get('if-range', headers['ETag']) == headers['ETag']:
        match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', range_header)
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1]); end = min(int(match[2]), size-1) if match[2] else size-1
            else:
                suffix = int(match[2]); start, end = max(0, size-suffix), size-1
                if suffix == 0: start = size
        else:
            start = size
        if start >= size or end < start:
            return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
        status = 206
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    headers['Content-Length'] = str(end-start+1)
    if request.method == 'HEAD':
        return Response(status_code=status, headers=headers, media_type=mime)

    def chunks():
        with path.open('rb') as source:
            source.seek(start)
            remaining = end-start+1
            while remaining:
                chunk = source.read(min(remaining, service.settings.media_chunk_bytes))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
    return StreamingResponse(chunks(), status_code=status, headers=headers, media_type=mime)


@router.get('/media/{asset_id}/preview')
@router.head('/media/{asset_id}/preview', include_in_schema=False)
def preview(scene_id: str, asset_id: str, request: Request):
    from packages.sop.preview import preview_video
    from packages.config.errors import ContractError
    service=request.app.state.sop
    path,asset=service.media(scene_id,asset_id)
    if asset['kind']!='video':
        raise ContractError('PREVIEW_MEDIA_KIND','兼容预览仅支持已登记的视频')
    result,metadata=preview_video(path,asset['sha256'],service.bundle,service.settings)
    return FileResponse(result,media_type='video/mp4',headers={
        'ETag':f'"{metadata["preview_sha256"]}"','Cache-Control':'private, no-cache',
        'X-Original-SHA256':metadata['source_sha256'],
        'X-Media-Lineage':'derived-for-local-viewing-only'})
