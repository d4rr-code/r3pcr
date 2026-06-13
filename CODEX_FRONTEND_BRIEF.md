# Codex Brief — Frontend Polish (R3-PCR)

You are doing **frontend visual polish only** on the R3-PCR Django app. Read
`AGENTS.md` first for project conventions. This brief defines your scope.

## Hard boundary — do not cross
**You MAY edit only:**
- `static/**/*.css`
- inline `<style>` blocks inside `templates/**/*.html`

**You MUST NOT touch:**
- Any Python (`.py`) — no views, models, forms, settings, logic.
- Django template tags or expressions: leave every `{% ... %}` and `{{ ... }}`
  exactly as-is. Do not add/remove/reorder template logic, loops, or `{% url %}`.
- HTML structure that changes behavior: do not rename/remove element IDs, `name`
  attributes, `href`/`action`/form fields, or any `class`/`id` referenced by JS.
- JavaScript behavior (`<script>` blocks, `static/**/*.js`). CSS only.
- No new dependencies, no CSS frameworks (the project is intentionally pure CSS).

If a polish idea requires touching anything above, **stop and leave a note**
instead of doing it.

## Mission
Make the **role dashboards** look cleaner and more consistent: spacing,
alignment, visual hierarchy, and responsive behavior. Small, reviewable
changes — not a redesign.

## Targets (priority order)
1. `templates/supervisor/analytics.html` (inline `<style>`) + `templates/supervisor/dashboard.html`
2. `templates/declarant/dashboard.html` (inline `<style>`)
3. `templates/consignee/dashboard.html` + `static/consignee/css/dashboard.css`
4. Shared shells: `templates/supervisor/base_supervisor.html`,
   `templates/declarant/base_declarant.html`, `static/consignee/css/base.css`,
   `static/css/main.css`

## Polish checklist (per page)
- **Spacing:** consistent gaps/padding between cards, sections, headings.
- **Alignment:** cards/grids align to a shared baseline; no off-by-a-few-px drift.
- **Responsive:** test at 1280 / 768 / 375 px. Note that `static/css/main.css`
  and `static/accounts/css/registerpage.css` have **zero** media queries — add
  sensible breakpoints where things overflow or squish.
- **Consistency:** matching card radius, shadow, border, font sizes across the
  three role dashboards.

## Gotcha — dual styling systems
Styling is split: **consignee + accounts** use external `.css` files;
**declarant + supervisor + computation** keep CSS in **inline `<style>` blocks**
in the template. Edit whichever applies to the page you're on; don't try to
unify the two architectures (that's out of scope).

## Verify before each commit
- The page still renders: `python manage.py check`
- Test suite still green: `python manage.py test --settings=config.settings_test`
  (should stay at 100 passing — you shouldn't be affecting it, this just proves
  you didn't break a template).
- Clean up any scratch/preview files before committing.

## Branch & push policy
- Work on the **`feature/auth`** branch (it has been synced up to `main` for you).
  **Do NOT commit to `main`.**
- You MAY `git push origin feature/auth` — pushing a *branch* does NOT redeploy;
  only pushing/merging to `main` triggers a Railway redeploy.
- **Do NOT open a PR or merge to `main`.** The project owner reviews and merges
  `feature/auth → main`. Just commit/push your branch and report what you did.

## Commit style
- Small, focused commits, one page/concern each. Imperative subject.
  e.g. `Tighten supervisor dashboard card spacing`, `Add mobile breakpoints to declarant dashboard`.

## Out of scope (leave for later / a human)
- Refactoring the inline styles into external files.
- Any backend, data, or behavior change.
- Rotating secrets (owner task).
