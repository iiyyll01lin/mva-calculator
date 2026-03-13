import {
  defaultProject,
  lineStandardSelectionStorageKey,
  lineStandardStorageKey,
  projectStorageKey,
} from './defaults';
import type { LineStandard, ProjectState } from './models';

function mergeProject(parsed: Partial<ProjectState>): ProjectState {
  return {
    ...defaultProject,
    ...parsed,
    basicInfo: { ...defaultProject.basicInfo, ...(parsed.basicInfo ?? {}) },
    machines: parsed.machines ?? defaultProject.machines,
    bomMap: parsed.bomMap ?? defaultProject.bomMap,
    processSteps: parsed.processSteps ?? defaultProject.processSteps,
    plant: {
      ...defaultProject.plant,
      ...(parsed.plant ?? {}),
      mvaRates: { ...defaultProject.plant.mvaRates, ...(parsed.plant?.mvaRates ?? {}) },
      mvaVolume: { ...defaultProject.plant.mvaVolume, ...(parsed.plant?.mvaVolume ?? {}) },
      overheadByProcess: {
        L6: { ...defaultProject.plant.overheadByProcess.L6, ...(parsed.plant?.overheadByProcess?.L6 ?? {}) },
        L10: { ...defaultProject.plant.overheadByProcess.L10, ...(parsed.plant?.overheadByProcess?.L10 ?? {}) },
      },
      yield: { ...defaultProject.plant.yield, ...(parsed.plant?.yield ?? {}) },
      idlRowsByMode: {
        L10: parsed.plant?.idlRowsByMode?.L10 ?? defaultProject.plant.idlRowsByMode.L10,
        L6: parsed.plant?.idlRowsByMode?.L6 ?? defaultProject.plant.idlRowsByMode.L6,
      },
      spaceSettings: { ...defaultProject.plant.spaceSettings, ...(parsed.plant?.spaceSettings ?? {}) },
      materials: { ...defaultProject.plant.materials, ...(parsed.plant?.materials ?? {}) },
      sga: { ...defaultProject.plant.sga, ...(parsed.plant?.sga ?? {}) },
      profit: { ...defaultProject.plant.profit, ...(parsed.plant?.profit ?? {}) },
      icc: { ...defaultProject.plant.icc, ...(parsed.plant?.icc ?? {}) },
      directLaborRows: parsed.plant?.directLaborRows ?? defaultProject.plant.directLaborRows,
      indirectLaborRows: parsed.plant?.indirectLaborRows ?? defaultProject.plant.indirectLaborRows,
      equipmentList: parsed.plant?.equipmentList ?? defaultProject.plant.equipmentList,
      extraEquipmentList: parsed.plant?.extraEquipmentList ?? defaultProject.plant.extraEquipmentList,
      spaceAllocation: parsed.plant?.spaceAllocation ?? defaultProject.plant.spaceAllocation,
    },
    productL10: {
      ...defaultProject.productL10,
      ...(parsed.productL10 ?? {}),
      testTime: { ...defaultProject.productL10.testTime, ...(parsed.productL10?.testTime ?? {}) },
    },
    productL6: {
      ...defaultProject.productL6,
      ...(parsed.productL6 ?? {}),
      routingTimesSec: { ...defaultProject.productL6.routingTimesSec, ...(parsed.productL6?.routingTimesSec ?? {}) },
      testTime: { ...defaultProject.productL6.testTime, ...(parsed.productL6?.testTime ?? {}) },
    },
    laborTimeL10: {
      ...defaultProject.laborTimeL10,
      ...(parsed.laborTimeL10 ?? {}),
      meta: { ...defaultProject.laborTimeL10.meta, ...(parsed.laborTimeL10?.meta ?? {}) },
      stations: parsed.laborTimeL10?.stations ?? defaultProject.laborTimeL10.stations,
    },
    laborTimeL6: {
      ...defaultProject.laborTimeL6,
      ...(parsed.laborTimeL6 ?? {}),
      header: { ...defaultProject.laborTimeL6.header, ...(parsed.laborTimeL6?.header ?? {}) },
      segments: parsed.laborTimeL6?.segments ?? defaultProject.laborTimeL6.segments,
      stations: parsed.laborTimeL6?.stations ?? defaultProject.laborTimeL6.stations,
    },
    confirmation: { ...defaultProject.confirmation, ...(parsed.confirmation ?? {}) },
    lineStandards: parsed.lineStandards?.length ? parsed.lineStandards : defaultProject.lineStandards,
  };
}

export function loadProject(): ProjectState {
  try {
    const raw = window.localStorage.getItem(projectStorageKey);
    if (!raw) return defaultProject;
    const parsed = JSON.parse(raw) as Partial<ProjectState>;
    if (!parsed || typeof parsed !== 'object') return defaultProject;
    return mergeProject(parsed);
  } catch {
    return defaultProject;
  }
}

export function saveProject(project: ProjectState): void {
  window.localStorage.setItem(projectStorageKey, JSON.stringify(project));
}

export function loadLineStandards(): LineStandard[] {
  try {
    const raw = window.localStorage.getItem(lineStandardStorageKey);
    if (!raw) return defaultProject.lineStandards;
    const parsed = JSON.parse(raw) as LineStandard[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : defaultProject.lineStandards;
  } catch {
    return defaultProject.lineStandards;
  }
}

export function saveLineStandards(standards: LineStandard[]): void {
  window.localStorage.setItem(lineStandardStorageKey, JSON.stringify(standards));
}

export function loadSelectedLineStandardId(): string {
  return window.localStorage.getItem(lineStandardSelectionStorageKey) ?? '';
}

export function saveSelectedLineStandardId(id: string): void {
  window.localStorage.setItem(lineStandardSelectionStorageKey, id);
}
