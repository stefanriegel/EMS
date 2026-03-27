import { test, expect } from '@playwright/test';

/**
 * Full mock PoolState with cross-charge fields for WebSocket interception.
 *
 * K052: pool data flows via WebSocket (/api/ws/state), not REST /api/state.
 * The route handler IS the onopen callback — call ws.send() directly.
 * K045: mock /api/setup/status → {setup_complete: true} to prevent wizard redirect.
 * K043: mock /api/tariff/schedule to suppress TariffCard fetch noise.
 */
function mockPool(overrides: Record<string, unknown> = {}) {
  return {
    combined_soc_pct: 50,
    huawei_soc_pct: 50,
    victron_soc_pct: 50,
    huawei_available: true,
    victron_available: true,
    control_state: 'IDLE',
    huawei_discharge_setpoint_w: 0,
    victron_discharge_setpoint_w: 0,
    combined_power_w: 0,
    huawei_charge_headroom_w: 5000,
    victron_charge_headroom_w: 5000,
    timestamp: Date.now() / 1000,
    grid_charge_slot_active: false,
    evcc_battery_mode: 'normal',
    huawei_role: 'IDLE',
    victron_role: 'IDLE',
    pool_status: 'NORMAL',
    huawei_effective_min_soc_pct: 10,
    victron_effective_min_soc_pct: 10,
    cross_charge_active: false,
    cross_charge_waste_wh: 0,
    cross_charge_episode_count: 0,
    ...overrides,
  };
}

function mockWsPayload(poolOverrides: Record<string, unknown> = {}) {
  return {
    pool: mockPool(poolOverrides),
    devices: {
      huawei: {
        available: true,
        pack1_soc_pct: 50,
        pack1_power_w: 0,
        pack2_soc_pct: null,
        pack2_power_w: null,
        total_soc_pct: 50,
        total_power_w: 0,
        max_charge_w: 5000,
        max_discharge_w: 5000,
        master_pv_power_w: 0,
        slave_pv_power_w: null,
      },
      victron: {
        available: true,
        soc_pct: 50,
        battery_power_w: 0,
        l1_power_w: 0,
        l2_power_w: 0,
        l3_power_w: 0,
        l1_voltage_v: 230,
        l2_voltage_v: 230,
        l3_voltage_v: 230,
        grid_power_w: 0,
        grid_l1_power_w: 0,
        grid_l2_power_w: 0,
        grid_l3_power_w: 0,
        consumption_w: 500,
        pv_on_grid_w: 0,
      },
    },
    tariff: { effective_rate_eur_kwh: 0.30, octopus_rate_eur_kwh: 0.30, modul3_rate_eur_kwh: null },
    optimization: null,
    evcc: null,
    ha_mqtt_connected: false,
    loads: null,
  };
}

/** Wire all route mocks for a given page */
async function setupRoutes(
  page: import('@playwright/test').Page,
  poolOverrides: Record<string, unknown> = {}
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

  // Mock REST fallback — needed to suppress fetch errors from FallbackConsumer
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

  // K052: mock WebSocket — send one WsPayload frame on connect.
  // The handler IS the onopen callback; call ws.send() directly.
  await page.routeWebSocket('**/api/ws/state', async (ws) => {
    ws.send(JSON.stringify(mockWsPayload(poolOverrides)));
  });
}

test('cross-charge badge is hidden by default (no active cross-charge)', async ({ page }) => {
  await setupRoutes(page, { cross_charge_active: false });

  await page.goto('/');
  await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });
  // Cross-charge badge must NOT be visible when cross_charge_active is false
  await expect(page.locator('[data-testid="cross-charge-badge"]')).not.toBeVisible();
});

test('cross-charge badge appears when cross_charge_active is true', async ({ page }) => {
  await setupRoutes(page, {
    cross_charge_active: true,
    cross_charge_waste_wh: 1500,
    cross_charge_episode_count: 3,
  });

  await page.goto('/');
  await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });

  // Badge should be visible with correct text
  await expect(page.locator('[data-testid="cross-charge-badge"]')).toBeVisible();
  await expect(page.locator('[data-testid="cross-charge-badge"]')).toContainText('Cross-Charge');
});

test('cross-charge history section appears in OptimizationCard when episodes > 0', async ({ page }) => {
  await setupRoutes(page, {
    cross_charge_active: false,
    cross_charge_waste_wh: 1500,
    cross_charge_episode_count: 3,
  });

  await page.goto('/');
  await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });

  // History section should be visible with correct stats
  const history = page.locator('[data-testid="cross-charge-history"]');
  await expect(history).toBeVisible();
  await expect(history).toContainText('Episodes: 3');
  await expect(history).toContainText('Waste: 1.50 kWh');
});
