import type { ProjectState } from './models';

export const lineStandardStorageKey = 'mva_v2_line_standards';
export const projectStorageKey = 'mva_v2_project';
export const lineStandardSelectionStorageKey = 'mva_v2_selected_line_standard';

export const defaultProject: ProjectState = {
  schemaVersion: 1,
  basicInfo: {
    modelName: '1395A3368201',
    workDays: 26,
    demandWeekly: 6000,
    shiftsPerDay: 2,
    hoursPerShift: 11,
    breaksPerShiftMin: 150,
    targetUtilization: 0.9,
    fpy: 0.96,
    vpy: 1,
    boardsPerPanel: 10,
    lotSize: 1000,
    setupTimeMin: 60,
    oeePlacement: 0.7,
    oeeOthers: 0.8,
  },
  machines: [
    { id: 'm1', group: 'Printer', description: 'Solder paste printer', rate: 120, type: 'fixed' },
    { id: 'm2', group: 'SPI', description: 'Solder paste inspection', rate: 200, type: 'fixed' },
    { id: 'm3', group: 'Placement_NXT_Chips', description: 'FUJI NXT M3 - chips', rate: 22000, type: 'placement' },
    { id: 'm4', group: 'Placement_NXT_IC', description: 'FUJI NXT M6 - IC', rate: 10000, type: 'placement' },
    { id: 'm5', group: 'Placement_NXT_BGA', description: 'FUJI NXT M6 - BGA/CSP', rate: 3500, type: 'placement' },
    { id: 'm6', group: 'AOI', description: 'SMT AOI', rate: 189, type: 'fixed' },
    { id: 'm7', group: 'Reflow', description: 'Reflow oven', rate: 120, type: 'fixed' },
    { id: 'm8', group: 'THT/DIP', description: 'Manual insertion', rate: 120, type: 'fixed' },
    { id: 'm9', group: 'WaveSolder', description: 'Wave solder', rate: 250, type: 'fixed' },
    { id: 'm10', group: 'ICT', description: 'In-circuit test', rate: 90, type: 'fixed' },
    { id: 'm11', group: 'FBT', description: 'Functional test', rate: 60, type: 'fixed' },
    { id: 'm12', group: 'Visual', description: 'Manual inspection', rate: 144, type: 'fixed' },
    { id: 'm13', group: 'Assembly', description: 'Final assembly', rate: 100, type: 'fixed' },
    { id: 'm14', group: 'Packing', description: 'Pack and label', rate: 120, type: 'fixed' }
  ],
  bomMap: [
    { id: 'b1', side: 'Top', bucket: '0402/0603 chips', count: 2385, machineGroup: 'Placement_NXT_Chips' },
    { id: 'b2', side: 'Top', bucket: 'SOP/QFP/QFN', count: 206, machineGroup: 'Placement_NXT_IC' },
    { id: 'b3', side: 'Top', bucket: 'BGA/CSP', count: 3, machineGroup: 'Placement_NXT_BGA' },
    { id: 'b4', side: 'Bottom', bucket: '0402/0603 chips', count: 2130, machineGroup: 'Placement_NXT_Chips' },
    { id: 'b5', side: 'Bottom', bucket: 'SOP/QFP/QFN', count: 26, machineGroup: 'Placement_NXT_IC' },
    { id: 'b6', side: 'Bottom', bucket: 'BGA/CSP', count: 0, machineGroup: 'Placement_NXT_BGA' }
  ],
  processSteps: [
    { step: 1, process: 'Printer', side: 'Bottom' },
    { step: 2, process: 'SPI', side: 'Bottom' },
    { step: 3, process: 'Placement_NXT_Chips', side: 'Bottom' },
    { step: 4, process: 'Placement_NXT_IC', side: 'Bottom' },
    { step: 5, process: 'Placement_NXT_BGA', side: 'Bottom' },
    { step: 6, process: 'AOI', side: 'Bottom' },
    { step: 7, process: 'Reflow', side: 'Bottom' },
    { step: 8, process: 'Printer', side: 'Top' },
    { step: 9, process: 'SPI', side: 'Top' },
    { step: 10, process: 'Placement_NXT_Chips', side: 'Top' },
    { step: 11, process: 'Placement_NXT_IC', side: 'Top' },
    { step: 12, process: 'Placement_NXT_BGA', side: 'Top' },
    { step: 13, process: 'AOI', side: 'Top' },
    { step: 14, process: 'Reflow', side: 'Top' },
    { step: 15, process: 'THT/DIP', side: 'N/A' },
    { step: 16, process: 'WaveSolder', side: 'N/A' },
    { step: 17, process: 'ICT', side: 'N/A' },
    { step: 18, process: 'FBT', side: 'N/A' },
    { step: 19, process: 'Visual', side: 'N/A' },
    { step: 20, process: 'Assembly', side: 'N/A' },
    { step: 21, process: 'Packing', side: 'N/A' }
  ],
  plant: {
    processType: 'L10',
    plantOperationDaysPerYear: 300,
    mvaRates: {
      directLaborHourlyRate: 12.5,
      indirectLaborHourlyRate: 23.28,
      efficiency: 0.93,
    },
    mvaVolume: {
      demandMode: 'monthly',
      rfqQtyPerMonth: 10000,
      weeklyDemand: 6000,
      workDaysPerWeek: 5,
      workDaysPerMonth: 22,
    },
    overheadByProcess: {
      L6: {
        equipmentAverageUsefulLifeYears: 2,
        equipmentDepreciationPerMonth: 0,
        equipmentMaintenanceRatio: 0.1,
        spaceMonthlyCost: 0,
        powerCostPerUnit: 0,
      },
      L10: {
        equipmentAverageUsefulLifeYears: 2,
        equipmentDepreciationPerMonth: 11271.15,
        equipmentMaintenanceRatio: 0.1,
        spaceMonthlyCost: 229742.99,
        powerCostPerUnit: 0.28,
      },
    },
    yield: {
      strategy: 'ignore',
      fpy: 0.96,
      vpy: 1,
    },
    directLaborRows: [
      { id: 'dl-1', name: 'Offline', process: 'Support', headcount: 17, uphSource: 'line' },
      { id: 'dl-2', name: 'Assembly', process: 'Assembly', headcount: 15, uphSource: 'line' },
      { id: 'dl-3', name: 'Kitting', process: 'Kitting', headcount: 3, uphSource: 'line' },
      { id: 'dl-4', name: 'Test', process: 'Test', headcount: 4, uphSource: 'line' },
      { id: 'dl-5', name: 'Packing', process: 'Packing', headcount: 10, uphSource: 'line' },
      { id: 'dl-6', name: 'Shipping', process: 'Shipping', headcount: 1, uphSource: 'line' }
    ],
    indirectLaborRows: [
      { id: 'idl-1', name: 'Site Mgr.', department: 'Management', role: 'Site Mgr.', headcount: 0.22, allocationPercent: 1, uphSource: 'line' },
      { id: 'idl-2', name: 'FIS/MES', department: 'Systems', role: 'FIS/MES', headcount: 1.76, allocationPercent: 1, uphSource: 'line' },
      { id: 'idl-3', name: 'IE/OM/Facility', department: 'IE', role: 'IE/OM/Facility', headcount: 2.376, allocationPercent: 1, uphSource: 'line' },
      { id: 'idl-4', name: 'TE', department: 'Engineering', role: 'TE', headcount: 8.36, allocationPercent: 1, uphSource: 'line' },
      { id: 'idl-5', name: 'QAI', department: 'Quality', role: 'QAI', headcount: 0.7128, allocationPercent: 1, uphSource: 'line' }
    ],
    equipmentList: [
      { id: 'eq-1', process: 'SMT', item: 'Placement line', qty: 1, unitPrice: 540000, depreciationYears: 5 },
      { id: 'eq-2', process: 'Test', item: 'Functional tester', qty: 2, unitPrice: 80000, depreciationYears: 4 }
    ],
    extraEquipmentList: [
      { id: 'ex-1', process: 'Assembly', item: 'Custom fixture', qty: 1, unitPrice: 12000, depreciationYears: 2 }
    ],
    spaceAllocation: [
      { id: 'sp-1', floor: 'F1', process: 'SMT', areaSqft: 1800, ratePerSqft: 8 },
      { id: 'sp-2', floor: 'F1', process: 'F/A', areaSqft: 2200, ratePerSqft: 8 },
      { id: 'sp-3', floor: 'F2', process: 'FBT', areaSqft: 1200, ratePerSqft: 8 }
    ],
    spaceSettings: {
      useSpaceAllocationTotal: true,
      spaceRatePerSqft: 8,
      spaceAreaMultiplier: 1,
      buildingAreaOverrideSqft: 0,
    },
    materials: {
      bomCostPerUnit: 45,
      attritionRate: 0.012,
      consumablesCostPerUnit: 0.8,
    },
    sga: {
      corporateBurdenPerMonth: 30000,
      siteMaintenancePerMonth: 8000,
      itSecurityPerMonth: 2500,
    },
    profit: {
      bcBomCostPerUnit: 45,
      profitRate: 0.08,
    },
    icc: {
      inventoryValuePerUnit: 20,
      iccRate: 0.03,
    },
  },
  productL10: {
    bu: 'Networking',
    customer: 'Internal',
    projectName: 'L10 Demo',
    modelName: '1395A3368201',
    handlingSec: 90,
    functionSec: 300,
    yield: 0.96,
    rfqQtyPerMonth: 10000,
  },
  productL6: {
    bu: 'Networking',
    customer: 'Internal',
    pn: '1395A3368201',
    rfqQtyPerMonth: 10000,
    boardsPerPanel: 10,
    pcbSize: '120x80 mm',
    stationPath: 'SMT > Test > Assembly > Packing',
  },
  confirmation: {
    decision: 'OK',
    reviewer: 'System',
    comment: 'Default release candidate baseline',
    decidedAt: '2026-03-13T00:00:00.000Z',
  },
  lineStandards: [
    {
      id: 'std-1',
      name: 'Default L10 Line',
      source: 'seed',
      equipmentList: [
        { id: 'std-eq-1', process: 'SMT', item: 'Placement line', qty: 1, unitPrice: 540000, depreciationYears: 5 },
        { id: 'std-eq-2', process: 'Assembly', item: 'Torque fixture', qty: 2, unitPrice: 6000, depreciationYears: 3 }
      ],
    }
  ],
};
