import { resolveIndirectLaborRows } from '../domain/calculations';
import { defaultProject } from '../domain/defaults';
import {
  parseDlohIdlSetupCsv,
  parseEquipmentListSetupCsv,
  parseMonthYearUpdateCsv,
  parseSpaceSetupCsv,
  validateLineStandardsPayload,
  validateProjectPayload,
} from '../domain/importers';
import type { ProjectState } from '../domain/models';
import { assertFileSize, readJsonRecord } from '../utils/fileImport';

interface Options {
  updateProject: (updater: (current: ProjectState) => ProjectState) => void;
  setSelectedLineStandardId: (id: string) => void;
  setStatusMessage: (message: string) => void;
}

/**
 * Returns all project-level import handlers used by the SummaryPage
 * imports/exports slot. Keeps App.tsx free of ~140 lines of async file logic.
 */
export function useProjectImports({
  updateProject,
  setSelectedLineStandardId,
  setStatusMessage,
}: Options) {
  const importJsonProject = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = validateProjectPayload(readJsonRecord(await file.text()));
      updateProject(() => ({
        ...defaultProject,
        ...parsed,
        plant: { ...defaultProject.plant, ...parsed.plant },
        lineStandards: parsed.lineStandards?.length ? parsed.lineStandards : defaultProject.lineStandards,
      }));
      setStatusMessage(`Imported project: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import project JSON.');
      console.error(error);
    }
  };

  const importLineStandards = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = validateLineStandardsPayload(JSON.parse(await file.text()) as unknown);
      updateProject((current) => ({ ...current, lineStandards: parsed }));
      setSelectedLineStandardId(parsed[0]?.id ?? '');
      setStatusMessage(`Imported line standards: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import line standards JSON.');
      console.error(error);
    }
  };

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
              equipmentAverageUsefulLifeYears:
                parsed.equipmentAverageUsefulLifeYears ??
                current.plant.overheadByProcess[current.plant.processType].equipmentAverageUsefulLifeYears,
              equipmentMaintenanceRatio:
                parsed.equipmentMaintenanceRatio ??
                current.plant.overheadByProcess[current.plant.processType].equipmentMaintenanceRatio,
              powerCostPerUnit:
                parsed.powerCostPerUnit ??
                current.plant.overheadByProcess[current.plant.processType].powerCostPerUnit,
            },
          },
          spaceSettings: {
            ...current.plant.spaceSettings,
            spaceRatePerSqft: parsed.spaceRatePerSqft ?? current.plant.spaceSettings.spaceRatePerSqft,
          },
        },
      }));
      setStatusMessage(`Imported month/year update CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import month/year update CSV.');
      console.error(error);
    }
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
              equipmentDepreciationPerMonth:
                parsed.equipmentCostPerMonth ??
                current.plant.overheadByProcess[current.plant.processType].equipmentDepreciationPerMonth,
            },
          },
        },
      }));
      setStatusMessage(`Imported equipment setup CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import equipment setup CSV.');
      console.error(error);
    }
  };

  const importSpaceSetup = async (file: File | null) => {
    if (!file) return;
    try {
      assertFileSize(file);
      const parsed = parseSpaceSetupCsv(await file.text());
      updateProject((current) => ({
        ...current,
        plant: {
          ...current.plant,
          spaceAllocation: parsed.allocations,
          spaceSettings: {
            ...current.plant.spaceSettings,
            spaceAreaMultiplier: parsed.areaMultiplier ?? current.plant.spaceSettings.spaceAreaMultiplier,
          },
        },
      }));
      setStatusMessage(`Imported space setup CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import space setup CSV.');
      console.error(error);
    }
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
      setStatusMessage(`Imported DLOH/IDL setup CSV: ${file.name}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : 'Failed to import DLOH/IDL setup CSV.');
      console.error(error);
    }
  };

  return {
    importJsonProject,
    importLineStandards,
    importMonthYearUpdate,
    importEquipmentSetup,
    importSpaceSetup,
    importDlohIdlSetup,
  };
}
