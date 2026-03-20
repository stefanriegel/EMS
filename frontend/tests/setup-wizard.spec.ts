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
