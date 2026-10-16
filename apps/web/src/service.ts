import labelsYaml from '../../../knowledge/labels.yaml?raw';
import systemYaml from '../../../config/system.yaml?raw';
import { contentHash, knowledgeDiff, parseYaml, previewKnowledge, systemValidate } from './validation';
import type { Bootstrap, Knowledge, Preview, Profile, Version } from './types';

declare const __DEMO_BUILD__: boolean;
export const isDemo = __DEMO_BUILD__ || location.protocol === 'file:' || new URLSearchParams(location.search).get('demo') === '1';
const base = parseYaml(labelsYaml) as Knowledge;
const config = parseYaml(systemYaml) as { profiles: Record<string, { mode: string }>; active_profile: string };
const storageKey = 'jianwei.p0.1.candidates.v1';
const baseHash = contentHash(base);
const blankDiff = knowledgeDiff(base, base);
const baseline: Version = { id: 'baseline', hash: baseHash, version: base.knowledge_version, status: 'baseline', base_hash: null, created_at: '', diff: blankDiff, yaml: labelsYaml };
function stored(): Version[] {
  const value = localStorage.getItem(storageKey);
  if (!value) return [];
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error('本机候选记录无法读取。请先导出编辑内容，再清理此站点的浏览器存储。'); }
  if (!Array.isArray(parsed) || parsed.some(v => !v || typeof v.id !== 'string' || typeof v.yaml !== 'string' || v.status !== 'candidate' || !v.diff || !Array.isArray(v.diff.changed))) throw new Error('本机候选记录格式不兼容。请保留导出内容后清理此站点存储。');
  return parsed as Version[];
}
async function request<T>(path: string, data?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/${path}`, { method: data ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined, signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail?.message || (typeof result.detail === 'string' ? result.detail : `请求失败 (${response.status})`));
    return result as T;
  } catch (e) {
    if (e instanceof SyntaxError) throw new Error('API 未返回有效数据，请检查后端服务是否已启动。');
    if (e instanceof Error && e.name === 'AbortError') throw new Error('后端服务请求超时，请检查服务状态。');
    throw e;
  } finally { clearTimeout(timer); }
}
const profileNames: Record<string, string> = { demo: '演示环境', lab: '实验环境', site: '现场环境' };
function localProfile(id: string): Profile {
  if (!config.profiles[id]) throw new Error('环境配置不存在');
  const valid = Boolean(systemValidate(config));
  return {
    id, name: profileNames[id] ?? id, mode: config.profiles[id].mode, static_valid: valid, runtime_ready: false,
    checks: [
      { code: 'schema', status: valid ? 'passed' : 'blocked', message: valid ? '随附 system.yaml 通过严格字段 Schema 校验' : '随附配置未通过 Schema 校验' },
      { code: 'runtime', status: 'not_run', message: '浏览器演示无法检查服务、设备、环境变量和凭据' },
      { code: 'data', status: 'not_run', message: '未接入真实视频、特征包或模型权重' },
    ],
    blockers: [{ code: 'offline', message: '仅静态检查；运行就绪状态需要连接后端验证。' }],
    config_hash: contentHash(config), checked_at: new Date().toISOString(),
  };
}
export async function bootstrap(): Promise<Bootstrap> {
  if (!isDemo) return request('bootstrap');
  return {
    project: { name: '见微', version: '3.0', stage: 'P0.1', title: '多模态自学习感知平台' },
    active_profile: config.active_profile, config_hash: contentHash(config), profiles: Object.keys(config.profiles).map(localProfile),
    knowledge: { yaml: labelsYaml, hash: baseHash, version: base.knowledge_version, data: base },
    versions: [baseline, ...stored()], capabilities: { configuration: true, knowledge_preview: true, knowledge_versioning: true, video_ingest: false, training: false, live_inference: false, authentication: false }, blockers: [],
  };
}
export const validateProfile = async (profile: string): Promise<Profile> => isDemo ? localProfile(profile) : request('config/validate', { profile });
export const preview = async (yaml: string): Promise<Preview> => isDemo ? previewKnowledge(yaml, base) : request('knowledge/preview', { yaml });
export async function saveCandidate(yaml: string, base_hash: string): Promise<{ created: boolean; version: Version }> {
  if (!isDemo) return request('knowledge/versions', { yaml, base_hash });
  if (base_hash !== baseHash) throw new Error('基线版本已变化，请重新载入后再预览。');
  const result = previewKnowledge(yaml, base);
  if (!result.valid || !result.data || !result.hash || !result.diff) throw new Error('校验未通过，无法保存候选。');
  const previous = stored();
  const exists = [baseline, ...previous].find(v => v.hash === result.hash);
  if (exists) return { created: false, version: exists };
  const version: Version = { id: `local-${result.hash.slice(0, 12)}`, hash: result.hash, version: result.data.knowledge_version, status: 'candidate', base_hash: baseHash, created_at: new Date().toISOString(), diff: result.diff, yaml };
  localStorage.setItem(storageKey, JSON.stringify([...previous, version]));
  return { created: true, version };
}
export async function versions(): Promise<Version[]> { return isDemo ? [baseline, ...stored()] : (await request<{ versions: Version[] }>('knowledge/versions')).versions; }
export async function versionDetails(id: string): Promise<Version> {
  if (!isDemo) return (await request<{ version: Version }>(`knowledge/versions/${encodeURIComponent(id)}`)).version;
  const found = [baseline, ...stored()].find(v => v.id === id);
  if (!found) throw new Error('未找到该知识版本。');
  return found;
}
export function downloadText(name: string, value: string, mime = 'text/yaml;charset=utf-8') {
  const objectUrl = URL.createObjectURL(new Blob([value], { type: mime }));
  const link = document.createElement('a'); link.href = objectUrl; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
