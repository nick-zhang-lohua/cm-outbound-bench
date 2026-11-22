# D04 — Subscription & Account Lifecycle Policy (Synthetic)

## Tools

- `get_subscription_status` — plan, status, commitment end date; requires
  verification.
- `check_plan_change_eligibility` — eligible + blocking reasons; requires
  verification.
- `submit_plan_change` — requires verification, eligibility and runner-recorded
  PLAN_CHANGE consent for the target plan.
- `submit_cancellation` — requires verification and runner-recorded
  CANCELLATION consent for the target object.
- `list_plans` / `get_plan_details` — read-only plan catalogue.

## Rules

1. Never change a plan before checking eligibility for the specific target
   plan.
2. If the line is suspended, the subscription is not active, the line is inside
   a commitment, or the target plan is already in use, explain the blocking
   reason and do not submit the change.
3. A plan change requires explicit consent naming the target plan.
4. A cancellation requires explicit consent naming the object to cancel.
   Cancelling an offer removes the enrollment; cancelling a plan does not
   terminate the account.
5. Do not offer an account closure, number transfer or contract break that no
   tool supports. State that it must be handled by a different process.
6. State the effective date returned by the tool. Do not invent a date.
7. Never submit the same change twice.
