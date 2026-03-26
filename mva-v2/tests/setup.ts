// React 18 requires this flag in some non-RTL jsdom test setups.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// Stub out the Web Worker constructor so that useCalcWorker's feature-detect
// (`typeof Worker === 'undefined'`) triggers the synchronous fallback path in
// all Vitest / jsdom tests.  Without this stub jsdom would either expose a
// partially-working Worker shim or leave the symbol undefined depending on the
// version, producing inconsistent hook behaviour across test environments.
// The synchronous fallback is fully tested and feature-equivalent — this stub
// simply makes the decision deterministic.
(globalThis as Record<string, unknown>).Worker = undefined;

// Bypass the login auth guard for all tests so they render the main App
// without hitting the LoginPage. This mirrors what a real browser session
// would have after a successful sign-in.
sessionStorage.setItem('mva_auth', '1');

export {};
