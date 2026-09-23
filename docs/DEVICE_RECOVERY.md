# Device recovery evidence

## Snapshot health

The health classifier previously treated any fast failed snapshot as the observed
HTTP 400 failure pattern. A fast 401/403, unsupported URL, server error, invalid
JPEG or transport exception could therefore qualify a camera for automatic reboot.

Snapshot probes now keep HTTP status counts and transport-error counts. The
existing `hung` heuristic requires every failed probe to be an HTTP 400 received
in less than 0.2 seconds, plus a recent non-future heartbeat or an actual event
stream HTTP 200. Mixed/slow failures require diagnosis. JPEG success still wins.
This narrows the existing heuristic; HTTP 400 is not a universal hardware-fault
diagnosis, and the configured reboot/maintenance/cooldown policy still applies.

An event-stream read timeout before response headers does not prove HTTP 200.
[HTTPX exceptions](https://www.python-httpx.org/exceptions/) distinguish read and
connect timeouts; the [streaming API](https://www.python-httpx.org/async/#streaming-responses)
exposes the response status inside the context manager. No response body, camera
credential or vehicle image is retained by these probe diagnostics.

The change does not enable camera health or automatic reboot on any site.
Test coverage includes fast wrong statuses, transport failures, mixed/slow 400s,
future heartbeats, missing response headers, JPEG recovery and the automatic
runner not calling reboot after a credentials failure.

## One interrupted gate reservation

Gate commands are committed as PENDING before device I/O. A host reboot can leave
a reservation that blocks later automatic commands for that gate. Its physical
outcome cannot be reconstructed from age alone, and replaying the command can
open the barrier for the wrong car.

`app.services.gate_recovery.recover_preboot_command` is an explicit operator
recovery helper, not a startup/scheduled job or an unauthenticated API. The caller
must first verify the selected physical gate has one application host and that
its executor cannot have survived the independently verified host boot. This
helper must not be used with an unverified multi-host or remote command executor.

For a selected automatic entry command it:

1. Locks the device then command with NOWAIT, following the gate executor's order.
2. Rechecks active device, original PENDING state, preboot timestamp, associated
   stay and absence of recorded outcome evidence.
3. Calls the operator's private durable evidence writer before changing the row.
4. Changes only its status to UNKNOWN and adds a `BARRIER_PREBOOT_RECOVERY` audit.
   The caller commits both in one short transaction; errors must roll it back.

It sends no device command and changes no payment, debt, parking time or tariff.
An UNKNOWN command keeps the original stay's automatic no-replay guard. A
different stay can proceed through the usual entry authorization. A non-PENDING
command is left unchanged. Physical confirmation and later natural traffic remain
separate verification steps.

The helper's embedded form was exercised in a production-host, network-isolated
restored PostgreSQL database, including transaction rollback, before one audited
preboot reservation was classified. Later natural automatic entry commands
received successful device responses. This does not establish physical gate
position or authorize bulk cleanup of other unresolved commands.

The permanent crash-recovery design still needs durable executor ownership or
leases before automatic reclamation can be safe across multiple application
hosts. This change deliberately provides no age-only sweep or blind retry.
