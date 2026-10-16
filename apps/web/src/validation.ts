import Ajv2020 from 'ajv/dist/2020';
import { parseDocument, stringify } from 'yaml';
import { sha256 } from 'js-sha256';
import labelsSchema from '../../../schemas/labels.schema.json';
import systemSchema from '../../../schemas/system.schema.json';
import type { Diff, Knowledge, Preview } from './types';

const ajv = new Ajv2020({ allErrors: true, strict: false });
const schemaValidate = ajv.compile(labelsSchema);
// Form editing checks structural safety only. Incomplete names or invalid SOP
// relationships must remain editable while full validation blocks candidate save.
function formSchema(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(formSchema);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => !['minLength', 'maxLength', 'minItems', 'maxItems', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'].includes(key)).map(([key, item]) => [key, key === 'type' && item === 'integer' ? 'number' : formSchema(item)]));
  return value;
}
const formValidate = ajv.compile(formSchema(labelsSchema) as object);
export const systemValidate = ajv.compile(systemSchema);
export const dumpYaml = (value: unknown) => stringify(value, { lineWidth: 0 });
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical((value as Record<string, unknown>)[key])).join(',') + '}';
  return JSON.stringify(value);
}
export const contentHash = (value: unknown) => sha256(canonical(value));
export function parseYaml(source: string): unknown {
  if (new TextEncoder().encode(source).length > 256 * 1024) throw new Error('YAML 文件不能超过 256 KiB');
  const document = parseDocument(source, { uniqueKeys: true });
  if (document.errors.length) throw new Error(document.errors.map(e => e.message).join('\n'));
  const value = document.toJS({ maxAliasCount: 0 });
  function checkCanonical(input: unknown): void {
    if (typeof input === 'number' && (!Number.isFinite(input) || (Number.isInteger(input) && !Number.isSafeInteger(input)))) throw new Error('数字必须有限，整数不能超过 JavaScript 安全整数范围');
    if (typeof input === 'string') for (const char of input) { const point = char.codePointAt(0)!; if (point >= 0xd800 && point <= 0xdfff) throw new Error('文本包含无效 Unicode 字符'); }
    if (Array.isArray(input)) input.forEach(checkCanonical);
    else if (input !== null && typeof input === 'object') for (const [key, item] of Object.entries(input)) { checkCanonical(key); checkCanonical(item); }
  }
  checkCanonical(value);
  return value;
}
export function editableKnowledge(source: string): Knowledge | null {
  try { const value = parseYaml(source); return formValidate(value) ? value as Knowledge : null; } catch { return null; }
}
export function knowledgeDiff(base: Knowledge, next: Knowledge): Diff {
  const old = new Map(base.actions.map(a => [a.id, a]));
  const fresh = new Map(next.actions.map(a => [a.id, a]));
  return {
    added: [...fresh.keys()].filter(id => !old.has(id)),
    removed: [...old.keys()].filter(id => !fresh.has(id)),
    changed: next.actions.filter(a => old.has(a.id)).map(a => ({ id: a.id, fields: Object.keys(a).filter(key => canonical(a[key as keyof typeof a]) !== canonical(old.get(a.id)![key as keyof typeof a])) })).filter(a => a.fields.length),
    sop_changed: canonical(base.sop) !== canonical(next.sop),
    metadata_changed: ['knowledge_version', 'scene_id', 'approval_status', 'approved_by'].filter(key => canonical(base[key as keyof Knowledge]) !== canonical(next[key as keyof Knowledge])),
  };
}
export function previewKnowledge(source: string, base: Knowledge): Preview {
  const errors: Preview['errors'] = [];
  const add = (path: string, message: string) => errors.push({ path, message });
  const failed = (): Preview => ({ valid: false, errors, hash: null, diff: null, impact: [], data: null });
  let raw: unknown;
  try { raw = parseYaml(source); } catch (error) { add('yaml', (error as Error).message); return failed(); }
  if (!schemaValidate(raw)) {
    for (const e of schemaValidate.errors ?? []) add(e.instancePath || '/', `${e.message}${e.params.additionalProperty ? '：' + e.params.additionalProperty : ''}${e.params.missingProperty ? '：' + e.params.missingProperty : ''}`);
    return failed();
  }
  const data = raw as Knowledge;
  const ids = new Set<string>();
  for (const a of data.actions) {
    if (!a.id.trim() || !a.name.trim()) add(`actions/${a.id}`, '动作 ID 和名称不能为空');
    if (ids.has(a.id)) add(`actions/${a.id}`, '动作 ID 重复');
    ids.add(a.id);
  }
  if (!data.knowledge_version.trim()) add('knowledge_version', '知识版本不能为空');
  if (data.scene_id !== base.scene_id) add('scene_id', '场景 ID 必须与当前运行场景一致');
  for (const a of data.actions) {
    for (const ref of a.confusable_with) if (!ids.has(ref)) add(`actions/${a.id}/confusable_with`, `动作引用不存在：${ref}`);
    const d = a.duration_prior_seconds;
    if (d !== null && (d.length !== 2 || d[0] < 0 || d[0] > d[1])) add(`actions/${a.id}/duration_prior_seconds`, '时间范围须包含两个非负数字，且上限不小于下限');
    for (const ref of [...a.positive_examples, ...a.negative_examples]) add(`actions/${a.id}/examples`, `样本尚未登记：${ref}。本步骤尚未接入视频样本库。`);
    for (const ref of a.prototype_refs) add(`actions/${a.id}/prototype_refs`, `原型尚未登记：${ref}`);
  }
  const nodes = new Set<string>();
  for (const n of data.sop.nodes) {
    if (nodes.has(n.action_id) || !ids.has(n.action_id)) add('sop/nodes', `SOP 节点重复或不存在：${n.action_id}`);
    if (n.min_count > n.max_count) add(`sop/nodes/${n.action_id}`, '最少次数不能大于最多次数');
    nodes.add(n.action_id);
  }
  const graph = new Map([...nodes].map(id => [id, [] as string[]]));
  for (const edge of [...data.sop.edges, ...data.sop.repeatable_edges]) if (!nodes.has(edge.from_action) || !nodes.has(edge.to_action)) add('sop/edges', `边引用不存在：${edge.from_action} → ${edge.to_action}`);
  for (const edge of data.sop.edges) graph.get(edge.from_action)?.push(edge.to_action);
  const visiting = new Set<string>(), visited = new Set<string>();
  let cycle = false;
  function visit(id: string) { if (visiting.has(id)) { cycle = true; return; } if (visited.has(id)) return; visiting.add(id); for (const target of graph.get(id) ?? []) visit(target); visiting.delete(id); visited.add(id); }
  for (const id of nodes) visit(id);
  if (cycle) add('sop/edges', '存在未声明为 repeatable_edges 的 SOP 环路');
  if (data.sop.ordering === 'unordered' && data.sop.edges.length) add('sop/edges', '无序流程不得包含顺序边，请在 YAML 中明确移除');
  if (data.approval_status === 'approved' && !data.approved_by?.trim()) add('approved_by', '已批准的知识包必须填写批准人');
  for (const op of data.curated_memory) {
    for (const ref of [op.action_id, op.related_action_id]) if (ref && !ids.has(ref)) add('curated_memory', `动作引用不存在：${ref}`);
    add('curated_memory', `尚未登记的样本或簇：${op.target_id}。本步骤尚未接入机器记忆。`);
  }
  if (errors.length) return failed();
  const diff = knowledgeDiff(base, data);
  const changed = diff.added.length + diff.removed.length + diff.changed.length;
  const impact = ['当前会话继续绑定原知识快照；候选保存不会激活或覆盖基线。'];
  if (changed) impact.push('动作定义发生变更；接入真实样本后需重建受影响原型并重新评估。');
  if (diff.sop_changed) impact.push('SOP 规范发生变更；需要业务复核和流程回归评估。');
  impact.push('已登记真实样本：0；当前没有训练或推理效果可供评估。');
  return { valid: true, errors: [], hash: contentHash(data), diff, impact, data };
}
