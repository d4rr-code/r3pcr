# R3-PCR Optimization Program

This document is the working scope and decision record for optimizing R3-PCR. A
feature is removed only after routes, imports, templates, static references, and
tests show that it is unused.

## Baseline (2026-09-23)

- Django system check: clean.
- Test suite: 217 tests passing.
- Model/migration check: no changes detected.
- Architecture: Django 6, eight active domain/role apps after cleanup, root
  templates, and role-specific static assets.
- Main maintenance hotspots: supervisor analytics, intelligence/report exports,
  large declarant/computation templates, and duplicated account/public assets.

## Feature Classification

| Tier | Feature area | Decision | Reason |
| --- | --- | --- | --- |
| Critical | Authentication, OTP, registration, role access | Keep and harden | Security and entry point for every workflow |
| Critical | Shipment submission and document management | Keep and optimize | Core consignee workflow and source data |
| Critical | Declarant queue, claim, processing, and status lifecycle | Keep and optimize | Core operational workflow |
| Critical | Duty/tax computation, HS classification, OCR, MCDA | Keep and optimize | Primary decision-support capability |
| Critical | Supervisor analytics and exports | Keep and prioritize | Primary oversight and reporting capability |
| Important | Audit trail, notifications, issue reports | Keep | Accountability and operational support |
| Important | Tariff/configuration management | Keep | Governs computations and classification |
| Important | Shipment tracking and records exports | Keep | Customer visibility and operations |
| Supporting | Memos, approved feedback display, public about/legal pages | Retain, low priority | Useful but outside the optimization critical path |
| Removed | Empty `apps.analytics` shell | Remove | No models, URLs, migrations, views, or importers; real analytics is supervisor-owned |
| Removed | Legacy `supervisor/dashboard.html` | Remove | No renderer or template reference; active dashboard is `analytics.html` |
| Removed | `static/images/main bg 1.png` | Remove | Unreferenced 13.1 MB asset |
| Removed | `CODEX_FRONTEND_BRIEF.md` | Remove | Unreferenced task brief with obsolete branch, PR, test-count, and template instructions |

Do not classify migration history, view-package re-export shims, star-import
hubs, tariff workbooks, or static computation templates as dead code based on a
simple import scan. They have framework or runtime ownership.

## Workstreams

### 1. Analytics performance and correctness

- [x] Replace the live status poll's per-status queries with one grouped query.
- [x] Add a one-query regression test for the polling endpoint.
- [x] Consolidate dashboard shipment-type, feedback, landed-cost, status KPI,
  user, and MCDA totals into grouped aggregates.
- [x] Remove the dashboard's unused shipment-table query and hidden legacy
  search/status state.
- [x] Add a stable 21-query budget for the full analytics page.
- [x] Reduce export report construction from 57 queries with nine declarants to
  13 fixed queries, backed by a 13-query regression budget.
- [x] Profile export report construction with 420 local shipments: about 200 ms
  after warm-up, with the query count remaining fixed at 13.
- [x] Extract 765 lines of embedded analytics CSS into a cacheable static file
  without changing the rendered dashboard contract.
- [x] Move 422 lines of template-data-dependent analytics JavaScript into a
  static module backed by a safely serialized configuration payload.
- [x] Vendor pinned Chart.js 4.4.0 locally so dashboard charts do not depend on
  public CDN availability at runtime.
- [x] Add truthful freshness and stale-data states for live status polling.
- [x] Add preparing, success, and retry feedback for analytics and intelligence
  export actions.

### 2. Code and asset cleanup

- [x] Remove the empty analytics app and legacy dashboard template.
- [x] Remove the unreferenced 13.1 MB image.
- [x] Review linter findings and distinguish real dead imports from required
  re-export/star-import patterns.
- [x] Consolidate byte-identical public/account image copies after updating all
  template and CSS references.
- [x] Begin splitting oversized templates by cohesive partial or static resource,
  beginning with analytics and computation.

### 3. Frontend improvement

- [x] Establish shared tokens for spacing, controls, tables, status colors, and
  responsive gutters across the three role shells; migrate their common card,
  form, button, table, message, and status primitives to the shared values.
- [x] Improve analytics scan order, filter feedback, responsive KPI layout, and
  live-status accessibility.
- [x] Audit the consignee submission and declarant processing flows for keyboard,
  validation, empty, loading, and error states; add server validation, responsive
  submission controls, submit feedback, and accessible process dialogs.
- [x] Verify analytics at 1280, 768, and 375 pixels with no horizontal overflow
  or browser console errors.
- [x] Verify representative consignee, declarant, and supervisor workflows at
  desktop and 375 pixels with shared tokens loaded, responsive gutters applied,
  no horizontal overflow, and no browser console warnings or errors.

### 4. Declarant workflow

- [x] Profile the queue manager with realistic data and document-heavy rows.
- [x] Reduce queue rendering from 47 to 15 queries by preloading only the
  paginated document collections and joining computation data.
- [x] Reduce shipment processing from 27 to 14 queries by reusing one document
  collection and joining computation, advisory, and status-log authors.
- [x] Add query budgets for the queue and process screens.
- [x] Move overdue-email dispatch out of queue page rendering into the explicit
  `send_overdue_alerts` management command.
- [ ] Schedule `send_overdue_alerts --apply` daily in Railway after deployment.
- [x] Make shipment claiming POST-only and atomic so concurrent declarants
  cannot overwrite ownership or duplicate status history.
- [x] Enforce the declarant's forward-only customs lifecycle and reject status
  skips, reversals, and duplicate no-op history entries.
- [x] Remove unreachable legacy billing panels guarded by a status that does not
  exist in the shipment model; active billing documents remain in the current
  consolidated document sections.

### 5. Reliability and delivery

- [x] Add focused tests before each behavior-changing refactor.
- [x] Run system check, full tests, migration drift check, and dead-code lint for
  every optimization batch.
- [x] Keep commits small by concern; do not push `main` until deployment is
  intended.
- [ ] Rotate the known exposed secrets as a separate owner task.

## Final Verification (2026-09-26)

- Full suite: 234 tests passing in the offline test configuration.
- Django system check: no issues.
- Model/migration drift check: no changes detected.
- Diff whitespace check: clean.
- Dead-code lint: reviewed; remaining findings are the documented re-export
  shims and star-import hubs required by the current package architecture.
- Representative consignee, declarant, and supervisor pages: verified at desktop
  and 375 pixels with no horizontal overflow or browser console warnings/errors.
- Delivery: changes split into focused local commits; nothing pushed or deployed.

## Recommended Delivery Order

1. Finish analytics query budgeting and export profiling.
2. Extract and improve the analytics frontend while preserving tests.
3. Consolidate duplicated static assets.
4. Audit and optimize computation/declarant high-complexity screens.
5. Apply shared frontend tokens and accessibility fixes across roles.
6. Run full regression and visual verification, then prepare focused commits.

## Definition of Done

The program is complete when critical workflows have characterization coverage,
analytics has measured query and export budgets, dead code/assets have verified
zero references before removal, role interfaces pass desktop/mobile visual QA,
and the full system/test/migration gates pass while remaining lint exceptions are
documented architectural shims rather than actionable dead code.
