/**
 * Playwright spec — TariffCard source badge
 *
 * All tests run at 375px (configured globally in playwright.config.ts).
 * No backend required — the WebSocket and all REST API calls are intercepted
 * with route mocks so the TariffCard receives controlled tariff data.
 *
 * Knowledge applied:
 *   K025 — ESM __dirname reconstruction via fileURLToPath
 *   K026 — filter /api/* 502 console noise from vite preview proxy
 *   K039 — filter [Settings] component-level console.error re-logs
 *   K043 — mock /api/tariff/schedule to avoid TariffCard noise
 *   K045 — mock /api/setup/status → {setup_complete: true} to prevent wizard redirect
 */
import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'url';
import * as path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ---------------------------------------------------------------------------
// Mock payload builders
// ---------------------------------------------------------------------------

/** Full WsPayload shape — only tariff.source varies between tests */
function makeWsPayload(source: "live" | "hardcoded" | undefined) {
  const tariff: Record<string, unknown> = {
    effective_rate_eur_kwh: 0.24,
    octopus_rate_eur_kwh: 0.24,
    modul3_rate_eur_kwh: null,
  };
  if (source !== undefined) {
    tariff.source = source;
  }

  return JSON.stringify({
    pool: {
      combined_soc_pct: 75,
      huawei_soc_pct: 75,
      victron_soc_pct: 75,
      huawei_available: true,
      victron_available: true,
      control_state: 'IDLE',
      huawei_discharge_setpoint_w: 0,
      victron_discharge_setpoint_w: 0,
      combined_power_w: 0,
      huawei_charge_headroom_w: 0,
      victron_charge_headroom_w: 0,
      timestamp: Date.now() / 1000,
      grid_charge_slot_active: false,
      evcc_battery_mode: 'normal',
    },
    devices: {
      huawei: {
        available: true,
        pack1_soc_pct: 75,
        pack1_power_w: 0,
        pack2_soc_pct: null,
        pack2_power_w: null,
        total_soc_pct: 75,
        total_power_w: 0,
        max_charge_w: 2500,
        max_discharge_w: 2500,
        master_pv_power_w: null,
        slave_pv_power_w: null,
      },
      victron: {
        available: true,
        soc_pct: 75,
        battery_power_w: 0,
        l1_power_w: 0,
        l2_power_w: 0,
        l3_power_w: 0,
        l1_voltage_v: 230,
        l2_voltage_v: 230,
        l3_voltage_v: 230,
        grid_power_w: null,
        grid_l1_power_w: null,
        grid_l2_power_w: null,
        grid_l3_power_w: null,
        consumption_w: null,
        pv_on_grid_w: null,
      },
    },
    tariff,
    optimization: null,
    evcc: null,
    ha_mqtt_connected: true,
    loads: null,
  });
}

/** Wire all route mocks for a given page */
async function setupRoutes(
  page: import('@playwright/test').Page,
  source: "live" | "hardcoded" | undefined
) {
  // K045: prevent setup wizard redirect
  await page.route('**/api/setup/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ setup_complete: true }),
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

  // Mock REST fallback endpoints (useEmsState)
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

  // Mock WebSocket — send one WsPayload frame on connect.
  await page.routeWebSocket('**/api/ws/state', async (ws) => {
    ws.send(makeWsPayload(source));
  });
}

/** Console error filter: exclude known infrastructure noise (K026, K039, K043) */
function isRealError(text: string): boolean {
  return (
    !text.includes('WebSocket') &&
    !text.includes('ws://') &&
    !text.includes('Failed to load resource') &&
    !text.includes('/api/') &&
    !text.includes('[TariffCard]') &&
    !text.includes('[Settings]') &&
    !text.includes('[OptimizationCard]')
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('TariffCard — source badge', () => {

  test('tariff source badge shows Live ⚡ when source is live', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 'live');
    await page.goto('/');

    // Wait for TariffCard to render
    await expect(page.locator('.tariff-card')).toBeVisible({ timeout: 15_000 });

    const badge = page.locator('[data-testid="tariff-source-badge"]');
    await expect(badge).toBeVisible({ timeout: 10_000 });
    await expect(badge).toContainText('Live');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('tariff source badge shows Hardcoded when source is hardcoded', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 'hardcoded');
    await page.goto('/');

    await expect(page.locator('.tariff-card')).toBeVisible({ timeout: 15_000 });

    const badge = page.locator('[data-testid="tariff-source-badge"]');
    await expect(badge).toBeVisible({ timeout: 10_000 });
    await expect(badge).toContainText('Hardcoded');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('tariff source badge absent when source undefined', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, undefined);
    await page.goto('/');

    await expect(page.locator('.tariff-card')).toBeVisible({ timeout: 15_000 });

    // Badge must be completely absent from the DOM
    await expect(page.locator('[data-testid="tariff-source-badge"]')).toHaveCount(0);

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

});
