/**
 * Playwright spec — OptimizationCard solar forecast line and EVopt badge
 *
 * All tests run at 375px (configured globally in playwright.config.ts).
 * No backend required — the WebSocket and all REST API calls are intercepted
 * with route mocks so the OptimizationCard receives real optimization data.
 *
 * Knowledge applied:
 *   K025 — ESM __dirname reconstruction via fileURLToPath
 *   K026 — filter /api/* 502 console noise from vite preview proxy
 *   K039 — filter [Settings] component-level console.error re-logs
 *   K043 — mock /api/tariff/schedule to avoid TariffCard noise
 *   K045 — mock /api/setup/status → {setup_complete: true} to prevent wizard redirect
 */
import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock payload builders
// ---------------------------------------------------------------------------

/** Full WsPayload shape — only optimization.reasoning fields vary between tests */
function makeWsPayload(tomorrow_solar_kwh: number, evopt_status: string) {
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
    optimization: {
      slots: [],
      reasoning: {
        text: 'Test fixture: charge overnight using off-peak tariff.',
        tomorrow_solar_kwh,
        expected_consumption_kwh: 10.0,
        charge_energy_kwh: 5.0,
        cost_estimate_eur: 1.20,
        evopt_status,
      },
      computed_at: '2025-01-01T00:00:00Z',
      stale: false,
    },
    evcc: null,
    ha_mqtt_connected: true,
    loads: null,
  });
}

/** Wire all route mocks for a given page with the given optimization parameters */
async function setupRoutes(
  page: import('@playwright/test').Page,
  tomorrow_solar_kwh: number,
  evopt_status: string
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

  // Mock REST fallback endpoints (useEmsState) — optimization is NOT delivered via REST,
  // but we need these to avoid noisy fetch errors from the FallbackConsumer.
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
  // optimization.reasoning is the primary test variable.
  // The routeWebSocket handler is invoked when the page opens the connection —
  // call ws.send() directly (no onopen; the handler IS the open event).
  await page.routeWebSocket('**/api/ws/state', async (ws) => {
    ws.send(makeWsPayload(tomorrow_solar_kwh, evopt_status));
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

test.describe('OptimizationCard — solar forecast line and EVopt badge', () => {

  test('solar forecast visible with correct value when tomorrow_solar_kwh > 0', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 8.4, 'Heuristic');
    await page.goto('/');

    // Wait for the OptimizationCard section to render with schedule data
    await expect(page.locator('.optimization-card')).toBeVisible({ timeout: 15_000 });

    // Solar forecast should appear
    const forecast = page.locator('[data-testid="opt-solar-forecast"]');
    await expect(forecast).toBeVisible({ timeout: 10_000 });
    await expect(forecast).toContainText('8.4 kWh');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('EVopt badge shows "Heuristic" when evopt_status is Heuristic', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 8.4, 'Heuristic');
    await page.goto('/');

    await expect(page.locator('.optimization-card')).toBeVisible({ timeout: 15_000 });

    const badge = page.locator('[data-testid="opt-evopt-badge"]');
    await expect(badge).toBeVisible({ timeout: 10_000 });
    await expect(badge).toHaveText('Heuristic');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('solar forecast absent when tomorrow_solar_kwh === 0', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 0, 'Heuristic');
    await page.goto('/');

    await expect(page.locator('.optimization-card')).toBeVisible({ timeout: 15_000 });

    // Badge must still be present (unconditional render)
    await expect(page.locator('[data-testid="opt-evopt-badge"]')).toBeVisible({ timeout: 10_000 });

    // Forecast element must be absent from DOM
    await expect(page.locator('[data-testid="opt-solar-forecast"]')).toHaveCount(0);

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

  test('EVopt badge shows "EVopt ✓" when evopt_status is Optimal', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await setupRoutes(page, 5.0, 'Optimal');
    await page.goto('/');

    await expect(page.locator('.optimization-card')).toBeVisible({ timeout: 15_000 });

    const badge = page.locator('[data-testid="opt-evopt-badge"]');
    await expect(badge).toBeVisible({ timeout: 10_000 });
    await expect(badge).toHaveText('EVopt ✓');

    const realErrors = consoleErrors.filter(isRealError);
    expect(realErrors).toHaveLength(0);
  });

});
