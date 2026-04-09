# Bug: Monorepo Path Alias Resolution Fails

**Created:** 2026-04-09
**Severity:** Medium -- causes 0 importers for heavily-imported files in monorepos
**Discovered while:** Analyzing `imbhargav5/nextbase-ultimate` (Turborepo monorepo)

---

## Problem

CodeStats fails to resolve TypeScript path aliases (`@/*`) when the `tsconfig.json` defining those aliases is in a nested monorepo package, not at the repo root.

### Reproduction

1. Clone a Turborepo monorepo where `tsconfig.json` with path aliases lives at `apps/web/tsconfig.json`
2. Run `codestats init` from the repo root
3. Run `codestats risk` on a file that other files import via `@/` alias

### Expected

The file shows correct importer count based on resolved `@/` imports.

### Actual

The file shows **0 importers** even though 12+ files import it via `import { X } from "@/payments/stripe-payment-gateway"`.

## Details

### The tsconfig chain

```
repo-root/
  apps/web/tsconfig.json          <-- defines "@/*": ["./src/*"]
    extends: ../../packages/typescript-config/tsconfig.base.json
  apps/web/src/payments/stripe-payment-gateway.ts   <-- the target file
  apps/web/src/data/user/billing.tsx                <-- imports via @/payments/...
```

### What codestats sees

```
codestats risk apps/web/src/payments/stripe-payment-gateway.ts

  Importers (0):        <-- WRONG, should be 12
  Dependencies (1):
    -> apps/web/src/payments/abstract-payment-gateway.ts   <-- this one resolves (relative import?)
```

### What grep confirms

```bash
grep -r "import.*StripePaymentGateway" apps/web/src/
# Returns 12 files, all using: import { StripePaymentGateway } from "@/payments/stripe-payment-gateway"
```

### Why the abstract gateway resolves but importers don't

`stripe-payment-gateway.ts` imports `abstract-payment-gateway.ts` with a relative import or a resolvable path. But the 12 files that import `stripe-payment-gateway.ts` all use the `@/` alias, which codestats can't resolve because it only reads `tsconfig.json` at the repo root (if at all), not in `apps/web/`.

## Version Info

- codestats 0.1.0 (installed from `git+https://github.com/Abdo-El-Mobayad/codestats.git`)
- The pip registry has a different package called `CodeStats` (v1.1.0 by Niek Keijzer) -- a Code::Stats API wrapper, NOT this tool. `pip install codestats` installs the wrong package. `pip install git+https://...` is required.

## Impact

In the Nextbase analysis:

- Internal edges: 426 (with v0.1.0, was 321 with the wrong package)
- But still missing ~100+ edges from unresolved `@/` aliases
- Dead code findings inflated (480) because files imported via `@/` appear to have 0 importers

## Suggested Fix

When parsing a monorepo, codestats should:

1. Walk up from each file to find the nearest `tsconfig.json` (not just repo root)
2. If a tsconfig has `extends`, follow the chain
3. Resolve `paths` aliases relative to the tsconfig's `baseUrl` or directory
4. OR: accept a `--tsconfig` flag to point at a specific tsconfig: `codestats init --tsconfig apps/web/tsconfig.json`

Option 4 is simplest and handles 90% of cases since most monorepos have one primary app with the path aliases.

## Workaround

For now, run codestats from inside the app directory (`apps/web/`) rather than the repo root. This may cause issues with git history analysis but should fix import resolution.

Not tested -- just a hypothesis.
