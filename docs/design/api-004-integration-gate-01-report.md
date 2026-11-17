# SUPPORTOPS-API-004 — INTEGRATION-GATE-01 report

> Language: English (owner request during the task). Scope: M2 fix, owner decisions, G1–G12 verification.
> Production readiness is **not** claimed. No push was performed.

## 1. Baseline (verified, not assumed)

| Ref | Expected | Actual |
|---|---|---|
| `main` | `7ac7cb6` | `7ac7cb6` |
| integration HEAD (start) | `016e5a0` | `016e5a0` |
| alpha `feat/supportops-api004-alpha-security` | `e55b2a1` | `e55b2a1` |
| beta `feat/supportops-api004-beta-wp2` | `747ef6a` | `747ef6a` |
| `main` ancestor of integration | — | yes |
| `git diff main..integration -- src/supportops_agent` | empty | empty |

## 2. M2 fix (AuthenticationGate vs PostgreSQL pool)

- Default `max_concurrent_authentications` = **3** (was 8); still configurable via `create_app(...)`.
- Rule: with a shared pool, `limit < pool.max_size` (default 3 < 4). Pool size comes from `create_app(pool_max_size=…)` or from `PostgresCredentialRepository.pool_max_size` (← `DatabasePool.max_size`).
- Validated at startup; violation or `limit < 1` / non-int → `ServiceConfigurationError("Invalid authentication gate configuration.")` (fixed text, no values).
- Unknown pool size → `gate.pool_isolation_verified = False`, warning logged, **no isolation claimed**.
- Saturation → 503 `service_unavailable` + `Retry-After: 1` (unchanged); never 500; never unauthenticated access; `/healthz` bypasses the gate.
- No Redis / RabbitMQ / new rate limiter. The gate is a concurrency cap, **not** DoS protection.
- Files changed: `security/dependencies.py`, `app.py`, `repositories/credentials.py`, `db/pool.py` (read-only `max_size` property). `src/supportops_agent/**` untouched.

New tests (+24): `tests/service/security/test_auth_gate_pool_limit.py` (20), `tests/integration/test_m2_gate_pool_pg.py` (4, real PG with pool of exactly 4): 150 concurrent plausible unknown keys, concurrent ticket creation through the repository during the flood (40 tickets, no starvation), no gate bypass, no 500, `/healthz` 200, two event loops, no credential/pepper/DB text in responses.

## 3. Owner decisions recorded

- **D1** `StorageFailure` → 500 `internal_error`.
- **D2** `StorageUnavailable` → 503 `service_unavailable`.
- **D3** Pepper rotation: `SUPPORTOPS_PEPPER`, `SUPPORTOPS_PEPPER_ID`, `SUPPORTOPS_PREVIOUS_PEPPERS`; previous peppers verify-only; duplicate `pepper_id` forbidden.
- **D4** Review LOW **L1–L3 accepted for the MVP** and registered as technical debt below.
- **D5** No external rate limit in the MVP; not production-ready.

(Note: these D1–D5 are the INTEGRATION-GATE-01 task decisions, distinct from the D1–D5 open questions in `api-004-interface-alignment-and-integration-gate.md` §4.)

### Technical debt (API-004 integration)

| ID | Item | Status |
|---|---|---|
| API4-TD-L1..L3 | Review LOW findings L1–L3 | Accepted by owner for MVP. **The text of L1–L3 is not present in the repository**; details must be copied in from the review before finalization. |
| API4-TD-RL | No external rate limit; gate is per-process, concurrency-only | Accepted (D5) |
| API4-TD-TENANT | No tenant-disabled check (no `tenants` table) | Open, M02 |
| API4-TD-REHASH | No re-hash on pepper change | Open |

## 4. G1–G12 acceptance matrix (criteria taken verbatim from §6 of the alignment document)

