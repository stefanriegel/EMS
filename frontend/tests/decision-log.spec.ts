import { test, expect } from '@playwright/test';

test('decision log card renders empty state', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');

  // Decision log card visible
  await expect(page.locator('[data-testid="decision-log-card"]')).toBeVisible({ timeout: 10_000 });

  // Empty state text visible (no backend = no decisions)
  await expect(page.locator('text=No dispatch decisions yet')).toBeVisible();

  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes('WebSocket') &&
      !e.includes('ws://') &&
      !e.includes('Failed to load resource') &&
      !e.includes('/api/')
  );
  expect(realErrors).toHaveLength(0);
});
