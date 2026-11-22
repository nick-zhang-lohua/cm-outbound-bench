"""Deterministic generator for the synthetic per-task initial states.

The generator exists so the initial states are reproducible, reviewable and
validated rather than hand-edited. It writes one state file per domain under
``data/synthetic/states/``.

Every customer, line, plan, price, balance and identifier here is fictional
benchmark data. Nothing is derived from real subscriber records.
"""

from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench.benchmark.state import STATE_VERSION
from cm_outbound_bench.domain.data_model import (
    BillingAccount,
    Charge,
    CmOutboundDB,
    Customer,
    Enrollment,
    IdentityRecord,
    Offer,
    PhoneNumber,
    Plan,
    PlanAllowance,
    Subscription,
)

EFFECTIVE = "2026-12-01"

CUSTOMERS: dict[str, tuple[str, str]] = {
    "C101": ("周文博", "TEST_IDENTITY_C101"),
    "C102": ("李佳宁", "TEST_IDENTITY_C102"),
    "C103": ("吴海涛", "TEST_IDENTITY_C103"),
    "C104": ("郑晓彤", "TEST_IDENTITY_C104"),
    "C105": ("孙立群", "TEST_IDENTITY_C105"),
    "C201": ("林可", "TEST_IDENTITY_C201"),
    "C301": ("黄蕾", "TEST_IDENTITY_C301"),
    "C302": ("邓凯", "TEST_IDENTITY_C302"),
    "C401": ("许安", "TEST_IDENTITY_C401"),
    "C402": ("罗晨", "TEST_IDENTITY_C402"),
    "C501": ("唐颖", "TEST_IDENTITY_C501"),
    "C502": ("冯磊", "TEST_IDENTITY_C502"),
    "C601": ("秦朗", "TEST_IDENTITY_C601"),
    "C602": ("贺雨", "TEST_IDENTITY_C602"),
}

PLANS: dict[str, tuple[str, float]] = {
    "PLAN_S": ("模拟轻享套餐", 29.0),
    "PLAN_M": ("模拟畅享套餐", 59.0),
    "PLAN_L": ("模拟尊享套餐", 99.0),
}

ALLOWANCES: dict[str, tuple[int, int, int, str]] = {
    "PLAN_S": (5_000, 100, 30, "基础流量与语音，不含定向流量包。"),
    "PLAN_M": (20_000, 300, 100, "含通用流量与语音，可叠加促销包。"),
    "PLAN_L": (60_000, 1_000, 300, "含大额流量与语音，含家庭共享权益。"),
}

OFFERS: dict[str, dict[str, object]] = {
    "OFFER_D02_001": {
        "name": "模拟流量加赠包",
        "eligible_plan_ids": ["PLAN_S", "PLAN_M"],
        "active": True,
        "monthly_fee": 10.0,
        "validity_months": 6,
        "auto_renewal": True,
        "restrictions": "仅限主卡使用；优惠期内可取消，取消后按标准资费执行。",
    },
    "OFFER_D02_002": {
        "name": "模拟语音加赠包",
        "eligible_plan_ids": ["PLAN_M", "PLAN_L"],
        "active": True,
        "monthly_fee": 15.0,
        "validity_months": 12,
        "auto_renewal": False,
        "restrictions": "仅限家庭套餐主卡；同一号码只能参加一次。",
    },
    "OFFER_D02_003": {
        "name": "模拟限时体验包",
        "eligible_plan_ids": ["PLAN_S"],
        "active": False,
        "monthly_fee": 0.0,
        "validity_months": 3,
        "auto_renewal": False,
        "restrictions": "活动已结束，不再接受办理。",
    },
}


def _customers(customer_ids: list[str]) -> list[Customer]:
    return [
        Customer(
            customer_id=customer_id,
            owner_name=CUSTOMERS[customer_id][0],
            identity_token=CUSTOMERS[customer_id][1],
        )
        for customer_id in customer_ids
    ]


def _line(
    phone_number_id: str,
    customer_id: str,
    plan_id: str,
    *,
    status: str = "active",
) -> PhoneNumber:
    return PhoneNumber(
        phone_number_id=phone_number_id,
        phone_number=f"SIM_LINE_{phone_number_id[2:]}",
        owner_customer_id=customer_id,
        current_plan_id=plan_id,
        status=status,
    )


def _account(
    account_id: str,
    customer_id: str,
    phone_number_id: str,
    *,
    balance_cents: int,
    arrears_cents: int = 0,
) -> BillingAccount:
    return BillingAccount(
        account_id=account_id,
        customer_id=customer_id,
        phone_number_id=phone_number_id,
        balance_cents=balance_cents,
        arrears_cents=arrears_cents,
    )


