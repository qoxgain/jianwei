"""Append-only normative draft review. No activation or automatic compliance verdicts."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import subprocess
from threading import RLock

from sqlalchemy import select, text
from infra.models import AuditEvent
from packages.config.errors import ContractError
from packages.config.loader import resolve_path
from packages.knowledge.service import content_hash


def fail(code, message):
    raise ContractError(code, message)


def fingerprint(path):
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class SopService:
    def __init__(self, bundle, database):
        self.bundle, self.database = bundle, database
        self.settings = getattr(bundle.config, 'sop_review', None)
        self.tenant = bundle.profile.tenant_id
        self._lock, self._cache, self._verified = RLock(), None, {}

    def check_scene(self, scene):
        if scene not in self.bundle.scenes:
            fail('SCENE_NOT_FOUND', '场景不存在')
        if self.settings is None or scene != self.settings.scene_id:
            fail('SOP_NOT_CONFIGURED', '当前场景没有注册待复核 SOP')

    def _path(self, value):
        root = resolve_path(self.bundle.root, self.settings.asset_root)
        path = resolve_path(root, value)
        if not path.is_relative_to(root):
            fail('SOP_ASSET_PATH', 'SOP 资产路径必须位于配置的素材目录内')
        if not path.is_file():
            fail('SOP_ASSET_MISSING', '已注册的 SOP 资产尚未就绪，请检查文件或 Git LFS 实体')
        return path

    def _json(self, path):
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            fail('SOP_REGISTRY_INVALID', 'SOP 素材登记文件无法读取')

    def _verify(self, path, asset):
        stamp = fingerprint(path)
        if stamp[3] != asset['bytes']:
            fail('SOP_ASSET_IDENTITY', '已注册资产字节数不一致，可能仍为 Git LFS 指针')
        with self._lock:
            if self._verified.get(stamp) == asset['sha256']:
                return
        digest = hashlib.sha256()
        with path.open('rb') as source:
            first = source.read(1048576)
            if first.startswith(b'version https://git-lfs.github.com/spec/v1'):
                fail('SOP_ASSET_IDENTITY', '请先获取 Git LFS 视频实体')
            digest.update(first)
            for chunk in iter(lambda: source.read(1048576), b''):
                digest.update(chunk)
        if digest.hexdigest() != asset['sha256'] or fingerprint(path) != stamp:
            fail('SOP_ASSET_IDENTITY', '已注册资产内容发生变化，不能沿用旧引用')
        with self._lock:
            self._verified[stamp] = asset['sha256']

    def _extract(self, source):
        try:
            version = subprocess.run(['pdftotext', '-v'], capture_output=True, text=True,
                                     timeout=self.settings.text_timeout_seconds, check=True)
            result = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', '-eol', 'unix',
                                     str(source), '-'], capture_output=True, text=True,
                                    timeout=self.settings.text_timeout_seconds, check=True)
        except (OSError, subprocess.SubprocessError):
            fail('SOP_EXTRACTION_UNAVAILABLE', 'SOP 原文提取失败或超时，请检查 pdftotext 依赖和文档')
        pages = result.stdout.split('\f')
        if pages and not pages[-1]:
            pages.pop()
        extractor = (version.stderr or version.stdout).splitlines()[0]
        return pages, f'{extractor};layout;UTF-8;unix;unicode-codepoint-offsets-v1'

    def _load(self, scene):
        self.check_scene(scene)
        with self._lock:
            catalog_path = self._path(self.settings.catalog_path)
            outline_path = self._path(self.settings.outline_path)
            mapping_path = self._path(self.settings.mapping_path)
            catalog, outline, mapping = (self._json(p) for p in (catalog_path, outline_path, mapping_path))
            try:
                source_path = self._path(outline['source']['path'])
                stamp = tuple(fingerprint(p) for p in (catalog_path, outline_path, mapping_path, source_path))
                if self._cache and self._cache['stamp'] == stamp:
                    return self._cache
                by_id = {a['asset_id']: a for a in catalog['assets']}
                if len(by_id) != len(catalog['assets']):
                    fail('SOP_REGISTRY_INVALID', '素材标识重复')
                source_asset = next(a for a in catalog['assets'] if a['path'] == outline['source']['path'])
                if source_asset['kind'] != 'normative_document' or source_asset['sha256'] != outline['source']['sha256'] or mapping['source_sop_sha256'] != source_asset['sha256']:
                    fail('SOP_ASSET_IDENTITY', 'SOP 原文、章节和工位映射引用不一致')
                self._verify(source_path, source_asset)
                pages, extractor = self._extract(source_path)
                if len(pages) != outline['source']['pdf_pages']:
                    fail('SOP_PAGE_COUNT', '实际 PDF 页数与章节目录不一致')
                sections = copy.deepcopy(outline['sections'])
                if len({section['document_id'] for section in sections}) != len(sections):
                    fail('SOP_REGISTRY_INVALID', '章节标识重复')
                for section in sections:
                    section['raw_references'] = []
                    for page in section['pdf_pages']:
                        if not 1 <= page <= len(pages):
                            fail('SOP_PAGE_RANGE', '章节引用超过实际 PDF 页数')
                        raw = pages[page-1]
                        section['raw_references'].append({'source_sha256': source_asset['sha256'],
                            'pdf_page': page, 'text_start': 0, 'text_end': len(raw), 'text': raw,
                            'text_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'extraction_version': extractor})
                    raw = '\n'.join(ref['text'] for ref in section['raw_references'])
                    instruments = [term for term in ('扭矩', '扭力', '绝缘', '耐压', '安规', '电性能', '老化') if term in raw or term in section['title']]
                    section['evidence_classification'] = {'visible_operation': 'requires_coverage_and_human_review',
                        'closeup_detail': 'requires_closeup_or_human_review', 'instrument_required': bool(instruments),
                        'instrument_reason': ' / '.join(instruments) or None,
                        'classification_status': 'conservative_draft_requires_process_owner_review'}
                entries = [{'asset_id': e['asset_id'], 'section_ids': e['candidate_sop_document_ids'],
                            'notes': e.get('coarse_visual_observation', '')} for e in mapping['entries']]
                for original in mapping['entries']:
                    if original['source_sha256'] != by_id[original['asset_id']]['sha256']:
                        fail('SOP_ASSET_IDENTITY', '工位候选映射的录像 SHA 与目录不一致')
                selected = [by_id[e['asset_id']] for e in entries] + [source_asset]
                if any(a['kind'] != 'video' or a.get('domain') != source_asset.get('domain') for a in selected[:-1]):
                    fail('SOP_REGISTRY_INVALID', '映射引用了非当前 SOP 领域的录像')
                media_prefix = f'/api/scenes/{scene}/sop/media/'
                root = {'source': {**outline['source'], 'asset_id': source_asset['asset_id'],
                                  'media_url': media_prefix + source_asset['asset_id'], 'extraction_version': extractor},
                        'sections': sections, 'mappings': entries,
                        'disputes': [item['finding'] for item in outline.get('review_items', [])],
                        'review_items': outline.get('review_items', []), 'product': outline.get('product', {}),
                        'evidence_policy': outline['evidence_policy'], 'limitations': mapping.get('limitations', []),
                        'assets': [{**a, 'media_url': media_prefix + a['asset_id'], **({'preview_url': media_prefix + a['asset_id'] + '/preview'} if a['kind']=='video' and self.settings.preview_enabled else {})} for a in selected],
                        'candidate_source': mapping.get('label_source'),
                        'initial_mapping_candidates': copy.deepcopy(mapping['entries']),
                        'status': 'draft', 'activated': False, 'is_ground_truth': False,
                        'compliance_result': 'not_evaluated'}
                scope = content_hash({'contract': 'sop-review-v1', 'tenant': self.tenant, 'scene': scene,
                                      'config_hash': self.bundle.config_hash, 'root': root})
                root['revision'] = scope
                self._validate_mappings(root, entries)
                self._cache = {'stamp': stamp, 'root': root, 'scope': scope, 'assets': {a['asset_id']: a for a in selected}}
                return self._cache
            except (KeyError, TypeError, StopIteration):
                fail('SOP_REGISTRY_INVALID', 'SOP 素材登记缺少必要字段')

    def _events(self, session, scene, scope):
        rows = session.scalars(select(AuditEvent).where(AuditEvent.tenant_id == self.tenant,
            AuditEvent.scene_id == scene, AuditEvent.entity_id == scope,
            AuditEvent.event_type.in_(['sop.revision', 'sop.decision', 'sop.evidence']))).all()
        return sorted([{'id': row.id, 'event_type': row.event_type, 'created_at': row.created_at.isoformat(),
                        **json.loads(row.payload_json)} for row in rows], key=lambda e: e['sequence'])

    def _state(self, loaded, events):
        state = copy.deepcopy(loaded['root'])
        evidence = []
        for event in events:
            if event['event_type'] == 'sop.revision':
                state.update({key: event[key] for key in ('revision', 'mappings', 'disputes')})
                state['status'] = 'draft'
            elif event['event_type'] == 'sop.decision' and event['revision'] == state['revision']:
                state['status'] = 'approved' if event['action'] == 'approve' else 'revoked'
            elif event['event_type'] == 'sop.evidence':
                evidence.append(event)
        state['audit'] = events
        state['evidence'] = [e for e in evidence if e['revision'] == state['revision']]
        state['review_scope'] = 'Local human declarations; reviewer identity and instrument references are not authenticated'
        return state

    def get(self, scene):
        loaded = self._load(scene)
        with self.database.session() as session:
            return self._state(loaded, self._events(session, scene, loaded['scope']))

    def _validate_mappings(self, state, mappings):
        expected = {a['asset_id'] for a in state['assets'] if a['kind'] == 'video'}
        valid_sections = {s['document_id'] for s in state['sections']}
        actual = [m['asset_id'] for m in mappings]
        if set(actual) != expected or len(actual) != len(expected):
            fail('SOP_MAPPING_ASSETS', '每个注册录像应且只应有一条映射；待确认章节可保持空列表')
        for item in mappings:
            if not set(item['section_ids']) <= valid_sections or len(item['section_ids']) != len(set(item['section_ids'])):
                fail('SOP_MAPPING_SECTIONS', '映射包含未知或重复章节')

    def _write(self, scene, request, kind):
        loaded = self._load(scene)
        with self.database.session() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            events = self._events(session, scene, loaded['scope'])
            state = self._state(loaded, events)
            payload = request.model_dump(mode='json')
            base = payload.get('base_revision', payload.get('revision'))
            if base != state['revision']:
                fail('BASE_HASH_CONFLICT', 'SOP 复核版本已变化，请刷新后重新审核')
            if kind == 'revision':
                self._validate_mappings(state, payload['mappings'])
                payload['revision'] = content_hash({'scope': loaded['scope'], **payload})
            elif kind == 'decision':
                if payload['action'] == 'approve' and state['disputes']:
                    fail('SOP_DISPUTES_PENDING', '当前草稿仍有待核实争议，请记录处理依据后保存新版本再批准')
                if payload['action'] == 'revoke' and state['status'] != 'approved':
                    fail('SOP_DECISION_STATE', '仅已批准的映射版本可撤销')
                if payload['action'] == 'approve' and state['status'] == 'approved':
                    fail('SOP_DECISION_STATE', '当前映射已经批准')
            else:
                self._validate_evidence(state, payload)
            payload['sequence'] = len(events) + 1
            payload['source_sha256'] = state['source']['sha256']
            payload['config_hash'] = self.bundle.config_hash
            payload['activated'] = False
            payload['is_ground_truth'] = False
            session.add(AuditEvent(tenant_id=self.tenant, scene_id=scene, entity_id=loaded['scope'],
                event_type=f'sop.{kind}', payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            session.flush()
            return self._state(loaded, self._events(session, scene, loaded['scope']))

    def revise(self, scene, request):
        return self._write(scene, request, 'revision')

    def decide(self, scene, request):
        return self._write(scene, request, 'decision')

    def record_evidence(self, scene, request):
        return self._write(scene, request, 'evidence')

    def _validate_evidence(self, state, payload):
        asset = next((a for a in state['assets'] if a['asset_id'] == payload['asset_id'] and a['kind'] == 'video'), None)
        section = next((s for s in state['sections'] if s['document_id'] == payload['section_id']), None)
        if not asset or not section:
            fail('SOP_EVIDENCE_REFERENCE', '证据需要引用已注册的录像与章节')
        if payload['end_ms'] > round(asset['media']['duration_seconds'] * 1000):
            fail('SOP_EVIDENCE_RANGE', '证据区间不能超过原始录像时长')
        instrument = section['evidence_classification']['instrument_required']
        if payload['evidence_type'] == 'instrument_record':
            payload['result'] = 'pending_instrument_review' if payload.get('evidence_reference') else 'insufficient_evidence'
        elif instrument:
            payload['result'] = 'insufficient_evidence'
        else:
            payload['result'] = 'pending_human_review'
        payload['compliance_result'] = 'not_evaluated'
        payload['source_video_sha256'] = asset['sha256']
        payload['document_sha256'] = state['source']['sha256']
        payload['evidence_policy'] = 'No instrument authentication, automatic compliance decision or training-label promotion'

    def media(self, scene, identifier):
        loaded = self._load(scene)
        asset = loaded['assets'].get(identifier)
        if asset is None:
            fail('SOP_ASSET_NOT_FOUND', '当前场景没有注册此媒体资产')
        path = self._path(asset['path'])
        self._verify(path, asset)
        return path, asset
