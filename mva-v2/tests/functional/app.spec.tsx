import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import App from '../../src/App';

describe('App functional flow', () => {
  it('updates weekly demand from the basic information screen', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<App />);
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

    await act(async () => {
      root.render(<App />);
    });

    const equipmentButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Equipment List'));
    expect(equipmentButton).toBeTruthy();
    await act(async () => {
      equipmentButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

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
    await act(async () => {
      laborButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(container.textContent?.includes('Labor Time Estimation (L6)')).toBe(true);
    expect(container.textContent?.includes('Segment Name')).toBe(true);
    expect(container.textContent?.includes('Bottleneck UPH')).toBe(true);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
