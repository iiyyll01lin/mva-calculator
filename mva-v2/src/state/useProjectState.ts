import { useEffect, useState } from 'react';
import { defaultProject } from '../domain/defaults';
import { loadLineStandards, loadProject, saveLineStandards, saveProject } from '../domain/persistence';
import type { ProjectState } from '../domain/models';

export function useProjectState() {
  const [project, setProject] = useState<ProjectState>(() => {
    const loaded = typeof window === 'undefined' ? defaultProject : loadProject();
    const standards = typeof window === 'undefined' ? loaded.lineStandards : loadLineStandards();
    return { ...loaded, lineStandards: standards };
  });

  useEffect(() => {
    saveProject(project);
    saveLineStandards(project.lineStandards);
  }, [project]);

  return { project, setProject };
}
