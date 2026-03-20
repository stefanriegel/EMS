import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test('login page is visible at /login with no console errors', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  // Navigate directly to /login
  await page.goto('/login');

  // Login form must be visible
  await expect(page.locator('[data-testid="login-form"]')).toBeVisible({ timeout: 10_000 });

  // Password input and login button must be present
  await expect(page.locator('[data-testid="password-input"]')).toBeVisible();
  await expect(page.locator('[data-testid="login-btn"]')).toBeVisible();
  await expect(page.locator('[data-testid="login-btn"]')).toContainText('Login');

  // Take screenshot
  const screenshotDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, 'login-375px.png'),
    fullPage: false,
  });
  expect(fs.existsSync(path.join(screenshotDir, 'login-375px.png'))).toBe(true);

  // Filter known infrastructure noise (no backend running in preview env)
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
