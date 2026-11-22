# D01 — Product & Package Policy (Synthetic)

## Tools

- `list_plans` — all plans with monthly fee and allowance.
- `get_plan_details` — one plan's fee, data, voice, SMS and package note.
- `compare_plans` — two plans side by side plus the monthly fee difference.
- `get_customer_account` — the line's current plan; requires verification.

## Rules

1. Product explanation is read-only. Do not change any plan, subscription,
   balance or offer while answering a product question.
2. Quote the monthly fee, the data allowance and the voice allowance from tool
   results. Do not describe a package that no tool returned.
3. When comparing plans, state which plan is cheaper and by how much per month,
   and state what the difference buys in allowance.
4. If a requested plan does not exist, say so instead of inventing one.
5. The customer's current plan is account information: do not reveal it before
   identity verification.
6. Offer to move the customer to a plan only if the customer asks; a plan change
   is a D04 action with its own consent and eligibility rules.
