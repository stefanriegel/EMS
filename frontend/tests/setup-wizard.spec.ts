import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('setup wizard is visible at /setup with no console errors', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  // Navigate directly to /setup — bypasses auto-redirect which needs a backend
  await page.goto('/setup');

  // The setup wizard root must be visible
  await expect(page.locator('[data-testid="setup-wizard"]')).toBeVisible({ timeout: 10_000 });

  // Step indicator must show "Step 1 of 6"
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 1 of 6');

  // Step title for step 1 is Modbus
  await expect(page.locator('.setup-step-title')).toContainText('Modbus');

  // Next → button should be visible
  await expect(page.locator('[data-testid="next-step-btn"]')).toBeVisible();

  // Take screenshot
  const screenshotDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'setup-wizard-step1-375px.png'),
    fullPage: false,
  });
  expect(fs.existsSync(path.join(screenshotDir, 'setup-wizard-step1-375px.png'))).toBe(true);

  // Filter known infrastructure noise from no-backend test environment
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});

test('setup wizard can step through all 6 steps', async ({ page }) => {
  await page.goto('/setup');

  // Start at step 1
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 1 of 6');

  // Step through steps 1–5 via Next → button
  for (let i = 1; i <= 5; i++) {
    await expect(page.locator('[data-testid="step-indicator"]')).toContainText(`Step ${i} of 6`);
    await page.locator('[data-testid="next-step-btn"]').click();
  }

  // Should now be on step 6 — Finish Setup button appears, no Next button
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 6 of 6');
  await expect(page.locator('[data-testid="finish-setup-btn"]')).toBeVisible();
  await expect(page.locator('[data-testid="next-step-btn"]')).toHaveCount(0);

  // Back button should navigate back
  await page.locator('.btn--ghost').click();
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 5 of 6');
});

test('step 2 shows Victron Modbus TCP title with port 502', async ({ page }) => {
  await page.goto('/setup');
  await page.locator('[data-testid="next-step-btn"]').click();
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 2 of 6');

  // Title says Victron Modbus TCP
  await expect(page.locator('.setup-step-title')).toContainText('Victron Modbus TCP');
  // Must NOT contain old MQTT title
  await expect(page.locator('.setup-step-title')).not.toContainText('Victron MQTT');
  // Port input defaults to 502
  await expect(page.locator('#victron_port')).toHaveValue('502');
});

test('step 2 Advanced toggle reveals unit ID fields', async ({ page }) => {
  await page.goto('/setup');
  await page.locator('[data-testid="next-step-btn"]').click();
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 2 of 6');

  // Unit ID fields are hidden inside collapsed details
  await expect(page.locator('#victron_system_unit_id')).not.toBeVisible();

  // Click the Advanced summary to expand
  await page.locator('.setup-advanced summary').click();

  // Unit ID fields are now visible with defaults
  await expect(page.locator('#victron_system_unit_id')).toBeVisible();
  await expect(page.locator('#victron_system_unit_id')).toHaveValue('100');
  await expect(page.locator('#victron_battery_unit_id')).toBeVisible();
  await expect(page.locator('#victron_battery_unit_id')).toHaveValue('225');
  await expect(page.locator('#victron_vebus_unit_id')).toBeVisible();
  await expect(page.locator('#victron_vebus_unit_id')).toHaveValue('227');
});

test('step 2 probe calls victron_modbus endpoint with success', async ({ page }) => {
  await page.goto('/setup');
  await page.locator('[data-testid="next-step-btn"]').click();

  // Fill in host
  await page.locator('#victron_host').fill('192.168.0.20');

  // Intercept the probe POST to victron_modbus
  await page.route('**/api/setup/probe/victron_modbus', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });

  // Click Test Connection
  await page.locator('button:has-text("Test Connection")').click();

  // Green badge should appear
  await expect(page.locator('.probe-badge--ok')).toBeVisible();
  await expect(page.locator('.probe-badge--ok')).toContainText('Connection OK');
});

test('step 2 probe shows amber warning on partial success', async ({ page }) => {
  await page.goto('/setup');
  await page.locator('[data-testid="next-step-btn"]').click();

  await page.locator('#victron_host').fill('192.168.0.20');

  await page.route('**/api/setup/probe/victron_modbus', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        warning: 'TCP connected, Modbus register read failed. Check unit IDs.',
      }),
    });
  });

  await page.locator('button:has-text("Test Connection")').click();

  // Amber warning badge appears
  await expect(page.locator('.probe-badge--warn')).toBeVisible();
  await expect(page.locator('.probe-badge--warn')).toContainText('TCP connected');
});

test('step 5 shows Modul3 grid-fee fields', async ({ page }) => {
  await page.goto('/setup');

  // Navigate to Step 5
  for (let i = 0; i < 4; i++) {
    await page.locator('[data-testid="next-step-btn"]').click();
  }
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 5 of 6');

  // Octopus section heading
  await expect(page.locator('h3:has-text("Octopus Go Rates")')).toBeVisible();

  // Modul3 section heading
  await expect(page.locator('h3:has-text("Modul3 Grid-Fee Windows")')).toBeVisible();

  // Modul3 fields exist
  await expect(page.locator('#modul3_surplus_start_min')).toBeVisible();
  await expect(page.locator('#modul3_deficit_rate_eur_kwh')).toBeVisible();
});

test('finish payload includes Modbus TCP and Modul3 fields', async ({ page }) => {
  await page.goto('/setup');

  // Navigate through all steps to step 6
  for (let i = 0; i < 5; i++) {
    await page.locator('[data-testid="next-step-btn"]').click();
  }
  await expect(page.locator('[data-testid="step-indicator"]')).toContainText('Step 6 of 6');

  // Intercept the finish POST
  let capturedPayload: Record<string, unknown> = {};
  await page.route('**/api/setup/complete', (route) => {
    const body = route.request().postDataJSON();
    capturedPayload = body;
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });

  // Click Finish Setup
  await page.locator('[data-testid="finish-setup-btn"]').click();

  // Wait for the route handler to fire
  await page.waitForTimeout(500);

  // Verify payload includes Modbus TCP fields
  expect(capturedPayload).toHaveProperty('victron_port', 502);
  expect(capturedPayload).toHaveProperty('victron_system_unit_id', 100);
  expect(capturedPayload).toHaveProperty('victron_battery_unit_id', 225);
  expect(capturedPayload).toHaveProperty('victron_vebus_unit_id', 227);

  // Verify payload includes Modul3 fields
  expect(capturedPayload).toHaveProperty('modul3_surplus_start_min');
  expect(capturedPayload).toHaveProperty('modul3_deficit_rate_eur_kwh');
});