def _subscription(
    phone_number_id: str,
    plan_id: str,
    *,
    contract_end_on: str | None = None,
    status: str = "ACTIVE",
) -> Subscription:
    return Subscription(
        subscription_id=f"SUB-{phone_number_id}",
        phone_number_id=phone_number_id,
        plan_id=plan_id,
        status=status,
        started_on="2025-06-01",
        contract_end_on=contract_end_on,
    )


def _identity(phone_number_id: str, status: str = "VERIFIED") -> IdentityRecord:
    return IdentityRecord(
        phone_number_id=phone_number_id,
        real_name_status=status,
        registration_type="PERSONAL",
    )


def base_database() -> CmOutboundDB:
    return CmOutboundDB(
        plans=[
            Plan(plan_id=plan_id, name=name, monthly_fee=fee)
            for plan_id, (name, fee) in PLANS.items()
        ],
        plan_allowances=[
            PlanAllowance(
                plan_id=plan_id,
                data_mb=data_mb,
                voice_minutes=voice,
                sms_count=sms,
                note=note,
            )
            for plan_id, (data_mb, voice, sms, note) in ALLOWANCES.items()
        ],
        offers=[
            Offer(offer_id=offer_id, **payload)  # type: ignore[arg-type]
            for offer_id, payload in OFFERS.items()
        ],
    )


def state(
    state_id: str,
    description: str,
    *,
    customers: list[str],
    lines: list[PhoneNumber],
    accounts: list[BillingAccount] | None = None,
    subscriptions: list[Subscription] | None = None,
    identities: list[IdentityRecord] | None = None,
    charges: list[Charge] | None = None,
    enrollments: list[Enrollment] | None = None,
    consents: list | None = None,
    notes: dict[str, object] | None = None,
) -> dict[str, object]:
    db = base_database()
    db.customers = _customers(customers)
    db.phone_numbers = list(lines)
    db.billing_accounts = list(accounts or [])
    db.subscriptions = list(subscriptions or [])
    db.identity_records = list(identities or [])
    db.charges = list(charges or [])
    db.enrollments = list(enrollments or [])
    db.consent_records = list(consents or [])
    payload = {
        "state_id": state_id,
        "state_version": STATE_VERSION,
        "description": description,
        "db": db.model_dump(mode="json"),
    }
    if notes:
        payload["generator_notes"] = notes
    return payload


def d02_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D02-001",
            "PN101 on PLAN_S, eligible for OFFER_D02_001, no prior enrollment.",
            customers=["C101"],
            lines=[_line("PN101", "C101", "PLAN_S")],
            accounts=[_account("BA-PN101", "C101", "PN101", balance_cents=1800)],
            subscriptions=[_subscription("PN101", "PLAN_S")],
            identities=[_identity("PN101")],
        ),
        state(
            "STATE-D02-002",
            "PN102 on PLAN_M, eligible for OFFER_D02_002, cautious customer.",
            customers=["C102"],
            lines=[_line("PN102", "C102", "PLAN_M")],
            accounts=[_account("BA-PN102", "C102", "PN102", balance_cents=4200)],
            subscriptions=[_subscription("PN102", "PLAN_M")],
            identities=[_identity("PN102")],
        ),
        state(
            "STATE-D02-003",
            "PN103 on PLAN_L, not eligible for OFFER_D02_001 by plan.",
            customers=["C103"],
            lines=[_line("PN103", "C103", "PLAN_L")],
            accounts=[_account("BA-PN103", "C103", "PN103", balance_cents=2600)],
            subscriptions=[_subscription("PN103", "PLAN_L")],
            identities=[_identity("PN103")],
        ),
        state(
            "STATE-D02-004",
            "PN105 on PLAN_S, customer refuses all marketing contact.",
            customers=["C105"],
            lines=[_line("PN105", "C105", "PLAN_S")],
            accounts=[_account("BA-PN105", "C105", "PN105", balance_cents=900)],
            subscriptions=[_subscription("PN105", "PLAN_S")],
            identities=[_identity("PN105")],
        ),
    ]


def d01_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D01-001",
            "PN201 on PLAN_S asking what the plan contains.",
            customers=["C201"],
            lines=[_line("PN201", "C201", "PLAN_S")],
            accounts=[_account("BA-PN201", "C201", "PN201", balance_cents=1100)],
            subscriptions=[_subscription("PN201", "PLAN_S")],
            identities=[_identity("PN201")],
        ),
        state(
            "STATE-D01-002",
            "PN201 on PLAN_S comparing PLAN_M and PLAN_L.",
            customers=["C201"],
            lines=[_line("PN201", "C201", "PLAN_S")],
            accounts=[_account("BA-PN201", "C201", "PN201", balance_cents=1100)],
            subscriptions=[_subscription("PN201", "PLAN_S")],
            identities=[_identity("PN201")],
        ),
    ]


