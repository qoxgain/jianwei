"""No guessed cameras, dates or sessions. Unknown views remain isolated by bytes."""
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash


def view_identity(source, source_hash, real):
    identity = getattr(source, 'capture_identity', None)
    if identity and identity.review_status == 'confirmed':
        return content_hash({'contract': 'physical-view-v1', 'physical_camera_id': identity.physical_camera_id,
                             'stable_view_id': identity.stable_view_id})
    if real:
        return content_hash({'contract': 'unconfirmed-view-v1', 'source_hash': source_hash, 'logical_source': source.id})
    return content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy})


def roi_identity(source):
    identity = getattr(source, 'capture_identity', None)
    if identity and identity.review_status == 'confirmed':
        return content_hash({'view': view_identity(source, '', True), 'roi_xyxy': source.roi_xyxy,
                             'coordinates': 'normalized_display_after_rotation_and_sar'})
    return content_hash({'source_id': source.id, 'roi_xyxy': source.roi_xyxy})


def reviewed_intervals(request, domains):
    result = []
    if any(v.domain_index >= len(domains) for v in request.view_intervals):
        raise ContractError('VIEW_INTERVAL_DOMAIN', '视角审查引用未知时间域')
    for domain in domains:
        cursor = domain['start_ms']
        items = sorted((v for v in request.view_intervals if v.domain_index == domain['domain_index']), key=lambda v:v.start_ms)
        for item in items:
            if item.start_ms < cursor or item.end_ms > domain['end_ms']:
                raise ContractError('VIEW_INTERVAL_RANGE', '视角审查区间重叠或超过原始时间域')
            if item.start_ms > cursor:
                result.append({'domain_index': domain['domain_index'], 'start_ms':cursor,'end_ms':item.start_ms,
                               'status':'unreviewed','reason':'no_human_view_review','reviewer':None})
            result.append(item.model_dump());cursor=item.end_ms
        if cursor < domain['end_ms']:
            result.append({'domain_index':domain['domain_index'],'start_ms':cursor,'end_ms':domain['end_ms'],
                           'status':'unreviewed','reason':'no_human_view_review','reviewer':None})
    return result


def feature_window(episode):
    return bool(episode and (episode['complete'] or (
        episode.get('observation_contract') == 'p033-observation-v1' and
        episode.get('kind') == 'observation_window' and episode.get('feature_eligible') is True)))
