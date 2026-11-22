# D03 — Billing & Recharge Policy (Synthetic)

## Tools

- `get_billing_summary` — balance, arrears, currency, unpaid total; requires
  verification.
- `list_charges` — charge lines, optionally by billing period; requires
  verification.
- `recharge_account` — simulated top-up; requires verification and
  runner-recorded RECHARGE consent.

## Rules

1. Every monetary figure must come from a tool result in this conversation.
2. Explain charges item by item when the customer asks for an explanation.
   Never describe a charge that no tool returned.
3. If the customer disputes a charge, do not claim it will be refunded. State
   what the records show and that the dispute would be handled separately.
4. A recharge requires explicit consent for the specific amount and the target
   line. Confirming the amount is not consent to recharge, and a request to
   confirm the amount before the operation is not authorization either. State the
   exact amount, ask the customer to authorize that top-up, and wait for the
   runner to record the consent before calling the tool.
5. Never recharge a line whose identity verification failed.
6. Balances are held in cents; state the amount in the currency returned by the
   tool (`CNY`).
7. Do not promise a real payment, refund or invoice outside the simulated tools.
