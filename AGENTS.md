# R3-PCR — Agent Working Guide

Django 6 customs Pre-Clearance Decision Support System. Deployed on Railway,
backed by Supabase PostgreSQL. Config module is `config/`; apps live under
`apps/` (namespaced `apps.accounts`, `apps.shipments`, etc.); templates in
`templates/` at root; static in `static/`.

## Environment & safety
- Local `.env` points at a **separate dev** Supabase project `r3pcr-dev`
  (ref `cqhbouxniqsookoezego`) — isolated, safe to migrate/seed/test.
  **Production** is ref `hbvbxxmqbuhjcxnutdva`. Same pooler host; they differ by
  `DB_USER`. **Verify `DB_USER` before any destructive DB op.**
- Branch `main`. **Pushing to `main` triggers a Railway redeploy.** Commit
  freely; push only when you intend to deploy.
- Logins: `supervisor01 / Demo@1234` (also `consignee01-10`, `declarant01-03`).

## Run / test / lint
- Tests (offline, in-memory SQLite, ~100 tests):
  `python manage.py test --settings=config.settings_test`
- System check: `python manage.py check --settings=config.settings_test`
- Dead-code lint: `python -m pyflakes apps/ config/`
- Remove dead imports: `autoflake` is installed as a **local dev tool only**
  (not in requirements.txt). When using it, **exclude** the re-export shims and
  star-import hubs: `--exclude "__init__.py,common.py,analytics_sections.py"`.
- **Test-first for refactors.** Add/lock characterization tests before changing
  behavior, then prove the change behavior-preserving.

## Conventions
- **God-file splits**: large `views.py`/`ocr.py` files are split into packages
  (`views/` folder of cohesive submodules) with an `__init__.py` **re-export
  shim** so `from . import views` and cross-app imports stay unchanged. When
  splitting, preserve the public import surface (URLs + any external importers).
  Hubs use `from .common import *`; pyflakes "may be undefined, defined from
  star imports" on those is expected, not a bug.
- **Don't run whole-file `ftfy` on `.py`** — it strips accented regex ranges
  (`À-ÿ`) and adds BOMs. Targeted find/replace only for mojibake.
- **Frontend**: per-role templates (`templates/{consignee,declarant,supervisor,
  accounts}/`), pure CSS (no Bootstrap) — `static/css/main.css` is the dark-slate
  base (`#0f172a`); per-page CSS alongside. The supervisor dashboard KPI grid is
  a 12-col CSS grid in `templates/supervisor/analytics.html`.
- **Commits**: small and focused, one concern each. Imperative subject line.

## Gotchas
- Tests that hit `compute_shipment`/analytics must seed `SystemConfig` keys
  `exchange_rates_last_success` AND `exchange_rates_last_attempt` with
  `timezone.localdate().isoformat()` in `setUp`, or `ensure_daily_exchange_rates()`
  makes a live network call that overwrites `rate_USD` (flaky across date rollover).
- **Clean up scratch files** (e.g. rendered `_report*.png`, `_preview/`) before
  committing — they belong in `.gitignore`, never in a commit.
- Migrations are real files now (run `makemigrations --check --dry-run` to verify
  models and migrations are in sync before committing model changes).

## Known pending (owner task, not code)
- Rotate exposed secrets: `SECRET_KEY`, Supabase password + S3 keys,
  Resend/Gmail app password — in both `.env` and Railway.
