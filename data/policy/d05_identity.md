# D05 — Identity & Ownership Policy (Synthetic)

## Tools

- `verify_customer_identity` — verifies the line owner with a synthetic token.
- `get_identity_status` — real-name registration state; requires verification.
- `check_ownership_transfer_conditions` — blocking conditions; requires
  verification.

## Rules

1. Identity verification is scoped to one line and one conversation.
2. Never infer who is speaking from a channel number or from how the voice
   sounds. Only the token test decides ownership.
3. A respondent who is not the owner must not receive any account information,
   and must not be treated as acting for the owner.
4. Real-name status, registration type and any blocking condition must come from
   a tool result.
5. Ownership transfer is never executed by a tool in this benchmark. The agent
   may only report whether the conditions are currently met and that the
   transfer requires a separate process.
6. Never ask for, repeat or confirm a real identity document number, bank card
   number, password or verification code.
