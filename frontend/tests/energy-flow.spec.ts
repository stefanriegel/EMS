import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('energy flow card is visible at 375px mobile viewport with no console errors', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  // Energy flow card must be visible even with no backend (WS disconnected = null pool = static grey nodes)
  await expect(page.locator('[data-testid="energy-flow-card"]')).toBeVisible({ timeout: 10_000 });

  // Verify key SVG elements are present
  const svgEl = page.locator('[data-testid="energy-flow-card"] svg');
  await expect(svgEl).toBeVisible();

  // Verify both battery nodes exist in the 5-node SVG
  await expect(page.locator('[data-testid="ef-huawei-node"]')).toBeVisible();
  await expect(page.locator('[data-testid="ef-victron-node"]')).toBeVisible();

  // Take screenshot and save to tests/screenshots/
  const screenshotDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'energy-flow-375px.png'),
    fullPage: false,
  });

  // Assert screenshot file was written
  expect(fs.existsSync(path.join(screenshotDir, 'energy-flow-375px.png'))).toBe(true);

  // Assert no console errors (WS connection refused is a warn/log, not error — acceptable)
  // Filter out known harmless noise from missing backend in preview-only test environment:
  //   - WebSocket connection failures (no backend running)
  //   - HTTP 502/503/fetch failures on /api/* routes (no backend running)
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
