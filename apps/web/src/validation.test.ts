import { describe, expect, it } from 'vitest';
import labelsYaml from '../../../knowledge/labels.yaml?raw';
import { contentHash, dumpYaml, editableKnowledge, parseYaml, previewKnowledge } from './validation';
import type { Knowledge } from './types';

const baseline = parseYaml(labelsYaml) as Knowledge;
const copy = () => structuredClone(baseline);
const check = (value: Knowledge) => previewKnowledge(dumpYaml(value), baseline);

describe('knowledge candidate validation', () => {
  it('accepts the shipped baseline without inventing a change', () => {
    const result = previewKnowledge(labelsYaml, baseline);
    expect(result.valid).toBe(true);
    expect(result.diff).toEqual({ added: [], removed: [], changed: [], sop_changed: false, metadata_changed: [] });
    expect(result.hash).toBe(contentHash(baseline));
  });
  it('reports a renamed action without changing its stable identity', () => {
    const next = copy(); next.actions[0].name = '新的业务名称';
    const result = check(next);
    expect(result.valid).toBe(true);
    expect(result.diff?.changed).toEqual([{ id: next.actions[0].id, fields: ['name'] }]);
    expect(result.diff?.added).toEqual([]);
    expect(result.hash).not.toBe(contentHash(baseline));
  });
  it('keeps temporarily blank fields editable while blocking save', () => {
    const next = copy(); next.actions[0].name = '';
    expect(check(next).valid).toBe(false);
    expect(editableKnowledge(dumpYaml(next))?.actions[0].name).toBe('');
  });
  it('keeps an invalid SOP editable so users can correct relationships', () => {
    const next = copy(); next.sop.ordering = 'unordered';
    expect(check(next).valid).toBe(false);
    expect(editableKnowledge(dumpYaml(next))?.sop.ordering).toBe('unordered');
    next.sop.edges = [];
    expect(check(next).valid).toBe(true);
  });
  it('rejects accidental fields instead of silently discarding them through the form', () => {
    const next = { ...copy(), misspelled_field: true };
    const yaml = dumpYaml(next);
    expect(previewKnowledge(yaml, baseline).valid).toBe(false);
    expect(editableKnowledge(yaml)).toBeNull();
  });
  it('rejects dangling references and duplicate action identifiers', () => {
    const next = copy(); next.actions[0].confusable_with = ['does_not_exist'];
    expect(check(next).valid).toBe(false);
    next.actions[0].confusable_with = [];
    next.actions.push(structuredClone(next.actions[0]));
    expect(check(next).valid).toBe(false);
  });
  it('rejects undeclared SOP cycles but permits explicit repeatable edges', () => {
    const next = copy();
    const back = { from_action: next.sop.nodes.at(-1)!.action_id, to_action: next.sop.nodes[0].action_id };
    next.sop.edges.push(back);
    expect(check(next).valid).toBe(false);
    next.sop.edges.pop(); next.sop.repeatable_edges.push(back);
    expect(check(next).valid).toBe(true);
  });
  it('blocks sample/prototype claims until registries are available', () => {
    for (const key of ['positive_examples', 'negative_examples', 'prototype_refs'] as const) {
      const next = copy(); next.actions[0][key] = ['example-01'];
      expect(check(next).valid).toBe(false);
    }
    const next = copy(); next.curated_memory.push({ op: 'reject_sample', target_id: 'sample-01', action_id: null, related_action_id: null, reason: '示例' });
    expect(check(next).valid).toBe(false);
  });
  it('rejects approval without a named reviewer and wrong scene IDs', () => {
    const next = copy(); next.approval_status = 'approved'; next.approved_by = '  ';
    expect(check(next).valid).toBe(false);
    next.approval_status = 'draft'; next.scene_id = 'another_scene';
    expect(check(next).valid).toBe(false);
  });
  it('rejects empty actions and negative/inverted duration ranges', () => {
    const next = copy(); next.actions[0].duration_prior_seconds = [10, 2];
    expect(check(next).valid).toBe(false);
    next.actions[0].duration_prior_seconds = [-1, 2];
    expect(check(next).valid).toBe(false);
    next.actions = []; next.sop.nodes = []; next.sop.edges = [];
    expect(check(next).valid).toBe(false);
  });
});

describe('strict, portable YAML input', () => {
  it('rejects duplicate keys, aliases, unsafe numbers and malformed documents', () => {
    for (const source of ['name: one\nname: two', 'a: &value hello\nb: *value', 'n: 9007199254740992', 'n: .nan', 'name: [unfinished', 'name: "\\uD800"']) expect(() => parseYaml(source)).toThrow();
  });
  it('limits input using UTF-8 bytes', () => {
    expect(() => parseYaml('#' + '微'.repeat(90000))).toThrow('256 KiB');
  });
  it('uses YAML 1.2 booleans and preserves date-shaped text', () => {
    expect(parseYaml('name: on\ndescription: 2026-10-16\nflags: [yes, OFF, true]\nn: 012')).toEqual({ name: 'on', description: '2026-10-16', flags: ['yes', 'OFF', true], n: 12 });
  });
  it('ignores formatting and mapping order when identifying a version', () => {
    expect(contentHash(parseYaml('first: 1.0\nsecond: 0.0000001'))).toBe(contentHash(parseYaml('# comment\nsecond: 1e-7\nfirst: 1')));
  });
});
