# Frontend Quarantine

These pages are disabled from the active router but preserved for future use.

None of these pages are imported in `App.tsx`. Their routes show a "quarantined" placeholder.

## What is here

### `agents.tsx`
Wired to the orchestrator backend (`/api/copilot` and agent execution endpoints).
The orchestrator is quarantined in `backend/quarantine/`. This page will be revived
when the BrenusBridge copilot layer is built.

### `benchmarks.tsx`
Wired to the benchmarks harness (`backend/benchmarks/`), which was deleted.
No reusable parts. Recoverable from git history if needed.

### `copilot.tsx`
Wired to the copilot backend (`/api/copilot/chat`), which depended on `legacy_app._copilot`.
The legacy stack is removed. The conversation memory pattern in `backend/quarantine/copilot.py`
is worth reviving for a BrenusBridge copilot endpoint.

## Revival protocol

1. Confirm the Brenus integration sprint requires the page
2. Update the backend endpoint it depends on (wire to new substrate primitives)
3. Move the page back to `src/pages/`
4. Add the route back to `App.tsx` navItems and Switch
5. Remove from this quarantine folder