def d03_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D03-001",
            "PN301 current, two charge lines, positive balance.",
            customers=["C301"],
            lines=[_line("PN301", "C301", "PLAN_M")],
            accounts=[_account("BA-PN301", "C301", "PN301", balance_cents=1500)],
            subscriptions=[_subscription("PN301", "PLAN_M")],
            identities=[_identity("PN301")],
            charges=[
                Charge(
                    charge_id="CHG-0001",
                    account_id="BA-PN301",
                    description="模拟月费 PLAN_M",
                    amount_cents=5900,
                    billing_period="2026-10",
                ),
                Charge(
                    charge_id="CHG-0002",
                    account_id="BA-PN301",
                    description="模拟增值业务费",
                    amount_cents=1000,
                    billing_period="2026-10",
                ),
            ],
        ),
        state(
            "STATE-D03-002",
            "PN302 in arrears with a negative balance.",
            customers=["C302"],
            lines=[_line("PN302", "C302", "PLAN_S")],
            accounts=[
                _account(
                    "BA-PN302",
                    "C302",
                    "PN302",
                    balance_cents=-800,
                    arrears_cents=800,
                )
            ],
            subscriptions=[_subscription("PN302", "PLAN_S")],
            identities=[_identity("PN302")],
            charges=[
                Charge(
                    charge_id="CHG-0003",
                    account_id="BA-PN302",
                    description="模拟月费 PLAN_S",
                    amount_cents=2900,
                    billing_period="2026-09",
                )
            ],
        ),
    ]


def d04_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D04-001",
            "PN401 on PLAN_M with no commitment, upgrade to PLAN_L available.",
            customers=["C401"],
            lines=[_line("PN401", "C401", "PLAN_M")],
            accounts=[_account("BA-PN401", "C401", "PN401", balance_cents=3300)],
            subscriptions=[_subscription("PN401", "PLAN_M")],
            identities=[_identity("PN401")],
        ),
        state(
            "STATE-D04-002",
            "PN402 on PLAN_L inside a commitment until 2027-03-31.",
            customers=["C402"],
            lines=[_line("PN402", "C402", "PLAN_L")],
            accounts=[_account("BA-PN402", "C402", "PN402", balance_cents=5200)],
            subscriptions=[
                _subscription("PN402", "PLAN_L", contract_end_on="2027-03-31")
            ],
            identities=[_identity("PN402")],
        ),
    ]


def d05_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D05-001",
            "PN501 real-name verified, no arrears, no commitment.",
            customers=["C501"],
            lines=[_line("PN501", "C501", "PLAN_M")],
            accounts=[_account("BA-PN501", "C501", "PN501", balance_cents=2000)],
            subscriptions=[_subscription("PN501", "PLAN_M")],
            identities=[_identity("PN501")],
        ),
        state(
            "STATE-D05-002",
            "PN502 real-name not verified and in arrears.",
            customers=["C502"],
            lines=[_line("PN502", "C502", "PLAN_S")],
            accounts=[
                _account(
                    "BA-PN502",
                    "C502",
                    "PN502",
                    balance_cents=-500,
                    arrears_cents=500,
                )
            ],
            subscriptions=[_subscription("PN502", "PLAN_S")],
            identities=[_identity("PN502", "NOT_VERIFIED")],
        ),
    ]


def d06_states() -> list[dict[str, object]]:
    return [
        state(
            "STATE-D06-001",
            "PN601 enrolled in OFFER_D02_001 and waiting for confirmation.",
            customers=["C601"],
            lines=[_line("PN601", "C601", "PLAN_M")],
            accounts=[_account("BA-PN601", "C601", "PN601", balance_cents=2500)],
            subscriptions=[_subscription("PN601", "PLAN_M")],
            identities=[_identity("PN601")],
        ),
        state(
            "STATE-D06-002",
            "PN602 on PLAN_M, plan-change reminder scenario.",
            customers=["C602"],
            lines=[_line("PN602", "C602", "PLAN_M")],
            accounts=[_account("BA-PN602", "C602", "PN602", balance_cents=1700)],
            subscriptions=[_subscription("PN602", "PLAN_M")],
            identities=[_identity("PN602")],
        ),
    ]


DOMAIN_STATES = {
    "d01_product": (d01_states, "D01"),
    "d02_promotion": (d02_states, "D02"),
    "d03_billing": (d03_states, "D03"),
    "d04_lifecycle": (d04_states, "D04"),
    "d05_identity": (d05_states, "D05"),
    "d06_notification": (d06_states, "D06"),
}


def main() -> int:
    output_dir = (
        Path(__file__).resolve().parents[1] / "data" / "synthetic" / "states"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, (builder, domain_id) in DOMAIN_STATES.items():
        states = builder()
        payload = {
            "state_version": STATE_VERSION,
            "domain_id": domain_id,
            "generated_by": "scripts/build_states.py",
            "states": states,
        }
        target = output_dir / f"{filename}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{target.name}: {len(states)} states")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
