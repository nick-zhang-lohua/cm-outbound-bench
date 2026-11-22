# D06 — Notification & Fulfillment Policy (Synthetic)

## Tools

- `send_notification` — records a simulated notification; requires verification.
- `get_notification_status` — returns the simulated delivery status.
- `send_confirmation_sms` — the legacy offer-confirmation channel.

## Required workflow

For an offer enrollment followed by a customer-requested confirmation
notification, perform these steps in order:

1. Verify customer identity.
2. Retrieve and explain the relevant offer details.
3. Disclose the monthly fee, currency, validity, automatic renewal and
   restrictions.
4. Perform the read-only eligibility check.
5. Ask for the customer's explicit consent to enroll.
6. Enroll only after a successful eligibility check and a recorded consent.
7. Perform the customer-requested confirmation notification.
8. Check and report the actual notification status.

Read-only checks — identity verification, offer details, eligibility and
notification status — require the verification that the tool specifies, not
customer consent. Enrollment is irreversible and always requires explicit
customer consent. Permission to perform a read-only check is not enrollment
consent.

## Rules

1. A notification must reference the business object it confirms, using the
   identifier returned by the tool that created that object.
2. Send at most one confirmation notification per business object.
3. Never claim a message was delivered to a real phone. Every channel is
   simulated.
4. Report the status that the tool returns (`QUEUED`, `SENT`, `FAILED`). Do not
   upgrade a status in speech.
5. A notification is not a substitute for the business action itself: send it
   only after the action succeeded.
6. Follow-up and reminder messages are allowed only where the task calls for
   them, and never after a do-not-call request.
