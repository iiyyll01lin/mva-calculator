import { describe, expect, it } from 'vitest';
import { parseEquipmentRows, parseLaborRows, validateLineStandardsPayload, validateProjectPayload } from '../../src/App';
import { parseL6LaborTimeEstimationCsv } from '../../src/domain/importers';
import { defaultProject } from '../../src/domain/defaults';

describe('import validation', () => {
  it('rejects malformed project payloads', () => {
    expect(() => validateProjectPayload({ basicInfo: {} })).toThrow(/expected project structure/i);
    expect(validateProjectPayload(defaultProject)).toMatchObject({ schemaVersion: 2 });
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

describe('L6 multi-segment CSV import', () => {
  const makeL6Csv = (rows: string[]) => ['name,cycletimesec,laborhc', ...rows].join('\n');

  it('parses a single-segment CSV (no total row) into one segment', () => {
    const csv = makeL6Csv(['Assembly,10,2', 'Test,20,1']);
    const result = parseL6LaborTimeEstimationCsv(csv);
    expect(result.segments).toHaveLength(1);
    expect(result.segments[0].stations).toHaveLength(2);
  });

  it('splits on "total" rows into two segments', () => {
    const csv = makeL6Csv(['Assembly,10,2', 'total,0,0', 'Packaging,15,1', 'total,0,0']);
    const result = parseL6LaborTimeEstimationCsv(csv);
    expect(result.segments).toHaveLength(2);
    expect(result.segments[0].stations).toHaveLength(2); // Assembly + total row
    expect(result.segments[1].stations).toHaveLength(2); // Packaging + total row
  });

  it('names first segment using meta.segmentname and auto-names subsequent segments', () => {
    const csv = 'segmentname,SMT Line\nname,cycletimesec,laborhc\nSolder,5,1\ntotal,0,0\nPackage,3,1\ntotal,0,0';
    const result = parseL6LaborTimeEstimationCsv(csv);
    expect(result.segments).toHaveLength(2);
    expect(result.segments[0].name).toBe('SMT Line');
    expect(result.segments[1].name).toBe('Segment 2');
  });

  it('preserves all stations in flat .stations array', () => {
    const csv = makeL6Csv(['A,10,1', 'total,0,0', 'B,20,1']);
    const result = parseL6LaborTimeEstimationCsv(csv);
    expect(result.stations).toHaveLength(3);
  });
});
