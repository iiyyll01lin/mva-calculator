// React 18 requires this flag in some non-RTL jsdom test setups.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// Bypass the login auth guard for all tests so they render the main App
// without hitting the LoginPage. This mirrors what a real browser session
// would have after a successful sign-in.
sessionStorage.setItem('mva_auth', '1');

export {};
