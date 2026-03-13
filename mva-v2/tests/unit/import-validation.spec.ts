import { describe, expect, it } from 'vitest';
import { parseEquipmentRows, parseLaborRows, validateLineStandardsPayload, validateProjectPayload } from '../../src/App';
import { defaultProject } from '../../src/domain/defaults';

describe('import validation', () => {
  it('rejects malformed project payloads', () => {
    expect(() => validateProjectPayload({ basicInfo: {} })).toThrow(/expected project structure/i);
    expect(validateProjectPayload(defaultProject)).toMatchObject({ schemaVersion: 1 });
  });

  it('rejects malformed line standard payloads', () => {
    expect(() => validateLineStandardsPayload({})).toThrow(/must be an array/i);
    expect(() => validateLineStandardsPayload([{ bad: true }])).toThrow(/malformed/i);
  });

  it('rejects equipment csv with missing required headers', () => {
    expect(() => parseEquipmentRows('item,qty\nfixture,2')).toThrow(/missing required headers/i);
  });

  it('rejects labor csv with missing required headers', () => {
    expect(() => parseLaborRows('name,headcount\nAssembly,2')).toThrow(/missing required headers/i);
  });
});
