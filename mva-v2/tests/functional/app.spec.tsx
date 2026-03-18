import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import App from '../../src/App';

describe('App functional flow', () => {
  it('updates weekly demand from the simulation screen', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<App />);
    });

    const simulationButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Simulation'));
    expect(simulationButton).toBeTruthy();
    await act(async () => {
      simulationButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

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
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('shows summary preview in reports', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<App />);
    });

    const reportsButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Reports'));
    expect(reportsButton).toBeTruthy();
    await act(async () => {
      reportsButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const preview = container.querySelector('[data-testid="summary-preview"]') as HTMLTextAreaElement | null;
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

    await act(async () => {
      root.render(<App />);
    });

    const equipmentButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Equipment'));
    expect(equipmentButton).toBeTruthy();
    await act(async () => {
      equipmentButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(container.textContent?.includes('Equipment Delta')).toBe(true);
    expect(container.textContent?.includes('Space Strategy')).toBe(true);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
