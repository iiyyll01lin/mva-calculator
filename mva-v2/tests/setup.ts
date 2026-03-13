// React 18 requires this flag in some non-RTL jsdom test setups.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

export {};
