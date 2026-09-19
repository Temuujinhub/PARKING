# Inner-camera recovery follow-up

Base production release: `c699f280b258971e57f83389fbc8b86ec94323db`.
This follow-up is a separate candidate; publishing the branch does not deploy it.

An outer entry followed by an inner entry within 90 seconds was incorrectly
treated as a duplicate during camera-log recovery. Recovery now carries the
camera's database ID and checks the same device, direction, plate and time
window. It preserves live rejections and verifies the current inner-camera
mapping and the site's registration policy. Manual snapshots are excluded.

Automatic repair is limited to an unquoted OPEN stay covering the event time.
It never modifies an already quoted/paid/closed stay or attaches a past read to
a later visit. A later accepted inner transition makes the earlier timeline
ambiguous; that case requires review. Recovery and live inner processing lock
the same session row. The recovered event, audit record and counter update
commit together, making repeat and concurrent recovery safe. Existing open
stays are recovered before outer-log closure computes their bill.

The inner gate helper reports an acknowledged open only after SUCCESS, or a
recent SUCCESS for that same non-null session. Pending commands and another
vehicle's cooldown remain suppressed without reporting a false open. An ACK
still does not prove physical movement; no new gate command path is added.

The camera-sync preview also previously closed existing stays in its outer-exit
loop. The preview now leaves sessions, debt and audit records unchanged.

Validation includes synthetic four-camera tests, a 10-hour/600-minute credit,
duplicate and rejected events, remapped devices, newer visits and inner state,
registration scope, financial-state boundaries, preview immutability and gate
outcomes. A disposable PostgreSQL test runs concurrent recovery and the live
handler, including their joined-model row locks. No live provider/device IO is
part of these tests.

## Promotion and remaining work

Use an exact commit with passing Linux and PostgreSQL 16/18.6 CI. Keep the old
autodeploy timer disabled. Rehearse on a restored, network-isolated copy first;
then take a fresh backup and use the guarded release runner. No schema or
dependency changes are required. Preserve site policies and financial history.

Do not reset recovery watermarks to replay historical financial records. This
patch does not reconstruct closed stays, repair every missed entry/exit pair,
change employee contracts or the configured pause cap, improve OCR optics, or
resolve historic camera-clock drift. A wholly missing outer stay reconstructed
from camera logs still needs a complete inner timeline review before any
historical correction. The ANPR warning's cross-zone peer heuristic and its
overconfident wording remain separate work.
