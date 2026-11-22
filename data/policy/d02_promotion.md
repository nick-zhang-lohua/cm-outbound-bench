# D02 — Promotion & Offer Policy (Synthetic)

## Tools

- `get_offer_details` — name, fee, currency, validity, renewal and restrictions.
- `check_offer_eligibility` — requires verification; returns eligible + reasons.
- `enroll_offer` — requires verification, eligibility and runner-recorded consent.
- `send_confirmation_sms` — only after a successful enrollment.
- `register_do_not_call` — records an opt-out; idempotent.

## Required disclosure before consent

State all of the following from tool results:

1. the offer name and its additional monthly fee;
2. the currency and the validity period in months;
3. whether the offer renews automatically after the validity period;
4. any eligibility or usage restriction.

## Required sequence

For an enrollment attempt, follow this order:

1. Verify the customer's identity.
2. Retrieve the offer details.
3. Disclose every required offer term listed above.
4. Execute `check_offer_eligibility` directly. The check is read-only, so do not
   ask the customer for permission to run it and do not describe it as
   enrollment or as an irreversible operation.
5. Explain the returned eligibility result, including every returned reason.
6. Ask for explicit enrollment consent only when the result is eligible.
7. Enroll only after the runner has recorded that consent.
8. Send the requested confirmation SMS only after a successful enrollment.
9. Report the actual result.

A permission response for checking eligibility is never enrollment consent.

## Rules

1. Never enroll a line without a prior successful eligibility check for that
   offer.
2. Never enroll without an explicit statement of consent from the customer.
3. If the offer is inactive or the line is not eligible, explain why using the
   returned reasons and do not attempt an enrollment.
4. An already-enrolled line must not be enrolled again. Explain the existing
   enrollment instead.
5. Send the simulated confirmation SMS once, after the enrollment succeeds.
6. If the customer declines, acknowledge the decision in one short closing
   message, respect it and close the call; do not repeat the pitch and do not
   ask for enrollment again.
7. If the customer asks for no further contact, register do-not-call and stop.
