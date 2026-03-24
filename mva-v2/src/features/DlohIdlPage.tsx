import { memo } from 'react';
import { SectionCard } from '../components/SectionCard';
import { resolveIndirectLaborRows } from '../domain/calculations';
import { parseDlohIdlSetupCsv } from '../domain/importers';
import type { LaborRow, MvaResults, ProjectState } from '../domain/models';
import { assertFileSize } from '../utils/fileImport';
import { numberValue, toMoney } from '../utils/formatters';

interface DlohIdlPageProps {
  project: ProjectState;
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  mva: MvaResults;
  onStatusMessage: (msg: string) => void;
}

export const DlohIdlPage = memo(function DlohIdlPage({ project, updateProject, mva, onStatusMessage }: DlohIdlPageProps) {
  const updateDirectLaborRow = (id: string, patch: Partial<LaborRow>) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, directLaborRows: current.plant.directLaborRows.map((row) => (row.id === id ? { ...row, ...patch } : row)) },
    }));
  };
  const addDirectLaborRow = () => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, directLaborRows: [...current.plant.directLaborRows, { id: `dl-${Date.now()}`, name: '', process: '', headcount: 0, uphSource: 'line' }] },
    }));
  };
  const deleteDirectLaborRow = (id: string) => {
    updateProject((current) => ({
      ...current,
      plant: { ...current.plant, directLaborRows: current.plant.directLaborRows.filter((row) => row.id !== id) },
    }));
  };
  const updateIndirectLaborRow = (id: string, patch: Partial<LaborRow>) => {
    updateProject((current) => {
      const indirectLaborRows = current.plant.indirectLaborRows.map((row) => (row.id === id ? { ...row, ...patch } : row));
      const nextPlant: ProjectState['plant'] = { ...current.plant, indirectLaborRows };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = { ...current.plant.idlRowsByMode, [mode]: indirectLaborRows };
      return { ...current, plant: nextPlant };
    });
  };
  const addIndirectLaborRow = () => {
    updateProject((current) => {
      const newRow: LaborRow = { id: `idl-${Date.now()}`, name: '', department: '', role: '', headcount: 0, allocationPercent: 1, uphSource: 'line' };
      const indirectLaborRows = [...current.plant.indirectLaborRows, newRow];
      const nextPlant: ProjectState['plant'] = { ...current.plant, indirectLaborRows };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = { ...current.plant.idlRowsByMode, [mode]: indirectLaborRows };
      return { ...current, plant: nextPlant };
    });
  };
  const deleteIndirectLaborRow = (id: string) => {
    updateProject((current) => {
      const indirectLaborRows = current.plant.indirectLaborRows.filter((row) => row.id !== id);
      const nextPlant: ProjectState['plant'] = { ...current.plant, indirectLaborRows };
      const mode = current.plant.idlUiMode === 'auto' ? current.plant.processType : current.plant.idlUiMode;
      nextPlant.idlRowsByMode = { ...current.plant.idlRowsByMode, [mode]: indirectLaborRows };
      return { ...current, plant: nextPlant };
    });
  };
  const importDlohIdlSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseDlohIdlSetupCsv(await file.text());
      updateProject((current) => {
        const nextPlant: ProjectState['plant'] = {
          ...current.plant,
          idlRowsByMode: { ...current.plant.idlRowsByMode, L10: parsed.idlL10, L6: parsed.idlL6 },
        };
        nextPlant.indirectLaborRows = resolveIndirectLaborRows(nextPlant);
        return { ...current, plant: nextPlant };
      });
      onStatusMessage(`Imported DLOH/IDL setup CSV: ${file.name}`);
    } catch (error) {
      onStatusMessage(error instanceof Error ? error.message : 'Failed to import DLOH/IDL setup CSV.');
      console.error(error);
    }
  };

  return (
    <div className="stack-xl">
      <SectionCard title="DLOH-L & IDL Setup" description="Import, IDL mode control, and editable direct / indirect labor rows.">
        <div className="legacy-page-grid">
          <section className="legacy-subcard">
            <div className="legacy-subcard-header"><h3>Import and Mode</h3><p className="muted">CSV import, effective mode, and row counts.</p></div>
            <div className="form-grid cols-2">
              <label className="full-span"><span>Import DLOH-L &amp; IDL Setup CSV</span><input type="file" accept=".csv,text/csv" onChange={(event) => void importDlohIdlSetup(event.target.files?.[0] ?? null)} /></label>
              <label><span>IDL Mode</span><select value={project.plant.idlUiMode} onChange={(event) => updateProject((current) => {
                const idlUiMode = event.target.value as ProjectState['plant']['idlUiMode'];
                const plant: ProjectState['plant'] = { ...current.plant, idlUiMode };
                plant.indirectLaborRows = resolveIndirectLaborRows(plant, idlUiMode);
                return { ...current, plant };
              })}><option value="auto">auto</option><option value="L10">L10</option><option value="L6">L6</option></select></label>
              <label><span>Effective IDL Mode</span><input readOnly value={project.plant.idlUiMode === 'auto' ? project.plant.processType : project.plant.idlUiMode} /></label>
              <label><span>L10 Loaded Rows</span><input readOnly value={project.plant.idlRowsByMode.L10.length} /></label>
              <label><span>L6 Loaded Rows</span><input readOnly value={project.plant.idlRowsByMode.L6.length} /></label>
            </div>
          </section>
        </div>
      </SectionCard>

      <SectionCard title="Editable Labor Tables" description="Editable direct and indirect labor rows aligned to the legacy plant labor worksheets.">
        <div className="two-column-grid">
          <div>
            <div className="inline-actions"><h3 className="subsection-title">Direct labor</h3><button type="button" className="button ghost" onClick={addDirectLaborRow}>Add</button></div>
            <div className="editable-list">
              {project.plant.directLaborRows.map((row) => (
                <div className="list-row wide-5" key={row.id}>
                  <input value={row.name} onChange={(event) => updateDirectLaborRow(row.id, { name: event.target.value })} placeholder="Name" />
                  <input value={row.process ?? ''} onChange={(event) => updateDirectLaborRow(row.id, { process: event.target.value })} placeholder="Process" />
                  <input type="number" value={row.headcount} onChange={(event) => updateDirectLaborRow(row.id, { headcount: numberValue(event.target.value) })} placeholder="HC" />
                  <input type="number" value={row.overrideUph ?? ''} onChange={(event) => updateDirectLaborRow(row.id, { overrideUph: event.target.value === '' ? null : numberValue(event.target.value), uphSource: event.target.value === '' ? 'line' : 'override' })} placeholder="Override UPH" />
                  <button type="button" className="button ghost" onClick={() => deleteDirectLaborRow(row.id)}>Delete</button>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="inline-actions"><h3 className="subsection-title">Indirect labor</h3><button type="button" className="button ghost" onClick={addIndirectLaborRow}>Add</button></div>
            <div className="editable-list">
              {project.plant.indirectLaborRows.map((row) => (
                <div className="list-row wide-6" key={row.id}>
                  <input value={row.name} onChange={(event) => updateIndirectLaborRow(row.id, { name: event.target.value })} placeholder="Name" />
                  <input value={row.department ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { department: event.target.value })} placeholder="Department" />
                  <input value={row.role ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { role: event.target.value })} placeholder="Role" />
                  <input value={row.rrNote ?? ''} onChange={(event) => updateIndirectLaborRow(row.id, { rrNote: event.target.value })} placeholder="R&R Note" />
                  <input type="number" value={row.headcount} onChange={(event) => updateIndirectLaborRow(row.id, { headcount: numberValue(event.target.value) })} placeholder="HC" />
                  <button type="button" className="button ghost" onClick={() => deleteIndirectLaborRow(row.id)}>Delete</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Current Costed Labor Tables" description="Effective headcount and cost per unit from the current line assumptions.">
        <div className="two-column-grid">
          <div className="data-table-wrapper compact">
            <table className="data-table">
              <thead><tr><th>Name</th><th>HC</th><th>Cost / Unit</th></tr></thead>
              <tbody>
                {mva.directLabor.map((row) => (
                  <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{toMoney(row.costPerUnit)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="data-table-wrapper compact">
            <table className="data-table">
              <thead><tr><th>Name</th><th>HC</th><th>Cost / Unit</th></tr></thead>
              <tbody>
                {mva.indirectLabor.map((row) => (
                  <tr key={row.id}><td>{row.name}</td><td>{row.effectiveHeadcount}</td><td>{toMoney(row.costPerUnit)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </SectionCard>
    </div>
  );
});
