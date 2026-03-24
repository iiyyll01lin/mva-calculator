import { memo, useMemo } from 'react';
import { SectionCard } from '../components/SectionCard';
import {
  computeEquipmentDelta,
  deriveMachineRatesFromEquipmentLinks,
  equipmentMonthlyCost,
} from '../domain/calculations';
import { parseEquipmentListSetupCsv } from '../domain/importers';
import type { EquipmentItem, LineStandard, ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue, toMoney, formatTimestamp } from '../utils/formatters';
import { validateLineStandardsPayload } from '../domain/importers';
import { readJsonRecord } from '../utils/fileImport';
import { downloadText } from '../domain/exporters';

interface PlantEquipmentPageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  selectedLineStandardId: string;
  onUpdateLineStandardSelection: (id: string) => void;
  onStatusMessage: (msg: string) => void;
}

export const PlantEquipmentPage = memo(function PlantEquipmentPage({
  project,
  updateProject,
  selectedLineStandardId,
  onUpdateLineStandardSelection,
  onStatusMessage,
}: PlantEquipmentPageProps) {
  const selectedLineStandard = useMemo(
    () => project.lineStandards.find((item) => item.id === selectedLineStandardId) ?? null,
    [project.lineStandards, selectedLineStandardId],
  );
  const equipmentDelta = useMemo(
    () => computeEquipmentDelta({
      template: selectedLineStandard,
      equipmentList: project.plant.equipmentList,
      extraEquipmentList: project.plant.extraEquipmentList,
    }),
    [project.plant.equipmentList, project.plant.extraEquipmentList, selectedLineStandard],
  );
  const equipmentSimulationMapping = useMemo(
    () => deriveMachineRatesFromEquipmentLinks({
      baseMachines: project.machines,
      equipmentList: project.plant.equipmentList,
      extraEquipmentList: project.plant.extraEquipmentList,
      includeExtra: project.plant.includeExtraEquipmentInSimMapping,
    }),
    [project.machines, project.plant.equipmentList, project.plant.extraEquipmentList, project.plant.includeExtraEquipmentInSimMapping],
  );
  const equipmentListTotalPerMonth = useMemo(
    () => project.plant.equipmentList.reduce((sum, item) => sum + (item.costPerMonth ?? equipmentMonthlyCost(item)), 0),
    [project.plant.equipmentList],
  );
  const extraEquipmentTotalPerMonth = useMemo(
    () => project.plant.extraEquipmentList.reduce((sum, item) => sum + (item.costPerMonth ?? equipmentMonthlyCost(item)), 0),
    [project.plant.extraEquipmentList],
  );

  const updateEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList', id: string, patch: Partial<EquipmentItem>) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, [listKey]: current.plant[listKey].map((item) => (item.id === id ? { ...item, ...patch } : item)) },
    }));
  };
  const addEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList') => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, [listKey]: [...current.plant[listKey], { id: `${listKey}-${Date.now()}`, process: '', item: '', qty: 0, unitPrice: 0, depreciationYears: 1, costPerMonth: null }] },
    }));
  };
  const deleteEquipmentRow = (listKey: 'equipmentList' | 'extraEquipmentList', id: string) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, [listKey]: current.plant[listKey].filter((item) => item.id !== id) },
    }));
  };

  const applyEquipmentMappingToSimulation = () => {
    updateProject((current) => ({
      ...current,
      machines: deriveMachineRatesFromEquipmentLinks({
        baseMachines: current.machines,
        equipmentList: current.plant.equipmentList,
        extraEquipmentList: current.plant.extraEquipmentList,
        includeExtra: current.plant.includeExtraEquipmentInSimMapping,
      }).machines,
    }));
    onStatusMessage('Applied equipment-to-simulation mapping to Machine Rates.');
  };
  const applySelectedLineStandard = () => {
    const selected = project.lineStandards.find((item) => item.id === selectedLineStandardId);
    if (!selected) { onStatusMessage('Selected line standard was not found.'); return; }
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, equipmentList: selected.equipmentList.map((item, idx) => ({ ...item, id: `${selected.id}-${idx}` })) },
    }));
    onStatusMessage(`Applied line standard: ${selected.name}`);
  };
  const saveCurrentAsLineStandard = () => {
    const template: LineStandard = {
      id: `std-${Date.now()}`,
      name: `${project.basicInfo.modelName} baseline`,
      source: 'mva-v2',
      equipmentList: project.plant.equipmentList,
    };
    updateProject((current) => ({ ...current, lineStandards: [...current.lineStandards, template] }));
    onUpdateLineStandardSelection(template.id);
    onStatusMessage(`Saved current baseline as line standard: ${template.name}`);
  };

  const importEquipmentSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseEquipmentListSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          equipmentList: parsed.equipmentList,
          overheadByProcess: {
            ...current.plant.overheadByProcess,
            [current.plant.processType]: {
              ...current.plant.overheadByProcess[current.plant.processType],
              equipmentDepreciationPerMonth: parsed.equipmentCostPerMonth ?? current.plant.overheadByProcess[current.plant.processType].equipmentDepreciationPerMonth,
            },
          },
        },
      }));
      onStatusMessage(`Imported equipment setup CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import equipment setup CSV.');
      console.error(error);
    }
  };
  const importLineStandardsLocal = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = validateLineStandardsPayload(JSON.parse(await file.text()) as unknown);
      updateProject((current) => ({ ...current, lineStandards: parsed }));
      onUpdateLineStandardSelection(parsed[0]?.id ?? '');
      onStatusMessage(`Imported line standards: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import line standards JSON.');
      console.error(error);
    }
  };
  const exportLineStandards = () =>
    downloadText(`line-standards_${formatTimestamp()}.json`, JSON.stringify(project.lineStandards, null, 2), 'application/json');

  return (
    <div className="stack-xl">
      <SectionCard title="Equipment List" description="Baseline equipment, extra fixture delta, template reuse, and mapping assumptions." actions={<div className="inline-actions"><button type="button" className="button ghost" onClick={saveCurrentAsLineStandard}>Save Current as Template</button><button type="button" className="button secondary" onClick={applySelectedLineStandard}>Apply Selected</button></div>}>
        <div className="two-column-grid">
          <div>
            <label><span>Active line standard</span><select value={selectedLineStandardId} onChange={(event) => onUpdateLineStandardSelection(event.target.value)}>{project.lineStandards.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          </div>
          <div className="panel-list">
            <h3>Templates</h3>
            <ul className="plain-list">
              {project.lineStandards.map((item) => (
                <li key={item.id}>{item.name} · {item.equipmentList.length} items</li>
              ))}
            </ul>
          </div>
        </div>
        <div className="action-grid compact-actions mt-md">
          <label className="upload-card"><span>Import Equipment List Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importEquipmentSetup(event.target.files?.[0] ?? null)} /></label>
          <label className="upload-card"><span>Import Line Standards JSON</span><input type="file" accept="application/json" onChange={(event) => void importLineStandardsLocal(event.target.files?.[0] ?? null)} /></label>
        </div>
      </SectionCard>

      <SectionCard title="Equipment to Simulation Mapping" description="Legacy parity mapping from equipment rows into machine-group rates, with preview and explicit sync to the simulation sheet." actions={<div className="inline-actions"><button type="button" className="button secondary" onClick={applyEquipmentMappingToSimulation}>Write Derived Rates to Machine Rates</button></div>}>
        <div className="two-column-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Mapping Control</h3><p className="muted">Enable linked-rate simulation and optionally include new-product extra fixtures.</p></div>
            <div className="form-grid cols-2">
              <label className="checkbox-field full-span"><input type="checkbox" checked={project.plant.showEquipmentSimMapping} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, showEquipmentSimMapping: event.target.checked } }))} /><span>Use equipment mapping to drive simulation rates</span></label>
              <label className="checkbox-field full-span"><input type="checkbox" checked={project.plant.includeExtraEquipmentInSimMapping} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, includeExtraEquipmentInSimMapping: event.target.checked } }))} /><span>Include extra equipment in mapping</span></label>
              <label><span>Mapped Machine Groups</span><input readOnly value={equipmentSimulationMapping.rows.length} /></label>
              <label><span>Simulation Mode</span><input readOnly value={project.plant.showEquipmentSimMapping ? 'derived from equipment' : 'manual machine sheet'} /></label>
            </div>
          </section>
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Derived Rate Preview</h3><p className="muted">Current rate vs mapped total rate before writing back to the simulation sheet.</p></div>
            <div className="data-table-wrapper compact">
              <table className="data-table">
                <thead><tr><th>Machine Group</th><th>Current Rate</th><th>Mapped Parallel Qty</th><th>Derived Rate</th><th>Linked Items</th></tr></thead>
                <tbody>
                  {equipmentSimulationMapping.rows.length === 0 ? <tr><td colSpan={5}>No mapping links configured yet.</td></tr> : equipmentSimulationMapping.rows.map((row) => (
                    <tr key={row.machineGroup}>
                      <td>{row.machineGroup}</td>
                      <td>{row.currentRate.toFixed(2)}</td>
                      <td>{row.totalParallelQty.toFixed(2)}</td>
                      <td>{row.derivedRate.toFixed(2)}</td>
                      <td>{row.linkedItems.join(', ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </SectionCard>

      <SectionCard title="Equipment Delta" description="Compares the selected line standard against baseline plus extra equipment.">
        <div className="data-table-wrapper compact">
          <table className="data-table">
            <thead>
              <tr><th>Process</th><th>Item</th><th>Template Qty</th><th>Baseline Qty</th><th>Extra Qty</th><th>Template Cost / Mo</th><th>Baseline Cost / Mo</th><th>Extra Cost / Mo</th><th>Total Cost / Mo</th><th>Delta / Mo</th></tr>
            </thead>
            <tbody>
              {equipmentDelta.rows.map((row) => (
                <tr key={`${row.process}-${row.item}`}>
                  <td>{row.process}</td><td>{row.item}</td><td>{row.templateQty}</td><td>{row.baselineQty}</td><td>{row.extraQty}</td>
                  <td>{toMoney(row.templateCostPerMonth)}</td><td>{toMoney(row.baselineCostPerMonth)}</td><td>{toMoney(row.extraCostPerMonth)}</td><td>{toMoney(row.totalCostPerMonth)}</td><td>{toMoney(row.deltaCostPerMonth)}</td>
                </tr>
              ))}
              <tr className="summary-row"><td colSpan={5}>Total</td><td>{toMoney(equipmentDelta.totals.templateCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.baselineCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.extraCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.totalCostPerMonth)}</td><td>{toMoney(equipmentDelta.totals.deltaCostPerMonth)}</td></tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="Equipment List (F-MVA-14)" description="Editable baseline equipment and new-product delta equipment tables.">
        <div className="stack-xl">
          <div>
            <div className="inline-actions"><h3>Baseline equipment</h3><button type="button" className="button ghost" onClick={() => addEquipmentRow('equipmentList')}>Add</button></div>
            <div className="data-table-wrapper compact">
              <table className="data-table">
                <thead><tr><th>Process</th><th>Item</th><th>Qty</th><th>Unit Price</th><th>Years</th><th>Cost/Month</th><th>Sim Group</th><th>Sim Parallel Qty</th><th>Sim Rate Override</th><th>Used</th><th>Action</th></tr></thead>
                <tbody>
                  {project.plant.equipmentList.map((item) => {
                    const months = Math.max(1, item.depreciationYears * 12);
                    const derived = (item.qty * item.unitPrice) / months;
                    return (
                      <tr key={item.id}>
                        <td><input value={item.process} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { process: event.target.value })} /></td>
                        <td><input value={item.item} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { item: event.target.value })} /></td>
                        <td><input type="number" value={item.qty} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { qty: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={item.unitPrice} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { unitPrice: numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={item.depreciationYears} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { depreciationYears: Math.max(1, numberValue(event.target.value)) })} /></td>
                        <td><input type="number" value={item.costPerMonth ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { costPerMonth: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input value={item.simMachineGroup ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simMachineGroup: event.target.value })} /></td>
                        <td><input type="number" value={item.simParallelQty ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simParallelQty: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td><input type="number" value={item.simRateOverride ?? ''} onChange={(event) => updateEquipmentRow('equipmentList', item.id, { simRateOverride: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                        <td>{toMoney(item.costPerMonth ?? derived)}</td>
                        <td><button type="button" className="button ghost" onClick={() => deleteEquipmentRow('equipmentList', item.id)}>Delete</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <label className="checkbox-field"><input type="checkbox" checked={project.plant.useEquipmentListTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, useEquipmentListTotal: event.target.checked } }))} /><span>Use equipment list total to override Equipment Depreciation / Month</span></label>
            <p className="muted inline-total">Equipment List Total Per Month: {toMoney(equipmentListTotalPerMonth)}</p>
          </div>
          <div>
            <div className="inline-actions"><h3>Extra fixture / equipment</h3><button type="button" className="button ghost" onClick={() => addEquipmentRow('extraEquipmentList')}>Add</button></div>
            <div className="data-table-wrapper compact">
              <table className="data-table">
                <thead><tr><th>Process</th><th>Item</th><th>Qty</th><th>Unit Price</th><th>Years</th><th>Cost/Month</th><th>Sim Group</th><th>Sim Parallel Qty</th><th>Sim Rate Override</th><th>Action</th></tr></thead>
                <tbody>
                  {project.plant.extraEquipmentList.map((item) => (
                    <tr key={item.id}>
                      <td><input value={item.process} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { process: event.target.value })} /></td>
                      <td><input value={item.item} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { item: event.target.value })} /></td>
                      <td><input type="number" value={item.qty} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { qty: numberValue(event.target.value) })} /></td>
                      <td><input type="number" value={item.unitPrice} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { unitPrice: numberValue(event.target.value) })} /></td>
                      <td><input type="number" value={item.depreciationYears} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { depreciationYears: Math.max(1, numberValue(event.target.value)) })} /></td>
                      <td><input type="number" value={item.costPerMonth ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { costPerMonth: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                      <td><input value={item.simMachineGroup ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simMachineGroup: event.target.value })} /></td>
                      <td><input type="number" value={item.simParallelQty ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simParallelQty: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                      <td><input type="number" value={item.simRateOverride ?? ''} onChange={(event) => updateEquipmentRow('extraEquipmentList', item.id, { simRateOverride: event.target.value === '' ? null : numberValue(event.target.value) })} /></td>
                      <td><button type="button" className="button ghost" onClick={() => deleteEquipmentRow('extraEquipmentList', item.id)}>Delete</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <label className="checkbox-field"><input type="checkbox" checked={project.plant.useExtraEquipmentInTotal} onChange={(event) => updateProject((current) => ({ ...current, plant: { ...current.plant, useExtraEquipmentInTotal: event.target.checked } }))} /><span>Include extra fixture / equipment in total</span></label>
            <p className="muted inline-total">Extra Equipment Total Per Month: {toMoney(extraEquipmentTotalPerMonth)}</p>
          </div>
        </div>
      </SectionCard>
    </div>
  );
});
