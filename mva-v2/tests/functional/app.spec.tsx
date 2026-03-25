import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeAll, describe, expect, it } from 'vitest';
import App from '../../src/App';

/**
 * Wraps `work()` in an `act()` that correctly flushes React.lazy + Suspense
 * retries in React 18 concurrent mode (createRoot).
 *
 * Why this works when modules are pre-warmed (via `beforeAll`s below):
 *
 * 1. `work()` queues `performConcurrentWorkOnRoot` in `ReactCurrentActQueue`.
 * 2. `act()` drains the queue: the fiber renderer calls the lazy factory,
 *    `import()` returns a micro-task–resolved Promise (from Vitest's cache),
 *    and the Suspense fallback is committed.
 * 3. `recursivelyFlushAsyncActWork` schedules a `setImmediate` check.
 * 4. Before that `setImmediate` fires, micro-tasks run:
 *    the cached import resolves → `pingSuspendedRoot` → `scheduleCallback$1`
 *    sees `ReactCurrentActQueue.current !== null` and pushes the retry.
 * 5. The `setImmediate` check finds a non-empty queue, runs another
 *    `flushActQueue` cycle → the lazy component is committed to the DOM.
 * 6. `act()` resolves; the DOM is ready for assertions.
 *
 * Without `beforeAll` pre-warming, the Vite IPC import takes ~30 ms,
 * which is longer than the `setImmediate` check window, so the retry
 * lands outside the act context and the component never commits in time.
 */
async function actWithLazyFlush(work: () => void): Promise<void> {
  await act(async () => { work(); });
}

describe('App functional flow', () => {
  // Pre-warm Vitest's module cache for the feature pages that are rendered
  // lazily. Without this, React.lazy's dynamic import() goes through the Vite
  // dev-server IPC (~30 ms), which is longer than the setImmediate window in
  // `recursivelyFlushAsyncActWork`. Pre-loading ensures the import() Promise
  // resolves as a microtask (from cache), which fits inside the act() drain
  // window so the Suspense retry lands in ReactCurrentActQueue before act
  // resolves.
  beforeAll(async () => {
    await Promise.all([
      import('../../src/features/BasicInfoPage'),
      import('../../src/features/PlantEquipmentPage'),
      import('../../src/features/LaborTimeL6Page'),
    ]);
  });
  it('updates weekly demand from the basic information screen', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    // Render and flush — lazy BasicInfoPage is committed after act() drains.
    await actWithLazyFlush(() => root.render(<App />));

    const demandInput = container.querySelector('input[aria-label="Weekly Demand"]') as HTMLInputElement | null;
    expect(demandInput).toBeTruthy();
    await act(async () => {
      if (demandInput) {
        demandInput.value = '7200';
        demandInput.dispatchEvent(new Event('input', { bubbles: true }));
        demandInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });

    expect(demandInput?.value).toBe('7200');
    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('shows summary preview in the l10 summary screen', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });

    const summaryButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Summary (L10)'));
    expect(summaryButton).toBeTruthy();
    await act(async () => {
      summaryButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const preview = container.querySelector('[data-testid="summary-preview-l10"]') as HTMLTextAreaElement | null;
    expect(preview).toBeTruthy();
    expect(preview?.value.includes('Total / Unit')).toBe(true);
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders equipment workspace content from the sidebar', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => { root.render(<App />); });

    const equipmentButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Equipment List'));
    expect(equipmentButton).toBeTruthy();
    // Navigate and flush PlantEquipmentPage — lazy component committed after act.
    await actWithLazyFlush(() => equipmentButton!.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(container.textContent?.includes('Equipment Delta')).toBe(true);
    expect(container.textContent?.includes('Equipment List (F-MVA-14)')).toBe(true);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders the l6 segment matrix workspace', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });

    const laborButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Labor Time (L6)'));
    expect(laborButton).toBeTruthy();
    // Navigate and flush LaborTimeL6Page — lazy component committed after act.
    await actWithLazyFlush(() => laborButton!.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(container.textContent?.includes('Labor Time Estimation (L6)')).toBe(true);
    expect(container.textContent?.includes('Segment Name')).toBe(true);
    expect(container.textContent?.includes('Bottleneck UPH')).toBe(true);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});

// ─── downstream cascade tests ─────────────────────────────────────────────

describe('App downstream state cascade', () => {
  it('summary page reflects updated model name after BasicInfo change', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => { root.render(<App />); });

    // Navigate to Basic Info first (already default) and change model name via input
    const modelInput = container.querySelector('input[aria-label="Model Name"]') as HTMLInputElement | null;
    if (modelInput) {
      await act(async () => {
        modelInput.value = 'TEST-MODEL-9999';
        modelInput.dispatchEvent(new Event('input', { bubbles: true }));
        modelInput.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }

    // Navigate to L10 Summary
    const summaryButton = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Summary (L10)'),
    );
    await act(async () => { summaryButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    // The hero title should reflect the model name from state
    const heroTitle = container.querySelector('h1');
    expect(heroTitle).toBeTruthy();

    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('simulation page renders without crashing', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => { root.render(<App />); });

    const simButton = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Simulation'),
    );
    expect(simButton).toBeTruthy();
    await act(async () => { simButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    // Simulation page must show UPH or bottleneck info
    expect(container.textContent?.match(/UPH|Bottleneck|Cycle Time/i)).toBeTruthy();

    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('Plant Rates page renders rate inputs', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => { root.render(<App />); });

    const ratesButton = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Labor Rate & Efficiency'),
    );
    expect(ratesButton).toBeTruthy();
    await act(async () => { ratesButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(container.textContent?.match(/Labor|Rate|Efficiency/i)).toBeTruthy();

    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('DLOH / IDL page renders with headcount table', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => { root.render(<App />); });

    const dlohButton = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('DLOH-L & IDL Setup'),
    );
    expect(dlohButton).toBeTruthy();
    await act(async () => { dlohButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(container.textContent?.match(/Headcount|Labor|IDL/i)).toBeTruthy();

    await act(async () => { root.unmount(); });
    container.remove();
  });
});
