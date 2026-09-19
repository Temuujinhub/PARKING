# Payment wait and staged audit fixes

This release is a draft for staging acceptance. It has not been installed on a
server. An existing server running the old timer can still pull `main` every two
minutes: merging this PR before stopping that timer can bypass this rollout plan.

## Payment behavior

1. The first accepted exit read starts `payment_wait_started_at` and saves a price
   quote. Repeated camera reads and metadata edits do not extend this deadline.
2. The quote is held for 3 minutes by default. Configure 0–60 minutes in
   **Тохиргоо → Төлбөрийн дүрэм → Гарах хаалт → Төлбөр төлөх үеийн үнийг барих хугацаа**.
   Global defaults and authorized site overrides are supported. A setting change
   applies to new quotes; an already issued quote keeps its original deadline.
3. At the deadline, normal pricing uses the **total elapsed parking time**. The
   hold is not a deduction from parking time. For an hourly 1,000₮ tariff with no
   free minutes, a read at minute 59 keeps 1,000₮ through minute 61:59; at minute
   62 normal pricing is 2,000₮. Existing contractual discounts still apply.
4. Repeated QR requests during the hold reuse the invoice. Replacing an invoice
   requires successful provider cancellation first; an uncertain cancellation
   returns 409 and does not issue a second payable QR. Confirmed payment uses its
   saved fee allocation, so processing latency does not create a second charge.
5. Current cash/transfer UI submits the displayed `expected_amount`. A changed or
   invalid amount returns 409 and refreshes the queue for operator confirmation.
   Legacy POS clients that omit this field retain their existing API contract.
6. The existing auto-close controls remain in **Тохиргоо → Төлбөрийн дүрэм**:
   `auto_close.enabled=true`, `awaiting_hours=2`, and
   `create_debt_unpaid_exit=true` are needed for the requested behavior. Existing
   stored overrides are not silently replaced. The worker checks every 60 seconds
   and handles up to 200 waiting rows per site per pass; backlog may take longer.
7. After two hours from the first wait, unpaid sessions leave the payment queue
   and the unpaid remainder becomes debt once. Debt valuation freezes at the
   last recorded exit observation, avoiding an invented two-hour charge after
   the vehicle may have physically left. A later verified payment settles that
   debt and does not reopen a barrier for the closed session.

## Financial and access safeguards

- Local wallet debit and payment finalization share a transaction; failures roll
  back the local debit. Wallet locks refresh the persisted balance.
- An external wallet debit with an uncertain result remains `UNKNOWN`. No other
  provider or QR may charge that session until an authorized finance/admin user
  records a verified result through the reconciliation endpoint. A provider
  reference is required and an audit row is written. Automatic provider-specific
  reconciliation and a dedicated UI are follow-up work.
- Wallet top-ups verify the paid amount. Underpayment enters `REVIEW` without a
  wallet credit. Concurrent confirmations credit once.
- Existing EV wallets require their private wallet token; plate/phone alone do
  not disclose the token, change the stored phone, or authorize a debit. Users
  without the private wallet link need operator-assisted identity verification.
  This release does not implement SMS ownership verification/recovery.
- Wallet objects are scoped to the owning tenant (shared across that tenant's
  sites). EV devices/plans/sessions and company invoices enforce site/tenant
  scope. WebSockets require an initial token message and revalidate access.

Receipt and barrier side effects still run inline. A durable post-commit job/
outbox for crash recovery is not implemented by this patch; do not describe the
entire external payment/hardware workflow as exactly-once.

## Database upgrade

`backend/app/migrations.py` is the current startup migration entry point.
`Base.metadata.create_all()` and append-only SQL statements run in one PostgreSQL
transaction under an advisory lock. Statement hashes are recorded in
`parking_schema_migrations`; `parking_schema_state` records the release revision.
Missing model columns or a failed migration abort startup. `/api/health` returns
503 if the database or required schema is not ready. Historical Alembic files are
retained pending an inventory of operational use; do not run both migration paths.

