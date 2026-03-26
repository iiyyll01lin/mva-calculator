import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import { resolveIndirectLaborRows } from '../domain/calculations';
import type { MvaResults, ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { boundedNumber, numberValue, toMoney } from '../utils/formatters';
import { parseMonthYearUpdateCsv } from '../domain/importers';

interface PlantRatesPageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  mva: MvaResults;
  onStatusMessage: (msg: string) => void;
}

export const PlantRatesPage = memo(function PlantRatesPage({ project, updateProject, mva, onStatusMessage }: PlantRatesPageProps) {
  const importMonthYearUpdate = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseMonthYearUpdateCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          mvaRates: {
            ...current.plant.mvaRates,
            directLaborHourlyRate: parsed.directLaborHourlyRate ?? current.plant.mvaRates.directLaborHourlyRate,
            indirectLaborHourlyRate: parsed.indirectLaborHourlyRate ?? current.plant.mvaRates.indirectLaborHourlyRate,
            efficiency: parsed.efficiency ?? current.plant.mvaRates.efficiency,
          },
          overheadByProcess: {
            ...current.plant.overheadByProcess,
            [current.plant.processType]: {
              ...current.plant.overheadByProcess[current.plant.processType],
              equipmentAverageUsefulLifeYears: parsed.equipmentAverageUsefulLifeYears ?? current.plant.overheadByProcess[current.plant.processType].equipmentAverageUsefulLifeYears,
              equipmentMaintenanceRatio: parsed.equipmentMaintenanceRatio ?? current.plant.overheadByProcess[current.plant.processType].equipmentMaintenanceRatio,
              powerCostPerUnit: parsed.powerCostPerUnit ?? current.plant.overheadByProcess[current.plant.processType].powerCostPerUnit,
            },
          },
          spaceSettings: {
            ...current.plant.spaceSettings,
            spaceRatePerSqft: parsed.spaceRatePerSqft ?? current.plant.spaceSettings.spaceRatePerSqft,
          },
        },
      }));
      onStatusMessage(`Imported month/year update CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import month/year update CSV.');
      console.error(error);
    }
  };

  return (
    <div className="stack-xl">
      <SectionCard title="Labor Rate & Efficiency" description="Legacy labor rates, volume, yield strategy, and imported plant-rate assumptions.">
        <div className="action-grid compact-actions">
          <label className="upload-card"><span>Import Month/Year Update CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importMonthYearUpdate(event.target.files?.[0] ?? null)} /></label>
        </div>
        <div className="legacy-page-grid mt-md">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Labor Rates and Control</h3><p className="muted">Direct, indirect, efficiency, process type, and IDL mode.</p></div>
            <div className="form-grid cols-2">
              <label><span>Process Type</span><select value={project.plant.processType} onChange={(event) => updateProject((current) => {
                const processType = event.target.value as ProjectState['plant']['processType'];
                const plant: ProjectState['plant'] = { ...current.plant, processType };
                if (plant.idlUiMode === 'auto') {
                  plant.indirectLaborRows = resolveIndirectLaborRows(plant, 'auto', processType);
                }
                return { ...current, plant };
              })}><option value="L6">L6</option><option value="L10">L10</option></select></label>
              <label><span>IDL Mode</span><select value={project.plant.idlUiMode} onChange={(event) => updateProject((current) => {
                const idlUiMode = event.target.value as ProjectState['plant']['idlUiMode'];
                const plant: ProjectState['plant'] = { ...current.plant, idlUiMode };
                plant.indirectLaborRows = resolveIndirectLaborRows(plant, idlUiMode);
                return { ...current, plant };
              })}><option value="auto">auto</option><option value="L10">L10</option><option value="L6">L6</option></select></label>
              <label><span>Direct Labor - Hourly Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.directLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, directLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
              <label><span>Indirect Labor - Hourly Rate</span><input type="number" step="0.01" value={project.plant.mvaRates.indirectLaborHourlyRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, indirectLaborHourlyRate: numberValue(event.target.value) } } }))} /></label>
              <label className="full-span"><span>Efficiency (0-1)</span><input type="number" step="0.01" value={project.plant.mvaRates.efficiency} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaRates: { ...current.plant.mvaRates, efficiency: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Volume</h3><p className="muted">Monthly and weekly demand assumptions for MVA.</p></div>
            <div className="form-grid cols-2">
              <label><span>Demand Mode</span><select value={project.plant.mvaVolume.demandMode} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, demandMode: event.target.value as 'monthly' | 'weekly' } } }))}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></label>
              <label><span>Work Days / Month</span><input type="number" value={project.plant.mvaVolume.workDaysPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>RFQ Qty / Month</span><input type="number" value={project.plant.mvaVolume.rfqQtyPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, rfqQtyPerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>Weekly Demand</span><input type="number" value={project.plant.mvaVolume.weeklyDemand} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, weeklyDemand: numberValue(event.target.value) } } }))} /></label>
              <label className="full-span"><span>Work Days / Week</span><input type="number" value={project.plant.mvaVolume.workDaysPerWeek} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, mvaVolume: { ...current.plant.mvaVolume, workDaysPerWeek: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Yield / FPY / VPY Strategy</h3><p className="muted">Capacity strategy and whether to reuse L10 imported FPY.</p></div>
            <div className="form-grid cols-2">
              <label><span>Strategy</span><select value={project.plant.yield.strategy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, strategy: event.target.value as 'ignore' | 'apply_to_capacity' } } }))}><option value="ignore">Ignore</option><option value="apply_to_capacity">Apply to capacity</option></select></label>
              <label><span>L10 FPY (loaded)</span><input readOnly value={project.laborTimeL10.meta.fpy ?? ''} /></label>
              <label><span>FPY (0-1)</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.fpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, fpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
              <label><span>VPY (0-1)</span><input type="number" min="0" max="1" step="0.01" value={project.plant.yield.vpy} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, vpy: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
              <label className="full-span checkbox-field"><input type="checkbox" checked={Boolean(project.plant.yield.useL10Fpy)} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, yield: { ...current.plant.yield, useL10Fpy: event.target.checked } } }))} /><span>Use FPY from L10 labor time estimation CSV</span></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Plant Summary</h3><p className="muted">Current line and volume assumptions being consumed by MVA.</p></div>
            <ul className="plain-list compact-list">
              <li>Plant operation days / year: {project.plant.plantOperationDaysPerYear}</li>
              <li>Monthly volume used: {mva.monthlyVolume.toFixed(2)}</li>
              <li>Line UPH raw: {mva.lineUphRaw.toFixed(2)}</li>
              <li>Line UPH used: {mva.lineUphUsedForCapacity.toFixed(2)}</li>
            </ul>
          </section>
        </div>
      </SectionCard>

      <SectionCard title="Materials, SGA, Profit, and ICC" description="The same per-unit supporting cost drivers used by the legacy MVA workbook.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Materials</h3><p className="muted">BOM, attrition, and consumables assumptions.</p></div>
            <div className="form-grid cols-2">
              <label><span>BOM Cost / Unit</span><input type="number" step="0.01" value={project.plant.materials.bomCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, bomCostPerUnit: numberValue(event.target.value) } } }))} /></label>
              <label><span>Attrition Rate</span><input type="number" step="0.0001" value={project.plant.materials.attritionRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, attritionRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
              <label><span>Consumables / Unit</span><input type="number" step="0.01" value={project.plant.materials.consumablesCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, materials: { ...current.plant.materials, consumablesCostPerUnit: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>SG&amp;A</h3><p className="muted">Corporate burden, site maintenance, and IT security.</p></div>
            <div className="form-grid cols-2">
              <label><span>Corporate Burden / Month</span><input type="number" step="0.01" value={project.plant.sga.corporateBurdenPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, corporateBurdenPerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>Site Maintenance / Month</span><input type="number" step="0.01" value={project.plant.sga.siteMaintenancePerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, siteMaintenancePerMonth: numberValue(event.target.value) } } }))} /></label>
              <label><span>IT Security / Month</span><input type="number" step="0.01" value={project.plant.sga.itSecurityPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, sga: { ...current.plant.sga, itSecurityPerMonth: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Profit</h3><p className="muted">BC BOM cost and profit rate inputs.</p></div>
            <div className="form-grid cols-2">
              <label><span>BC BOM Cost / Unit</span><input type="number" step="0.01" value={project.plant.profit.bcBomCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, profit: { ...current.plant.profit, bcBomCostPerUnit: numberValue(event.target.value) } } }))} /></label>
              <label><span>Profit Rate</span><input type="number" step="0.0001" value={project.plant.profit.profitRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, profit: { ...current.plant.profit, profitRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>ICC</h3><p className="muted">Inventory value and ICC rate assumptions.</p></div>
            <div className="form-grid cols-2">
              <label><span>Inventory Value / Unit</span><input type="number" step="0.01" value={project.plant.icc.inventoryValuePerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, icc: { ...current.plant.icc, inventoryValuePerUnit: numberValue(event.target.value) } } }))} /></label>
              <label><span>ICC Rate</span><input type="number" step="0.0001" value={project.plant.icc.iccRate} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, icc: { ...current.plant.icc, iccRate: boundedNumber(event.target.value, { min: 0, max: 1 }) } } }))} /></label>
            </div>
          </section>
        </div>
      </SectionCard>
    </div>
  );
});
