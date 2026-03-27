/**
 * Playwright spec — LoadsCard entity fields
 *
 * All tests run at 375px (configured globally in playwright.config.ts).
 * No backend required — the WebSocket and all REST API calls are intercepted
 * with route mocks so the LoadsCard receives controlled loads data.
 *
 * Knowledge applied:
 *   K025 — ESM __dirname reconstruction via fileURLToPath
 *   K026 — filter /api/* 502 console noise from vite preview proxy
 *   K039 — filter [Settings] component-level console.error re-logs
 *   K043 — mock /api/tariff/schedule to avoid TariffCard noise
 *   K045 — mock /api/setup/status → {setup_complete: true} to prevent wizard redirect
 */
import { test, expect } from '@playwright/test';
import type { LoadsPayload } from '../src/types';

// ---------------------------------------------------------------------------
// Mock payload builders
// ---------------------------------------------------------------------------

/** Full WsPayload shape — only loads varies between tests */
function makeWsPayload(loads: LoadsPayload | null) {
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
    tariff: { effective_rate_eur_kwh: 0.24, octopus_rate_eur_kwh: 0.24, modul3_rate_eur_kwh: null },
    optimization: null,
    evcc: null,
    ha_mqtt_connected: true,
    loads,
  });
}

/** Wire all route mocks for a given page */
async function setupRoutes(
  page: import('@playwright/test').Page,
  loads: LoadsPayload | null
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
    ws.send(makeWsPayload(loads));
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
    !text.includes('[OptimizationCard]') &&
    !text.includes('[LoadsCard]')
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('LoadsCard — entity fields', () => {

  test('loads card shows all entity values when populated', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    const loadsData: LoadsPayload = {
      heat_pump_power_w: 1200,
      cop: 3.45,
      outdoor_temp_c: 5.2,
      flow_temp_c: 42.1,
      return_temp_c: 38.7,
      hausverbrauch_w: 3500,
      steuerbare_w: 1800,
      base_w: 1700,
      available: true,
    };

    await setupRoutes(page, loadsData);
    await page.goto('/');

    // Wait for the loads card to render
    await expect(page.locator('[data-testid="loads-card"]')).toBeVisible({ timeout: 15_000 });

    // Heat pump power
    await expect(page.locator('[data-testid="loads-heat-pump"]')).toContainText('1200 W');
    // COP formatted to 2 decimal places
    await expect(page.locator('[data-testid="loads-cop"]')).toContainText('3.45');
    // Outdoor temp formatted to 1 decimal place with °C
    await expect(page.locator('[data-testid="loads-outdoor-temp"]')).toContainText('5.2 °C');
    // Flow temp
    await expect(page.locator('[data-testid="loads-flow-temp"]')).toContainText('42.1 °C');
    // Return temp
    await expect(page.locator('[data-testid="loads-return-temp"]')).toContainText('38.7 °C');
    // Hausverbrauch
    await expect(page.locator('[data-testid="loads-hausverbrauch"]')).toContainText('3500 W');
    // Steuerbare
    await expect(page.locator('[data-testid="loads-steuerbare"]')).toContainText('1800 W');
    // Base
    await expect(page.locator('[data-testid="loads-base"]')).toContainText('1700 W');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('loads card shows dashes when all entity values are null', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    const loadsData: LoadsPayload = {
      heat_pump_power_w: null,
      cop: null,
      outdoor_temp_c: null,
      flow_temp_c: null,
      return_temp_c: null,
      hausverbrauch_w: null,
      steuerbare_w: null,
      base_w: null,
      available: false,
    };

    await setupRoutes(page, loadsData);
    await page.goto('/');

    // Wait for the loads card to render
    await expect(page.locator('[data-testid="loads-card"]')).toBeVisible({ timeout: 15_000 });

    // All value spans must show em-dash
    await expect(page.locator('[data-testid="loads-heat-pump"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-cop"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-outdoor-temp"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-flow-temp"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-return-temp"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-hausverbrauch"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-steuerbare"]')).toContainText('—');
    await expect(page.locator('[data-testid="loads-base"]')).toContainText('—');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

});
