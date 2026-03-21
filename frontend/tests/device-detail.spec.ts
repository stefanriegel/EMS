import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Minimal DevicesPayload mock:
//   - grid_l1_power_w: 1200  → import (positive) → .phase-bar-fill--import
//   - grid_l2_power_w: -800  → export (negative) → .phase-bar-fill--export
//   - grid_l3_power_w: null  → N/A, no fill bar
const MOCK_DEVICES = {
  huawei: {
    available: true,
    pack1_soc_pct: 80,
    pack1_power_w: 500,
    pack2_soc_pct: null,
    pack2_power_w: null,
    total_soc_pct: 80,
    total_power_w: 500,
    max_charge_w: 2500,
    max_discharge_w: 2500,
    master_pv_power_w: 1200,
    slave_pv_power_w: null,
  },
  victron: {
    available: true,
    soc_pct: 75,
    battery_power_w: 0,
    l1_power_w: 1000,
    l2_power_w: 1000,
    l3_power_w: 1000,
    l1_voltage_v: 230,
    l2_voltage_v: 230,
    l3_voltage_v: 230,
    grid_power_w: 400,
    grid_l1_power_w: 1200,
    grid_l2_power_w: -800,
    grid_l3_power_w: null,
    consumption_w: 3000,
    pv_on_grid_w: 0,
  },
};

test.describe('DeviceDetail PhaseBar', () => {
  test.beforeEach(async ({ page }) => {
    // K045: prevent wizard redirect
    await page.route('**/api/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ setup_complete: true }),
      })
    );

    // Mock /api/devices — supplies data to the fallback polling consumer
    await page.route('**/api/devices', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_DEVICES),
      })
    );

    // Mock /api/state — required by useEmsState fallback (fetched alongside /api/devices)
    await page.route('**/api/state', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          control_state: 'IDLE',
          combined_power_w: 0,
          huawei_discharge_setpoint_w: 0,
          victron_discharge_setpoint_w: 0,
          grid_charge_slot_active: false,
          tariff: null,
          optimization: null,
        }),
      })
    );

    // K043: suppress TariffCard fetch noise
    await page.route('**/api/tariff/schedule', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
  });

  test('PhaseBar: import/export/null all render correctly with no console errors', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto('/');

    // Wait for .device-detail to appear
    await expect(page.locator('[data-testid="device-detail"]')).toBeVisible({ timeout: 10_000 });

    // Wait for the fallback polling to fire and populate the grid phase bars.
    // The WS disconnects immediately → retryCount becomes 1 → useFallback=true → /api/devices is fetched.
    // We wait for any .phase-bar-row to appear as evidence that the data arrived.
    await expect(page.locator('.phase-bar-row').first()).toBeVisible({ timeout: 15_000 });

    // Three .phase-bar-row elements should exist (Grid L1, L2, L3)
    await expect(page.locator('.phase-bar-row')).toHaveCount(3);

    // Grid L1 (1200W import) → fill has .phase-bar-fill--import class
    const l1Row = page.locator('.phase-bar-row').nth(0);
    await expect(l1Row.locator('.phase-bar-fill--import')).toBeVisible();
    await expect(l1Row.locator('.phase-bar-fill--export')).toHaveCount(0);

    // Grid L2 (-800W export) → fill has .phase-bar-fill--export class
    const l2Row = page.locator('.phase-bar-row').nth(1);
    await expect(l2Row.locator('.phase-bar-fill--export')).toBeVisible();
    await expect(l2Row.locator('.phase-bar-fill--import')).toHaveCount(0);

    // Grid L3 (null) → .phase-bar-value contains "N/A", bar fill has width 0%
    const l3Row = page.locator('.phase-bar-row').nth(2);
    await expect(l3Row.locator('.phase-bar-value')).toHaveText('N/A');
    const fillStyle = await l3Row.locator('.phase-bar-fill').getAttribute('style');
    expect(fillStyle).toContain('width: 0%');

    // Screenshot
    const screenshotDir = path.join(__dirname, 'screenshots');
    if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
    await page.screenshot({
      path: path.join(screenshotDir, 'device-detail-375px.png'),
      fullPage: false,
    });
    expect(fs.existsSync(path.join(screenshotDir, 'device-detail-375px.png'))).toBe(true);

    // Console error check — filter expected infrastructure noise
    // K026: vite preview proxies /api/* → 502 when no backend
    // K039: component-level re-logs have prefix
    // K043: TariffCard logs [TariffCard] prefix
    const realErrors = consoleErrors.filter(
      (e) =>
        !e.includes('WebSocket') &&
        !e.includes('ws://') &&
        !e.includes('Failed to load resource') &&
        !e.includes('/api/') &&
        !e.includes('[TariffCard]') &&
        !e.includes('[Settings]')
    );
    expect(realErrors).toHaveLength(0);
  });
});
