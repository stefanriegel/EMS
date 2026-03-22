import { test, expect } from '@playwright/test';

test('battery status card renders with dual battery layout', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  // Battery status card must be visible (with null data = N/A state)
  await expect(page.locator('[data-testid="battery-status-card"]')).toBeVisible({ timeout: 10_000 });

  // Both battery sub-cards present
  await expect(page.locator('[data-testid="huawei-battery"]')).toBeVisible();
  await expect(page.locator('[data-testid="victron-battery"]')).toBeVisible();

  // Filter harmless errors (no backend in preview)
  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
