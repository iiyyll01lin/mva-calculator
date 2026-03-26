import { describe, expect, it } from 'vitest';
import { parseCsv, toCsv } from '../../src/domain/csv';

describe('csv utilities', () => {
  it('roundtrips quoted fields', () => {
    const rows = [
      ['name', 'note'],
      ['fixture', 'hello, world'],
      ['tester', 'quote "inside" value'],
    ];
    const csv = toCsv(rows);
    expect(parseCsv(csv)).toEqual(rows);
  });
});