| Gate | Original requirement | Evidence | Tests | Status | Residual risk |
|---|---|---|---|---|---|
| G1 | Record SHAs of both sides; worktrees clean | Section 1; integration worktree clean at start | — | PASS | Beta/Alpha branches are now behind integration |
| G2 | Interfaces/errors agreed, written confirmation | Owner decisions in this task (D1–D3) + `interfaces.py` unified (commit `f947a43`) | `test_security_static_boundaries` (single definitions) | PASS | Alpha-side written confirmation is the owner's task statement only |
| G3 | `merge-tree` dry run, no conflicts | `git merge-tree --write-tree main HEAD` rc=0 | — | PASS | merge-tree ignores untracked files in `main` (see section 8) |
| G4 | Integration branch from `main`, `--no-ff` merges, expected files only | Merge commits `16d9dee`, `25e5d17`; `main` is ancestor; 50 files changed, all service/docs/tests | — | PASS | — |
| G5 | `uv sync --frozen`, `uv lock --check` unchanged | `uv lock --check`: resolved 36 packages, no change; all runs used `--frozen` | — | PASS | — |
| G6 | `git diff main..HEAD -- src/supportops_agent` empty | Empty (also for working tree) | — | PASS | — |
| G7 | Full pytest, 0 failed, count ≥ sum of sides | **1072 passed, 0 failed, 0 skipped** (baseline 1048 + 24) | whole suite | PASS | — |
| G8 | PG16 isolated, `REQUIRE_PG=1`, 0 skipped | PostgreSQL 16.14, own container `supportops-gate01-pg`; `tests/db` 180, `tests/repositories` 23, `tests/security_pg` 18, `tests/integration` 19 incl. new 4 | marked `postgres` | PASS | Test DB only; no role/least-privilege setup (WP4) |
| G9 | Security regression: route_audit, leakage markers, static boundaries | `tests/service/security` 174 passed | `test_security_route_audit` (11), `test_security_leakage` (8), `test_security_static_boundaries` (9), I8 | PASS | — |
| G10 | Import without side effects; `create_app()`; uvicorn + `/healthz`, `/readyz`; `/v1` without key → 401 + `WWW-Authenticate` | Import creates 0 threads; real `uvicorn --factory`: `/healthz` 200, `/readyz` 200, 0 5xx; 401 + `WWW-Authenticate: Bearer` asserted at ASGI level in tests (the bare factory app mounts no `/v1`, so it cannot be curled) | `test_security_authentication`, `test_security_wiring`, integration I-tests | PASS | `/v1` 401 not exercised over a real socket |
| G11 | No `state.json`, `.env`, `__pycache__` in index; `git diff --check` | `git ls-files` clean; `git diff --check` rc=0 | — | PASS | — |
| G12 | Public API change after G4 → stop, owner approval | M2 added an optional `pool_max_size` keyword and changed the default 8→3, under explicit owner decision for this task; no other public change | — | PASS (owner-authorized) | Default change is behavioural |

## 5. Full regression (integration worktree, before commit of docs)

`tests/db` 180 · `tests/repositories` 23 · `tests/security_pg` 18 · `tests/service/security` 174 (+3 pre-existing deprecation warnings) · `tests/integration` 15 (+4 new run separately) · **full `pytest tests`: 1072 passed, 0 failed, 0 skipped**, PostgreSQL 16.14, `SUPPORTOPS_REQUIRE_PG=1`.

## 6. Security review (credentials / pepper / DSN)

- No logger call formats dsn/pepper/secret/hash/authorization.
- `repr`/`str` of `ServiceSettings` and `DatabaseSettings` contain no pepper, previous peppers or DSN password (checked).
- Overload and 401 responses contain no key, prefix, secret or pepper (new tests + existing I8).
- Configuration errors use fixed text.

## 7. Remaining risks

Concurrency cap only (per-process, no rate limit, no DoS claim); gate does not protect the pool from non-authentication load; no tenant-disabled check; L1–L3 accepted, text to be supplied; `/v1` 401 over a real socket not tested.

## 8. Merge into `main` — NOT performed (blocked)

Per the task, merge stops when `main` has foreign uncommitted changes or when merge would overwrite an untracked file:

1. `main` worktree has an **uncommitted foreign edit** in `src/supportops_agent/classification.py`: stray text `что такое` appended to a docstring line (`"""…ограничение."""что такое`). The file currently has a **SyntaxError**, so any regression in `main` (including SEC-003) would fail.
2. Untracked `docs/security/api-004-security-contract.md` (365 lines) exists in `main` and the merge would overwrite it. Content compared with the integration version (revision 3): it is an older draft; differing lines are outdated wording (status "draft/nothing implemented", pre-integration error table, open questions now resolved). Not overwritten.

Owner decision needed: (a) how to handle the `classification.py` edit (discard/commit/stash — not mine to touch), (b) permission to move the untracked draft aside (e.g. rename to `.orig` outside git) so the merge can proceed.

## 9. MONOLITH

Not updated, per the task. Installed `@latysh-development/monolith` in the main worktree: `0.1.0-alpha.2` (package.json range `^0.1.0-alpha.2`); no `node_modules` in the integration worktree. Discrepancy: `AGENTS.md` describes the Coordinatorium v0.1 mission lifecycle, while `.monolith/memory/working/active.md` still lists SEC-003 as the active story; the Coordinatorium problem is excluded from the critical path and will be synchronised separately. `.monolith/state.json` untouched; no mission commands run.
