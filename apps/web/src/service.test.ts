import { beforeEach, describe, expect, it, vi } from 'vitest';
import { dumpYaml } from './validation';

let storage = new Map<string, string>();
beforeEach(() => {
  vi.resetModules(); storage = new Map();
  vi.stubGlobal('location', { protocol: 'file:', search: '' });
  vi.stubGlobal('localStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
});

describe('explicit browser demo candidate persistence', () => {
  it('saves, deduplicates and restores a candidate without activating it', async () => {
    const service = await import('./service');
    const original = await service.bootstrap();
    const candidate = structuredClone(original.knowledge.data);
    candidate.actions[0].name = '测试候选';
    const yaml = dumpYaml(candidate);
    const saved = await service.saveCandidate(yaml, original.knowledge.hash);
    expect(saved.created).toBe(true);
    expect(saved.version.status).toBe('candidate');
    expect((await service.saveCandidate(yaml, original.knowledge.hash)).created).toBe(false);
    const after = await service.bootstrap();
    expect(after.knowledge.hash).toBe(original.knowledge.hash);
    expect(after.knowledge.data.actions[0].name).toBe(original.knowledge.data.actions[0].name);
    expect(after.versions).toHaveLength(2);
    expect((await service.versionDetails(saved.version.id)).yaml).toBe(yaml);
    vi.resetModules();
    expect((await (await import('./service')).versions())).toHaveLength(2);
  });
  it('rejects a stale baseline and invalid candidate before writing storage', async () => {
    const service = await import('./service');
    const data = await service.bootstrap();
    await expect(service.saveCandidate(data.knowledge.yaml, 'stale')).rejects.toThrow('基线版本');
    await expect(service.saveCandidate('invalid: true', data.knowledge.hash)).rejects.toThrow('校验未通过');
    expect(storage.size).toBe(0);
  });
  it('does not convert browser diagnostics into runtime readiness', async () => {
    const service = await import('./service');
    const result = await service.validateProfile('demo');
    expect(result.static_valid).toBe(true);
    expect(result.runtime_ready).toBe(false);
    expect(result.checks.some(c => c.status === 'not_run')).toBe(true);
  });
});
