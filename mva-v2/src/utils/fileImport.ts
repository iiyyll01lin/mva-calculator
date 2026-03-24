import { MAX_IMPORT_BYTES } from './formatters';

export function assertFileSize(file: File): void {
  if (file.size > MAX_IMPORT_BYTES) {
    throw new Error(`File exceeds ${MAX_IMPORT_BYTES / (1024 * 1024)} MB limit.`);
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function readJsonRecord(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text) as unknown;
  if (!isRecord(parsed)) {
    throw new Error('JSON payload must be an object.');
  }
  return parsed;
}