The first versioned run adopts historical statements once. Those statements
include existing QPay account/data normalization, so a backup and staging upgrade
against a copy of the actual schema are required. Duplicate active sessions or
invalid existing constraints can now fail startup instead of being ignored.

For old waiting rows, first-wait and last-exit timestamps are backfilled once from
`COALESCE(exit_time, updated_at)`. This is the best stored timestamp, not proof of
the actual first exit. Review old rows and expected debt amounts before enabling
the cleanup worker on production.

Existing company contacts/invoices receive owner `LEGACY`. Financial records are
retained; the migration never guesses a tenant. Same-company/period regeneration
is blocked while a legacy invoice is unresolved. Review each legacy invoice's
driver/site detail, allocate ownership explicitly, preserve its payment history,
and reconcile mixed-tenant invoices before promotion. Scoped users cannot safely
use those legacy rows until that review is complete.

## Ordered rollout

1. Obtain the read-only server audit: current commit, service/timer/cron units,
   paths, DB version/schema, pending payments, old wait rows, tenant ownership,
   camera firmware and site/lane/direction mapping. SSH audit output is still
   outstanding; actual server topology is not verified.
2. Stop the existing auto-update timer on both test and production using the unit
   names confirmed by that inventory. Take a database backup and verify restore
   in an isolated environment. Do not reset or truncate real transaction data.
3. Install this exact reviewed commit on staging, with an explicitly configured
   `PARKING_DEPLOY_ROLE=staging`. New systemd auto-update service configuration
   reads `/etc/parking/deploy.env`. Manual invocations also need their environment
   explicitly set. Keep this file owned and writable only by the administrator.
4. Run the migration on a staging copy; verify readiness, legacy ownership,
   settings save/reload with real administrator accounts, 3-minute boundary,
   invoice cancellation, delayed callback, 2-hour debt and wallet reconciliation.
5. Replay then physically test 2 entry + 2 exit cameras: concurrent similar
   plates, duplicate/late event, reconnect, image arriving before/after event,
   and paid vehicle retry. Require zero wrong session/barrier/image matches and
   zero duplicate debits. Measure image success rate and p50/p95/p99 latency
   before changing wait budgets. Mock regression tests do not replace this.
6. Require all CI checks and reviewer acceptance. Configure actual GitHub branch
   protection separately; a workflow file does not establish required checks.
7. Production defaults to `PARKING_DEPLOY_ROLE=production`. The new updater needs
   `/etc/parking/approved-release` containing the exact 40-character lowercase
   commit SHA that passed staging. A different requested target is rejected;
   the timer verifies the approved commit belongs to `origin/main`. Updating
   `main` alone is insufficient after the new updater is installed. The file is
   an operator's approval, not an automatic CI/physical-test verification.
8. Production promotion remains blocked until an atomic release switch and
   tested rollback plan are implemented for the actual server layout. The current
   `deploy/update.sh` still updates in place; the approval gate does not make
   build/copy/restart atomic. Record the previous release and schema compatibility,
   verify health and business checks after promotion, and monitor reconciliation.

Do not delete camera diagnostic/cleanup tools until cron/systemd/operator usage
is inventoried. `tools/reset_test_data.sh` now refuses production names and lacks
an implicit default, but its legacy truncate set is not an EV-aware reset workflow;
it was not executed and should not be used for financial reconciliation.

## Verification boundaries

Unit tests cover price boundaries, immutable waiting timestamps, site settings,
queue expiry/debt, partial and late payment, QR reuse/cancellation uncertainty,
wallet rollback/provider uncertainty, access scopes, and release approval gates.
Disposable PostgreSQL tests cover concurrent settings writes, migration replay,
legacy upgrade, duplicate wallet credit, fresh wallet balances and late debt
settlement. No test targets the application/production DSN.

Browser checks use synthetic API responses to verify site switch clearing,
display of the held price, and site-scoped 3→5 minute save/reload isolation. They
verify frontend wiring, not production persistence. PostgreSQL settings tests
separately verify storage concurrency. Real-bank and physical-camera acceptance
remains part of staging.
