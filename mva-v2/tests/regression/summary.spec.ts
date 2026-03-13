import { describe, expect, it } from 'vitest';
import { buildSummaryCsv } from '../../src/domain/exporters';
import { defaultProject } from '../../src/domain/defaults';

describe('summary regression', () => {
  it('matches the release-candidate summary snapshot', () => {
    expect(buildSummaryCsv(defaultProject)).toMatchInlineSnapshot(
      `"Section,Item,Value
Simulation,Model,1395A3368201
Simulation,UPH,6.4156
Simulation,Takt Time,265.2
Simulation,Weekly Output,2552.1257
MVA,Monthly Volume,10000
MVA,Line UPH Raw,6.4156
MVA,Line UPH Used,6.4156
MVA,Direct Labor / Unit,104.7514
MVA,Indirect Labor / Unit,52.3962
MVA,Equipment Depreciation / Unit,1.2833
MVA,Equipment Maintenance / Unit,0.1283
MVA,Space / Unit,4.16
MVA,Power / Unit,0.28
MVA,Material Attrition / Unit,0.54
MVA,Consumables / Unit,0.8
MVA,SGA / Unit,4.05
MVA,Profit / Unit,3.6
MVA,ICC / Unit,0.6
MVA,Total / Unit,172.5892
Confirmation,Decision,OK
Confirmation,Reviewer,System"`,
    );
  });
});
