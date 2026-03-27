---
name: add-react-component
description: Creates a new React component in frontend/src/components/ with TypeScript types in types.ts, CSS classes in index.css, and a Playwright E2E spec in frontend/tests/. Use when user says 'add card', 'new component', 'create dashboard widget', or 'add UI element'. Do NOT use for backend work or hook creation. Follows PascalCase.tsx naming, data-testid attributes, and 375px mobile-first viewport testing.
---
# Add React Component

## Critical

- **Read before writing**: Read `frontend/src/types.ts`, `frontend/src/index.css`, `frontend/src/App.tsx`, and at least one existing component in `frontend/src/components/` before creating anything. Never guess at existing patterns.
- **No barrel files**: Import directly from the component file. Never create or modify barrel export files — this project uses direct imports exclusively.
- **Null, not undefined**: All nullable model fields use `| null`, never `| undefined`.
- **data-testid required**: Every component root element and every value-display element must have a `data-testid` attribute.
- **Mobile-first**: All CSS must work at 375px viewport width. Playwright tests run at `{ width: 375, height: 812 }`.

## Instructions

### Step 1: Define types in `frontend/src/types.ts`

Add the interface for any new API payload the component consumes. Follow the existing pattern:

```typescript
export interface NewPayload {
  some_field: number;
  optional_field: string | null;
}
```

- Use `snake_case` for fields (matches Python backend JSON).
- Use `| null` for optional fields, never `| undefined`.
- Place the new interface near related existing types.

**Verify**: `cd frontend && npx tsc --noEmit` passes.

### Step 2: Create the component file

Create `frontend/src/components/NewCard.tsx` using **PascalCase** naming.

Follow the structure used in `frontend/src/components/EnergyFlowCard.tsx` and other existing components:

```typescript
import type { NewPayload } from "../types";

interface Props {
  data: NewPayload | null;
}

function formatValue(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1);
}

export function NewCard({ data }: Props) {
  if (!data) return null;

  return (
    <section className="card new-card" data-testid="new-card">
      <h2 className="card-title">New Card</h2>
      <div className="new-card-content">
        <span data-testid="new-value">{formatValue(data.some_field)}</span>
      </div>
    </section>
  );
}
```

Key patterns:
- `export function` (named export, not default).
- Props interface defined in the same file, not exported.
- Helper formatters as module-level functions above the component.
- Root element is `<section className="card component-name">` with `data-testid`.
- Return `null` when data is missing — graceful degradation.
- `h2.card-title` for the card heading.

**Verify**: `npx tsc --noEmit` passes.

### Step 3: Add CSS classes in `frontend/src/index.css`

Append component-specific styles. Use the project's CSS variable tokens:

```css
/* ── New Card ── */
.new-card-content {
  display: grid;
  gap: 0.5rem;
  font-family: var(--font-mono);
}
```

Available tokens: `--bg-primary: #0d1117`, `--bg-card: #161b22`, `--text-primary: #e6edf3`, `--text-secondary: #8b949e`, `--accent-green: #22c55e`, `--accent-red: #ef4444`, `--color-pv: #4ade80`, `--color-huawei: #f59e0b`, `--color-victron: #8b5cf6`, `--font-mono`, `--font-sans`.

Patterns:
- Base card styling comes from `.card` — don't redefine background, padding, or border-radius.
- BEM-like naming: `.new-card-content`, `.new-card-row`, `.new-card-value`.
- Conditional classes for state: `.new-card-value--positive`, `.new-card-value--negative`.

**Verify**: `npm run dev` renders the card without visual breakage at 375px.

### Step 4: Wire into App.tsx

Read `frontend/src/App.tsx` to find the `DashboardLayout` function. Import and place the component inside `<div className="dashboard-grid">`:

```typescript
import { NewCard } from "./components/NewCard";
// ...
<NewCard data={ws.data?.newPayload ?? null} />
```

Data flow pattern: WebSocket data arrives via `useEmsSocket` as `ws.data`. If the new data is on the WS payload, access it from `ws.data`. If it needs a separate fetch, create a custom hook (separate task — do not inline fetch logic in the component).

**Verify**: `npm run build` (`tsc -b && vite build`) succeeds with zero errors.

### Step 5: Create Playwright E2E spec

Create a new spec file in `frontend/tests/` using kebab-case naming (following the pattern in `frontend/tests/energy-flow.spec.ts` and `frontend/tests/battery-status.spec.ts`):

```typescript
import { test, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test("new card is visible at 375px mobile viewport with no console errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto("/");

  await expect(page.locator('[data-testid="new-card"]')).toBeVisible({ timeout: 10_000 });

  const screenshotDir = path.join(__dirname, "screenshots");
  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({
    path: path.join(screenshotDir, "new-card-375px.png"),
    fullPage: false,
  });

  const realErrors = consoleErrors.filter(
    (e) =>
      !e.includes("WebSocket") &&
      !e.includes("ws://") &&
      !e.includes("Failed to load resource") &&
      !e.includes("/api/")
  );
  expect(realErrors).toHaveLength(0);
});
```

Screenshot filename: `kebab-case-375px.png` matching the component name. Screenshots are saved to `frontend/tests/screenshots/`.

**Verify**: `cd frontend && npx playwright test` passes (requires `npm run preview` or dev server).

## Examples

**User says**: "Add a grid frequency card that shows the current Hz value"

**Actions**:
1. Add `GridFrequencyPayload` interface to `frontend/src/types.ts`
2. Create `frontend/src/components/GridFrequencyCard.tsx` with `data-testid="grid-frequency-card"` and `data-testid="grid-freq-value"`
3. Add `.grid-frequency-card-content` styles to `frontend/src/index.css`
4. Import `GridFrequencyCard` in `frontend/src/App.tsx`, place in `dashboard-grid`
5. Create a Playwright spec in `frontend/tests/` following the kebab-case naming convention with 375px screenshot test
6. Run `npm run build` and `npx playwright test` to verify

**Result**: New card renders at mobile width, screenshot saved, zero console errors, build passes.

## Common Issues

**`Cannot find module '../types'`**: You added the type to the wrong file. Types go in `frontend/src/types.ts`, not a co-located types file. There are no per-component type files.

**`Property 'newField' does not exist on type 'WsPayload'`**: The WebSocket payload type in `frontend/src/types.ts` needs the new field added to `WsPayload` (or whichever parent interface carries it). Check `frontend/src/hooks/useEmsSocket.ts` for the exact shape.

**Card doesn't appear**: Check `frontend/src/App.tsx` — the component must be inside `<div className="dashboard-grid">` in `DashboardLayout`. Also verify the data prop isn't `null` (which makes the component return `null`).

**Playwright test times out on `toBeVisible`**: The component returns `null` when data is missing. In preview mode there's no backend, so WS data is `null`. Either: (a) mock the API route in the test with `page.route()`, or (b) test for the card's absence and only screenshot when data is available.

**CSS looks wrong at 375px**: The `.card` base class handles responsive padding. Don't set fixed widths. Use `grid` or `flex` with `gap` and let the card fill its grid cell. Test with `npm run dev` and browser DevTools at 375px before committing.

**`ERR_MODULE_NOT_FOUND` in Playwright**: Tests use ES modules. Ensure the test file uses `import` (not `require`) and that `fileURLToPath` is imported for `__dirname`.