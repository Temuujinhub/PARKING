# History: exit evidence, closure and debt

History displays three independent facts: recorded exit recognition, the stay's
closure reason, and debts created for that stay. `MANUAL_CLOSED` is labelled
`Хаасан`; it does not imply a missing exit read or an unpaid balance.

An exit device plus recognition confidence is displayed as a camera read. A
device link alone is uncertain because manual special exits can assign a camera.
An exit time/confirmation alone is shown as a recorded exit, not camera evidence.
None of these fields proves physical passage through a gate.

For paid debts, the history response links to the paid Payment and its collection
stay, subject to the requesting user's site scope. A PENDING invoice association
does not count as payment. A standalone payment or a payment outside the user's
scope does not expose an inaccessible stay link. Exact-session links and Excel
exports retain the normal authorization scope.

The payment breakdown uses the stored invoice snapshot only when its nonnegative
decimal components reconcile exactly to Payment.amount. Missing/inconsistent
legacy snapshots are explicitly unknown; history does not recalculate old fees.
Example: the old stay's 25,000 MNT debt links to a later 30,000 MNT QPay payment,
whose allocation is 5,000 MNT current parking plus 25,000 MNT prior debt.

New AUTO_CLOSE logs record `trigger`, `threshold_hours`, `wait_started_at` and
`closed_at` (UTC). The awaiting-payment branch uses its actual site timeout,
commonly 2 hours, while the stale-session branch uses its own threshold.
Entry-only free cleanup records `entry_only_timeout` with its own threshold,
commonly 72 hours. Legacy `hours` in AUTO_CLOSE logs is not reinterpreted or
rewritten because older versions could record the unrelated stale threshold.

No tariff, debt collection/expiry rule, bank settlement, barrier command,
historical financial row or schema changes are included.
Existing debts are not cancelled by this display change.

Release validation also requires PyJWT 2.14.0: CI found ten advisories in the
existing 2.13.0 pin. This dependency-only security update is a separate commit;
see the upstream [release](https://github.com/jpadilla/pyjwt/releases/tag/2.14.0).
Build and test a separate runtime before switching the service; retain the old
runtime for rollback rather than upgrading the running environment in place.

## Release checks

Run backend unit tests, PostgreSQL integration tests and frontend tests/build.
Verify the candidate against a fresh isolated production backup before promotion.
Deploy backend and frontend together. Keep automatic deployment disabled.
Retain the previous frontend assets so open browser tabs remain usable.
Rollback code and the frontend index together without restoring the live database.

Acceptance: old paid debt remains on its original closed stay; the link opens
the authorized collection stay with the payment detail expanded; the components
sum to the bank payment; pending/cancelled debts remain distinct. On the next
natural auto-close, confirm the recorded trigger/threshold/timestamps. Do not
force a close or send a gate/payment command for acceptance testing.
