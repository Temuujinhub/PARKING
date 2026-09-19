# Deep-flow audit fixes and rollout contract (2026-09-19)

This extends the payment-wait / tenant-isolation release. Payment quote defaults
remain 3 minutes, then the whole elapsed stay is priced. Automatic debt defaults
remain 2 hours from the first exit wait. Existing database overrides are retained:
production's former `awaiting_hours=12` requires an explicit audited update to 2.

## Implemented stages

1. Validate finite positive two-decimal money and nonempty bank references; enforce
   partner site + key ownership; unique provider/account/reference; fix push payload
   dispatch and reject queued events for disabled or remapped cameras.
2. One session reservation for pending/creating/unknown payment attempts. Never
   discard an old wallet intent when its quote expires. Record late wallet/POS
   confirmations as the actual collected amount, and return only remaining dues.
   Overpayments are retained and flagged for reconciliation, never silently refunded.
   Persist QPay creation before sending; uncertain creation blocks another collection.
   Retry safe reads/checks, not ambiguous invoice/receipt writes. Busy callbacks return
   retryable failure. Internal wallet partial payments use the shared receipt path.
3. Decode and validate JPEG content (20 MB / 16 MP bounds). Untagged IP-only frames
   are ignored. Comet/WS share one fixed-duration selector (decoded resolution then
   edge-detail variance). Atomic attachment prevents overwrite by stale writers;
   identified event images may replace CGI context images. Camera/site/direction and
   actual exit-read time are checked. Gate command reservations persist before IO;
   unknown results cannot be automatically replayed. A busy RPC lock never permits
   parallel control. Cancellation records UNKNOWN instead of detaching a DB-bound task.

## Client contract / operations

* Wallet: create intent, collect the returned amount using a stable provider reference,
  then confirm that same intent. `status=PAID` means this payment was received; inspect
  `session_status`, `amount_due`, `needs_additional_payment` and `overpaid_amount`.
  A reprice requires resolving the earlier intent. `POST /api/v1/payments/{id}/cancel`
  accepts only `{"outcome":"NOT_CHARGED"}` after the wallet proves no debit occurred.
  Timeouts must never be treated as no-charge proof. Late confirmations are still recorded.
* POS: `POST /api/payments/pos/prepare` with session_id, registered terminal_id and
  expected_amount BEFORE bank collection. Pass returned payment_id to /pos/confirm
  along with the bank amount/reference. Confirm retries must reuse the same reference.
  `POST /api/payments/pos/{id}/cancel` requires terminal_id + outcome NOT_CHARGED.
  The separately maintained native app must adopt this API; old confirm-only clients
  remain compatible but cannot reserve checkout before their external bank charge.
* UNKNOWN QPay creation: locate the original sender_invoice_no in merchant/provider
  records, reconcile that operation; never create a replacement merely on timeout.
  No undocumented QPay lookup API or automatic refund was invented in this change.
* Gate PENDING after a crash or UNKNOWN: inspect the physical device first. An explicit
  authorized operator command is the recovery action. No automatic replay of an
  uncertain pulse; acknowledged RPC success is not proof of physical gate position.
* Live snapshot.cgi is a contextual live frame, not proof of plate identity. The stored
  source field distinguishes it. Prefer identified comet/WS events; disable CGI fallback
  per deployment only after measuring event coverage. Existing 2.5s selection / 4s event
  wait are preserved until real camera latency/quality measurements justify adjustment.

## Rollout gates and remaining limitations

Run unit + PostgreSQL 16/18.6 concurrency/migration tests, security and frontend CI on
this exact tree. Restore the production backup to an isolated database, migrate it
without starting background workers, compare row/financial totals, and inspect overdue
waits before activating 2 hours. Keep the old 2-minute auto-pull timer disabled. Deploy
an exact approved commit and validate /api/health, schema, services and queue totals.

This is not a claim of a distributed exactly-once transaction across banks, receipts,
webhooks and physical gates. Receipt HTTP and partner outbound notifications still
need a durable outbox/reconciliation worker as a later architecture stage. Bank-side
sandbox/terminal integration and real 4-camera acceptance require server/device access.
No financial records are deleted or bulk rewritten by these fixes.

