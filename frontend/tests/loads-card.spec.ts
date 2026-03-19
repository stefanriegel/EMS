import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('loads card is visible with null-state (no backend) and no console errors', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  // LoadsCard must be present in the DOM — always visible (never hidden)
  const loadsCard = page.locator('[data-testid="loads-card"]');
  await expect(loadsCard).toBeVisible({ timeout: 10_000 });

  // With no backend, loads=null → unavailable badge should be shown
  const badgeEl = loadsCard.locator('.badge');
  await expect(badgeEl).toBeVisible();

  // Take screenshot for visual record
  const screenshotDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'loads-card-null.png'),
    fullPage: false,
  });

  expect(fs.existsSync(path.join(screenshotDir, 'loads-card-null.png'))).toBe(true);

  // Filter known harmless noise from missing backend
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
