import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import type { MvaResults, ProjectState } from '../domain/models';
import { boundedNumber, numberValue, toMoney } from '../utils/formatters';

interface PlantEnvPageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  mva: MvaResults;
}

export const PlantEnvPage = memo(function PlantEnvPage({ project, updateProject, mva }: PlantEnvPageProps) {
  const activeOverhead = project.plant.overheadByProcess[project.plant.processType];

  return (
    <div className="stack-xl">
      <SectionCard title="Environment & Equipment Rate" description="Overhead, building area, equipment, and space assumptions by process type.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Overhead (MVP + P1)</h3><p className="muted">Equipment depreciation, maintenance, space, and power assumptions.</p></div>
            <div className="form-grid cols-2">
              <label><span>Equipment Useful Life Years</span><input type="number" step="0.1" value={activeOverhead.equipmentAverageUsefulLifeYears} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentAverageUsefulLifeYears: numberValue(event.target.value) } } } }))} /></label>
              <label><span>Equipment Depreciation / Month</span><input type="number" step="0.01" value={activeOverhead.equipmentDepreciationPerMonth} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentDepreciationPerMonth: numberValue(event.target.value) } } } }))} /></label>
              <label><span>Equipment Maintenance Ratio</span><input type="number" step="0.0001" value={activeOverhead.equipmentMaintenanceRatio} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], equipmentMaintenanceRatio: boundedNumber(event.target.value, { min: 0 }) } } } }))} /></label>
              <label><span>Space / Rental / Cleaning (Monthly)</span><input type="number" step="0.01" value={activeOverhead.spaceMonthlyCost} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], spaceMonthlyCost: numberValue(event.target.value) } } } }))} /></label>
              <label><span>Power Cost / Unit</span><input type="number" step="0.01" value={activeOverhead.powerCostPerUnit} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, overheadByProcess: { ...current.plant.overheadByProcess, [current.plant.processType]: { ...current.plant.overheadByProcess[current.plant.processType], powerCostPerUnit: numberValue(event.target.value) } } } }))} /></label>
            </div>
          </section>

          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Space Override</h3><p className="muted">Building area, rate, and allocation override controls.</p></div>
            <div className="form-grid cols-2">
              <label><span>Use Space Allocation Total</span><select value={project.plant.spaceSettings.useSpaceAllocationTotal ? 'yes' : 'no'} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, useSpaceAllocationTotal: event.target.value === 'yes' } } }))}><option value="yes">yes</option><option value="no">no</option></select></label>
              <label><span>Space Rate / Sqft</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceRatePerSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceRatePerSqft: numberValue(event.target.value) } } }))} /></label>
              <label><span>Area Multiplier</span><input type="number" step="0.01" value={project.plant.spaceSettings.spaceAreaMultiplier} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, spaceAreaMultiplier: numberValue(event.target.value) } } }))} /></label>
              <label><span>Building Override Sqft</span><input type="number" value={project.plant.spaceSettings.buildingAreaOverrideSqft} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, spaceSettings: { ...current.plant.spaceSettings, buildingAreaOverrideSqft: numberValue(event.target.value) } } }))} /></label>
            </div>
          </section>
        </div>
      </SectionCard>

      <SectionCard title="Environment Results" description="Resolved overhead and space figures used in the current MVA calculation.">
        <div className="three-column-grid">
          <div className="panel-list">
            <h3>Equipment</h3>
            <ul className="plain-list">
              <li>Total Equipment Cost / Month: {toMoney(mva.overhead.totalEquipmentCostPerMonth)}</li>
              <li>Equipment Dep / Month Used: {toMoney(mva.overhead.equipmentDepPerMonthUsed)}</li>
              <li>Equipment Dep / Unit: {toMoney(mva.overhead.equipmentDepPerUnit)}</li>
              <li>Equipment Maint / Unit: {toMoney(mva.overhead.equipmentMaintPerUnit)}</li>
            </ul>
          </div>
          <div className="panel-list">
            <h3>Space</h3>
            <ul className="plain-list">
              <li>Floor Total Sqft: {mva.overhead.spaceFloorTotalSqft.toFixed(2)}</li>
              <li>Building Area Sqft: {mva.overhead.spaceBuildingAreaSqft.toFixed(2)}</li>
              <li>Space / Month Used: {toMoney(mva.overhead.spaceMonthlyCostUsed)}</li>
              <li>Space / Unit: {toMoney(mva.overhead.spacePerUnit)}</li>
            </ul>
          </div>
          <div className="panel-list">
            <h3>Utilities</h3>
            <ul className="plain-list">
              <li>Power / Unit: {toMoney(mva.overhead.powerPerUnit)}</li>
              <li>Total Overhead / Unit: {toMoney(mva.overhead.totalOverheadPerUnit)}</li>
              <li>Material Attrition / Unit: {toMoney(mva.materials.materialAttritionPerUnit)}</li>
              <li>Consumables / Unit: {toMoney(mva.materials.consumablesPerUnit)}</li>
            </ul>
          </div>
        </div>
      </SectionCard>
    </div>
  );
});
