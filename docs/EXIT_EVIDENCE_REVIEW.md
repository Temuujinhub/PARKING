# Exit evidence review

The cashier's **Гарах уншилт ба төлсөн хүлээлт** panel separates rejected exit reads, paid stays waiting to leave, and failed/uncertain commands. It marks invalid bound camera/gate mappings for attention. Missing `exit_device_id` means the stay has not been linked to an exit camera; it does not prove that the camera is offline. Advance QR payment can legitimately precede arrival at an exit. In the audited 4927УНН incident, the exact exit text was rejected at confidence 14 (threshold 30), leaving the paid stay unbound and sending no payment-triggered gate command.

## Operator workflow

1. Select the parking site and a rejected read or paid waiting stay. The rejected list is limited to the newest 50 records in 24 hours (API maximum 100); the UI displays `+` when capped. Duplicate rejections are included. Camera/lane labels reflect current device configuration; historical lane numbers are not stored on old events.
2. Inspect raw/stored plate, event time, confidence and rejection reason. An original picture is displayed only when the event payload contains it. A missing historical picture is explicitly missing; an unidentified stream image or today's live image is never presented as that original event.
3. Choose the exact stay, or manually search for the correct plate. Exact text candidates are suggestions, never a gate decision. Select the actual exit camera and request a current frame. Compare it with the entry photograph, confirm the same vehicle and record a reason.
4. The server checks site/module access, operator ownership of proof, a 90-second freshness limit, unchanged camera mapping/plate/entry image, an active stay/site, a unique matching gate, conflicting later lane reads, uncertain prior commands, current unpaid amount (honouring paid grace), paid/free entitlement, minimum stay and the configured debt-count threshold. Entry and review images must exist. A snapshot is fetched before taking the stay lock. Concurrent financial operations use the existing NOWAIT stay lock.
5. The proof is consumed with the closure audit before gate dispatch. Retrying the same proof cannot issue another command. A failed/unknown command is not physical exit confirmation; follow the existing gate incident procedure rather than resubmitting blindly. Closed stays are not reopened by this endpoint.

The UI reports payment, record closure, command result, device ACK and physical exit separately. No independent passage sensor is integrated here, so physical exit is shown as unverified. Original rejected events and payment records remain unchanged. The existing unbound `paid_no_open` shortcut now directs the operator to this review flow instead of choosing the first exit camera.

## Automatic exit changes

External exit processing rejects invalid plate formats and fuzzy active-stay matches into review, except (2026-10-12) a measured OCR confusion (`plates_ocr_confusable`) with exactly one nearby active stay, which is auto-matched and audited as `EXIT_OCR_MATCH`. A payment opens the read lane while that read is within `exit_read_valid_minutes` (default 15) and no other plate was read on that camera since; a POS card payment without such a read opens the lane named in the POS request or the site's only outer exit lane (`POS_LANE_OPEN`). See the Mongolian rollout guide, section «2026-10-12 засвар». It no longer uses `paid_wait_fallback` to borrow a recently paid vehicle's entitlement. Fuzzy rereads go to review, and automatic reopening uses exact plate text. A valid-looking unmatched plate without an exact registration goes to review even where the no-entry fee is zero; it does not create a new automatic invoice. This intentionally increases operator review for ambiguous/no-entry events. Exact matching accepted reads retain the normal paid/free path. Entry and nested-lane matching policies are outside this change.

Low-confidence, unknown-confidence and conflicting plate fields are rejected by external exit ingestion. Plate and vehicle confidence are distinct. Metadata and event-specific crop variants support operator review. There is no automatic promotion based only on confidence, a single second-OCR result, a zero amount or a QR payment. Repeated-frame automatic recovery is not enabled; the operator supplies the independent current-lane check.

## Optional second OCR trial

`POST /api/exit-review/events/{id}/ocr` runs a local Tesseract process on the original payload's plate crop (preferred) or frame. It never captures a replacement live frame and never selects a stay, changes its plate, assigns payment or opens a gate. Output is an audited suggestion for the operator. No image is uploaded to a third party.

Default: disabled. A staging administrator must install Tesseract with `mon` and `eng` trained data and set `PARKING_EXIT_REVIEW_OCR_ENABLED=true` to trial it. An absent engine/model or missing source picture is an explicit error, not a success. Invocation uses an argument list, no shell, a 12-second total budget (8 seconds per subprocess), one trial worker per application process and a bounded input image. See the [official Tesseract CLI documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html). Actual Mongolian plate accuracy has **not** been validated; compare a labelled set of original day/night frames before enabling any broader use. Tesseract's reported confidence is not calibrated to the camera's confidence.

## Validation and release

No schema migration. No production configuration, payments, registrations or gates were changed during development.

- Synthetic API tests cover scope/permission checks, exact matching, stale/consumed/wrong-operator proof, changed lane/site/plate/images, outstanding money/debt, uncertain prior commands and no automatic gate decisions from OCR.
- Windows-compatible unit suite: 804 passed, 15 skipped; three POSIX-only timezone tests were excluded. Linux CI and PostgreSQL concurrency checks remain release prerequisites.
- Frontend: 26 tests and production build passed. Synthetic headless browser checks covered selection, metadata, images, OCR suggestions, disabled controls, one-command submission, independent ACK/passage wording, focus containment after submitting, and 375/390px mobile widths.
- A production rehearsal must verify the projected JSON text query against PostgreSQL, queue latency and exact-camera snapshot retrieval without dispatch. Gate acceptance should be an attended test with the operator physically verifying the vehicle and lane. Do not batch-resolve historical paid/unbound stays.

Rollback is the existing pinned-release procedure. Because ambiguous automatic exit behavior is deliberately stricter, operator staffing and the manual no-entry workflow must be included in rollout acceptance.

See [the Mongolian implementation and POS rollout guide](EXIT_RELIABILITY_POS_ROLLOUT_MN.md) for transaction-level lane inference, legacy POS compatibility and the rule matrix.
