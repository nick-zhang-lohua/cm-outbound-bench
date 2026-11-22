# Runtime Database, Initial States and Invariants

The runtime database is **synthetic operational state**, entirely separate from
the corpus database. It contains no real customer data and nothing derived from
transcript text.

One Pydantic model (`CmOutboundDB`) serves every domain. It is copied per
episode, validated on construction, and hashed so a run can be reproduced.

---

## 1. Entities

### Legacy marketing entities (unchanged)

| Entity | Purpose |
|---|---|
| `Customer` | Synthetic owner with a `TEST_IDENTITY_*` token |
| `PhoneNumber` | Synthetic line, owner, current plan, status |
| `Plan` | Plan id, display name, monthly fee |
| `Offer` | Offer terms: fee, validity, renewal, restrictions, eligible plans |
| `ConsentRecord` | Runner-only offer consent |
| `Enrollment` | Offer enrollment bound to one consent |
| `SmsRecord` | Simulated confirmation SMS |
| `DoNotCallRecord` | Opt-out preference |

### Multi-domain entities

| Entity | Domain | Purpose |
|---|---|---|
| `PlanAllowance` | D01 | Data, voice and SMS allowance attached to a plan |
| `BillingAccount` | D03 | Balance and arrears in cents for one line |
| `Charge` | D03 | One charge line with billing period and status |
| `Recharge` | D03 | Simulated top-up |
| `Subscription` | D04 | Lifecycle state, plan, commitment end date |
| `PlanChangeRequest` | D04 | Requested upgrade/downgrade and effective date |
| `CancellationRequest` | D04 | Requested cancellation of a plan, offer or service |
| `IdentityRecord` | D05 | Real-name registration state |
| `Notification` | D06 | Simulated notification and delivery status |
| `ActionConsentRecord` | D03/D04/D05 | Runner-only consent for non-offer actions |

All new lists default to empty, so the untouched legacy fixture
`data/synthetic/db.json` still loads and the original five marketing tasks behave
exactly as before.

### Consent model

Offer enrollment keeps the original `ConsentRecord`, which only the benchmark
runner can create. Every other irreversible action needs an
`ActionConsentRecord` with an explicit action (`PLAN_CHANGE`, `CANCELLATION`,
`RECHARGE`, `OWNERSHIP_TRANSFER`) and target reference. No business tool accepts
a consent argument, and no business tool can create either record type.

---

## 2. Invariants enforced on load

Structural:

* unique ids for every entity collection;
* every reference resolves (line → customer/plan, charge/recharge → account,
  subscription/request/identity/notification → line, request → plan);
* plan allowances are unique per plan;
* one billing account per line, owned by the line's owner;
* one active subscription per line;
* one identity record per line;
* one notification id per record, and `QUEUED`/`SENT` notifications must carry a
  `related_ref`;
* duplicate consents per `(line, action, target)` are rejected.

Marketing-specific (pre-existing):

* consent must belong to the line owner;
* enrollment must reference a matching consent;
* one enrollment per `(line, offer)`; one SMS per enrollment;
* one do-not-call record per customer.

---

## 3. Initial states

Every task references exactly one initial state by id. States are declarative
JSON under `data/synthetic/states/`, generated deterministically by
`scripts/build_states.py`.

| Domain | States |
|---|---|
| D01 | `STATE-D01-001`, `STATE-D01-002` |
| D02 | `STATE-D02-001` … `STATE-D02-004` |
| D03 | `STATE-D03-001`, `STATE-D03-002` |
| D04 | `STATE-D04-001`, `STATE-D04-002` |
| D05 | `STATE-D05-001`, `STATE-D05-002` |
| D06 | `STATE-D06-001`, `STATE-D06-002` |

Each state has:

* `state_id` — stable identifier referenced by tasks;
* `state_version` — schema version of the state payload;
* `description` — what makes this state distinct;
* `db` — the complete validated database payload;
* `state_hash` — SHA-256 of the canonical JSON of the payload.

```python
from cm_outbound_bench.benchmark.state import StateStore

store = StateStore.from_package("synthetic", "states", "d02_promotion.json")
state = store.get("STATE-D02-001")
db = state.instantiate()          # independent deep copy for one episode
print(state.state_hash)           # reproducible content hash
```

Determinism and isolation:

* identical state content always yields the identical hash;
* every episode receives a deep copy, so episode order cannot leak state;
* `execute_task` never mutates the loaded state, and a test asserts it.

State/task consistency is validated before every run: the customer must own the
line, the line must have a subscription and identity record, the offer must
exist, the simulator token must match the customer token, and consent targets
must resolve (`benchmark/execution.py::validate_task_against_state`).

---

## 4. State hash and run manifests

The evaluator records `final_state_hash` for every episode, and each run manifest
records both `initial_state_hash` and `final_state_hash`. Two runs of the same
task with the same agent must produce the same initial hash; a different final
hash proves the episode changed different state.

Hashing uses canonical JSON (`sort_keys=True`, no whitespace variation), so the
hash is stable across processes and platforms.
