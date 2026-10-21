# Direct network collection implementation plan

**Goal:** Run topology and network policy collection without maintenance playbooks.

**Architecture:** Manual requests create a collection run and launch the existing device-action pipeline in the management backend. A backend listener scans persisted cron cursors, locks each plan, and creates a scheduled run in the same transaction that advances its cursor. Pending direct runs are retried after a process restart. Existing stage claims, heartbeats, cancellation and timeout handling remain authoritative.

**Constraints:** Keep tenant boundaries and action run IDs. Preserve historical topology snapshot provenance and internal action APIs. Retire old collection cron jobs before using the new scheduler. Preserve unrelated workspace changes.

- [x] Add regression coverage for direct manual collection and topology stage sequencing, then implement both domain launchers.
- [x] Add schedule regressions for timezone, due claims, overlap, restart recovery and legacy timer removal; implement shared scheduler, model fields and additive migration.
- [x] Remove plan-save playbook validation and frontend gating; stop provisioning the two collection playbooks in installation packages.
- [x] Run backend collection tests, frontend contracts/type checks, package tests and Python compile checks; review runtime races and delivery boundaries.
- [ ] Deliver tested changes to origin and trigger test CI/CD with fresh tags when needed; verify terminal builds and runtime version before reporting deployment.
