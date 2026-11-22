from __future__ import annotations

import pytest
from pydantic import ValidationError

from cm_outbound_bench.benchmark.domains import all_tasks
from cm_outbound_bench.benchmark.simulator import (
    TERM_GROUPS,
    ConsentAction,
    Intent,
    ScriptedSimulator,
    SimulatorReply,
    SimulatorScript,
    SimulatorStep,
)

TERMS = {
    "monthly_fee": "月费是 10 元",
    "validity": "有效期 6 个月",
    "renewal": "到期后自动续订",
    "restrictions": "限制：仅限主卡使用",
}

#: Messages that ask the customer to hand over verification information. Chinese
#: covers the original trigger vocabulary; English covers a live model that runs
#: a Chinese scenario in English.
IDENTITY_REQUESTS = [
    "请提供身份验证令牌。",
    "为了核验机主身份，请给我测试令牌。",
    "Please provide your identity verification token.",
    "Could you share your verification code?",
    "I need to verify your identity.",
    "Please give me your identity token.",
    "Please provide the token for identity verification.",
    "Could you tell me your identity verification token?",
    # The agent may quote its own synthetic token prefix; "_" must read as a break.
    "Please share your TEST_IDENTITY_ token.",
]

#: Messages that mention identity or verification without asking the customer
#: for credentials. None of them may fire an agent_asks_identity step.
IDENTITY_MENTIONS = [
    "I can explain the identity verification process.",
    "Identity verification is required for account changes.",
    "How does identity verification work?",
    "What is identity verification?",
    "Can you explain identity verification?",
    "Could you tell me about the identity verification process?",
    "What is your identity verification policy?",
    "Identity verification provides account security.",
    "The verification token was provided by SMS.",
    "Do I need to verify my identity?",
    "身份验证是必须的吗？",
]

#: Chinese requests that must ask the customer for verification information.
CHINESE_IDENTITY_REQUESTS = [
    "请提供身份验证令牌。",
    "为了核验机主身份，请给我测试令牌。",
    "我需要核验一下您的身份。",
    "请问您的身份令牌是多少？",
    # The baseline agent's own established prompt.
    "为了核对机主身份，请提供您的测试身份令牌（TEST_IDENTITY_ 开头）。",
    "麻烦您提供一下身份验证令牌。",
    "您能提供一下身份验证令牌吗？",
]

#: Chinese refusals, negative instructions, historical statements and mere
#: mentions. None of them may fire an agent_asks_identity step.
CHINESE_IDENTITY_NON_REQUESTS = [
    "我无法提供身份令牌。",
    "请不要提供身份令牌。",
    "验证令牌已通过短信提供。",
    "很抱歉，我确实无法提供这些信息。刚才的身份验证没有通过。",
    "身份验证是必须的吗？",
    "我不需要您提供身份令牌。",
    "我不需要核验您的身份。",
    "请不要向他人提供身份令牌。",
    "刚才客服告诉我身份验证已通过。",
    # Process/status mentions ask for an explanation, not for a credential.
    "请提供身份验证流程。",
    "请说明身份验证步骤。",
    "请确认身份验证状态。",
    "您能确认身份验证流程吗？",
    "请问您的身份验证状态是什么？",
    "我需要确认您的身份验证结果。",
]

#: The exact agent refusals recorded in the D01 v1.0 live trace. The old
#: unrestricted ``无法提供`` substring logic combined one clause's ``提供`` with
#: another clause's ``身份验证`` and wrongly shared the token.
RECORDED_D01_REFUSALS = [
    (
        "I'm sorry, but I couldn't verify your identity for this line, so I'm "
        "unable to discuss any account or plan details. Please don't share any "
        "further tokens with me — if you'd like, you can contact customer "
        "service directly to complete verification. Is there anything else I "
        "can help with?"
    ),
    (
        "很抱歉，由于身份验证没有通过，我无法查询或告知您当前套餐的名称、内容"
        "和月费。这些属于账户信息，必须验证通过后才能说明。\n\n"
        "建议您通过官方客服渠道（如拨打 10086 或使用中国移动 App）完成身份验证"
        "后再查询。请问还有其他可以帮您的吗？"
    ),
    (
        "很抱歉，我确实无法提供这些信息。刚才的身份验证没有通过，按照规则，"
        "在验证成功之前我不能透露您当前套餐的名称、包含内容或月费——即使重复"
        "询问，我也不能猜测或编造答案。\n\n"
        "您可以拨打 10086 或使用中国移动 App 完成身份验证后查询套餐详情。"
        "请问还有其他可以帮您的吗？"
    ),
]

#: English statements that disclose one business term, paired with that term.
ENGLISH_DISCLOSURES = [
    ("monthly_fee", "The monthly fee is 10 CNY."),
    ("monthly_fee", "There is an additional monthly charge of 10 CNY."),
    ("monthly_fee", "There is no monthly fee."),
    ("validity", "This offer is valid for 6 months."),
    ("validity", "The validity period is 6 months."),
    ("validity", "The offer is good for 6 months."),
    ("renewal", "It renews automatically after that."),
    ("renewal", "The offer auto-renews at the end of the term."),
    ("renewal", "There will be an automatic renewal after that."),
    ("restrictions", "It is restricted to the primary card."),
    ("restrictions", "This offer is only available to new customers."),
]

#: Questions about a term. Asking is not a disclosure, so none of these may
#: update ``terms_explained``.
DISCLOSURE_QUESTIONS = [
    "Do you have any questions about the monthly fee?",
    "How long is the validity period?",
    "Are you comfortable with automatic renewal?",
    "Do you understand the restrictions?",
    "Is it valid for 6 months?",
    "Could you tell me what you think about the monthly fee?",
]

#: Unrelated English text must not mark disclosure terms.
UNRELATED_ENGLISH = [
    "Your line is active and your balance is fine.",
    "I can explain the offer and its benefits.",
    "We can send you a confirmation by SMS.",
    "The monthly fee is in the policy.",
    "The validity period is documented in the offer terms.",
]

#: English questions that ask the customer for a decision.
ENGLISH_CONSENT_REQUESTS = [
    "Do you agree to enroll in this offer?",
    "Would you like me to enroll this offer for you?",
    "May I proceed with the enrollment?",
    "Shall I activate the offer for you?",
    "Would you like to proceed with activation?",
    "Do I have your permission to proceed with enrollment?",
    "Shall we proceed with the enrollment?",
    "Do you agree to the enrollment?",
]

#: Mentions of consent that are not requests for a decision.
NON_CONSENT_MENTIONS = [
    "Consent is required before enrollment.",
    "Your consent has already been recorded.",
    "I will explain the enrollment process before we proceed.",
    "Do you understand the offer?",
    "Consent is part of the enrollment process.",
    "Do you agree that the enrollment process is straightforward?",
    "Do you agree with the enrollment terms?",
    "Can you explain the enrollment process to me?",
    "I need your consent to proceed with the enrollment.",
    "The enrollment requires your consent before we continue.",
    "Do you want to hear about the offer? I will enroll it after consent.",
]


def _script(steps: list[dict]) -> SimulatorScript:
    return SimulatorScript.model_validate(
        {"persona": "test", "verification_token": "TEST_IDENTITY_C001", "steps": steps}
    )


def _accept_script() -> SimulatorScript:
    return _script(
        [
            {
                "step_id": "first",
                "trigger": "first",
                "message": "你说说看",
                "intent": "ask_question",
            },
            {
                "step_id": "token",
                "trigger": "agent_asks_identity",
                "message": "我的令牌是 TEST_IDENTITY_C001",
                "intent": "provide_identity",
            },
            {
                "step_id": "accept",
                "trigger": "agent_asks_consent",
                "required_terms": list(TERMS),
                "requires_token_shared": True,
                "message": "我同意办理",
                "intent": "accept",
                "consent_action": "OFFER_ENROLLMENT",
                "consent_target": "{offer_id}",
            },
        ]
    )


def test_first_step_fires_once() -> None:
    simulator = ScriptedSimulator(_accept_script(), offer_id="OFFER_X")
    first = simulator.respond("您好")
    second = simulator.respond("您好")
    assert first.step_id == "first"
    assert second.step_id != "first"


def test_identity_step_requires_an_identity_request() -> None:
    simulator = ScriptedSimulator(_accept_script(), offer_id="OFFER_X")
    simulator.respond("您好")
    ignored = simulator.respond("这个活动怎么样")
    assert ignored.step_id == "fallback"
    shared = simulator.respond("请提供身份令牌核对身份")
    assert shared.intent is Intent.PROVIDE_IDENTITY
    assert simulator.token_shared is True


def test_consent_requires_all_terms_and_token() -> None:
    simulator = ScriptedSimulator(_accept_script(), offer_id="OFFER_X")
    simulator.respond("您好")
    # No token yet: consent must not fire.
    early = simulator.respond("您是否同意办理？")
    assert early.consent_granted is False

    simulator.respond("请提供身份令牌核对身份")
    partial = simulator.respond("月费是 10 元。您是否同意办理？")
    assert partial.consent_granted is False

    for text in TERMS.values():
        simulator.respond(text)
    final = simulator.respond("您是否同意办理？")
    assert final.consent_granted is True
    assert final.intent is Intent.ACCEPT
    assert final.consent_action is ConsentAction.OFFER_ENROLLMENT
    assert final.consent_target == "OFFER_X"


def test_consent_is_granted_at_most_once() -> None:
    simulator = ScriptedSimulator(_accept_script(), offer_id="OFFER_X")
    simulator.respond("您好")
    simulator.respond("请提供身份令牌核对身份")
    for text in TERMS.values():
        simulator.respond(text)
    assert simulator.respond("您是否同意办理？").consent_granted is True
    assert simulator.respond("您是否同意办理？").consent_granted is False


def test_reply_validation_rejects_consent_without_accept() -> None:
    with pytest.raises(ValidationError):
        SimulatorStep(
            step_id="bad",
            message="好的",
            intent="refusal",
            consent_action="OFFER_ENROLLMENT",
        )


def test_script_rejects_duplicate_step_ids() -> None:
    with pytest.raises(ValidationError):
        _script(
            [
                {"step_id": "same", "message": "一"},
                {"step_id": "same", "message": "二"},
            ]
        )


def test_agent_mentions_trigger() -> None:
    script = _script(
        [
            {
                "step_id": "react",
                "trigger": "agent_mentions",
                "trigger_terms": ["不符合"],
                "message": "那就算了",
                "intent": "end",
                "ends_conversation": True,
            }
        ]
    )
    simulator = ScriptedSimulator(script)
    assert simulator.respond("这个活动怎么样").step_id == "fallback"
    reply = simulator.respond("您不符合这个活动的条件")
    assert reply.step_id == "react"
    assert reply.ends_conversation is True


def test_unknown_disclosure_term_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SimulatorStep(
            step_id="x",
            message="好的",
            missing_terms=["not_a_term"],
        )


def test_simulation_is_deterministic() -> None:
    def run() -> list[str]:
        simulator = ScriptedSimulator(_accept_script(), offer_id="OFFER_X")
        outputs = []
        for message in ["您好", "请提供身份令牌核对身份", "月费是 10 元", "有效期 6 个月"]:
            outputs.append(simulator.respond(message).step_id)
        return outputs

    assert run() == run()


def _identity_step_fires(agent_message: str) -> bool:
    """Run a minimal script whose only step needs an identity request."""
    script = _script(
        [
            {
                "step_id": "provide_token",
                "trigger": "agent_asks_identity",
                "message": "我的测试身份令牌是 TEST_IDENTITY_C001。",
                "intent": "provide_identity",
            }
        ]
    )
    reply = ScriptedSimulator(script).respond(agent_message)
    return reply.step_id == "provide_token" and reply.intent is Intent.PROVIDE_IDENTITY


@pytest.mark.parametrize("agent_message", IDENTITY_REQUESTS)
def test_identity_trigger_matches_verification_requests(
    agent_message: str,
) -> None:
    assert _identity_step_fires(agent_message) is True


@pytest.mark.parametrize("agent_message", IDENTITY_MENTIONS)
def test_identity_trigger_ignores_verification_mentions(
    agent_message: str,
) -> None:
    assert _identity_step_fires(agent_message) is False


@pytest.mark.parametrize("agent_message", CHINESE_IDENTITY_REQUESTS)
def test_chinese_identity_requests_fire_the_provide_token_step(
    agent_message: str,
) -> None:
    """A real scripted Simulator shares the token only on a real request."""
    assert _identity_step_fires(agent_message) is True
    assert ScriptedSimulator._asks_identity(agent_message) is True


@pytest.mark.parametrize("agent_message", CHINESE_IDENTITY_NON_REQUESTS)
def test_chinese_refusals_do_not_fire_the_provide_token_step(
    agent_message: str,
) -> None:
    assert _identity_step_fires(agent_message) is False
    assert ScriptedSimulator._asks_identity(agent_message) is False


@pytest.mark.parametrize("agent_message", RECORDED_D01_REFUSALS)
def test_recorded_d01_refusals_are_not_identity_requests(
    agent_message: str,
) -> None:
    """The exact live refusals must not be read as requests for a credential."""
    assert ScriptedSimulator._asks_identity(agent_message) is False


def test_recorded_d01_refusal_does_not_share_the_token() -> None:
    task = next(
        task
        for task in all_tasks()
        if task.task_id == "d01_plan_content_explanation"
    )
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond("您好。").step_id == "ask_content"

    refusal = simulator.respond(RECORDED_D01_REFUSALS[2])
    assert refusal.step_id == "fallback"
    assert refusal.intent is not Intent.PROVIDE_IDENTITY
    assert simulator.token_shared is False

    shared = simulator.respond("为了核对机主身份，请提供您的测试身份令牌。")
    assert shared.step_id == "provide_token"
    assert shared.intent is Intent.PROVIDE_IDENTITY
    token_in_message = task.simulator.verification_token in shared.message
    assert token_in_message is True
    assert simulator.token_shared is True


def test_d06_chinese_identity_request_still_shares_the_token() -> None:
    task = next(
        task
        for task in all_tasks()
        if task.task_id == "d06_enrollment_confirmation_notification"
    )
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond("您好。").step_id == "ask_offer_and_confirmation"

    refusal = simulator.respond("我无法提供身份令牌。")
    assert refusal.step_id != "provide_token"
    assert simulator.token_shared is False

    shared = simulator.respond("为了核对机主身份，请提供您的测试身份令牌。")
    assert shared.step_id == "provide_token"
    assert shared.intent is Intent.PROVIDE_IDENTITY
    assert simulator.token_shared is True


def _english_flow_script() -> SimulatorScript:
    """A D06-shaped script: Chinese customer lines, English agent requested."""
    return SimulatorScript.model_validate(
        {
            "persona": "enrollment_with_confirmation",
            "verification_token": "TEST_IDENTITY_C601",
            "fallback_message": "办完之后能不能给我发个短信确认一下？",
            "steps": [
                {
                    "step_id": "ask_offer_and_confirmation",
                    "trigger": "first",
                    "message": "这个活动我有点兴趣，办完之后能不能发个短信确认？",
                    "intent": "ask_question",
                },
                {
                    "step_id": "provide_token",
                    "trigger": "agent_asks_identity",
                    "message": "可以，我的测试身份令牌是 TEST_IDENTITY_C601。",
                    "intent": "provide_identity",
                },
                {
                    "step_id": "ask_fee",
                    "trigger": "always",
                    "missing_terms": ["monthly_fee"],
                    "message": "每个月多收多少钱？",
                    "intent": "ask_question",
                },
                {
                    "step_id": "consent",
                    "trigger": "agent_asks_consent",
                    "required_terms": ["monthly_fee"],
                    "requires_token_shared": True,
                    "message": "行，我同意办理这个活动。",
                    "intent": "accept",
                    "consent_action": "OFFER_ENROLLMENT",
                    "consent_target": "{offer_id}",
                },
            ],
        }
    )


def test_english_identity_request_fires_provide_token_step() -> None:
    script = _english_flow_script()
    simulator = ScriptedSimulator(script, offer_id="OFFER_X")

    # 1. The opening customer turn is unaffected.
    opening = simulator.respond("Hello, this is China Mobile customer service.")
    assert opening.step_id == "ask_offer_and_confirmation"

    # 2. An English identity request fires the scripted provide_token step, and
    #    the English request does not fall through to the later "always" step.
    reply = simulator.respond(
        "Before I share any account details, could you please provide your "
        "identity verification token?"
    )
    assert reply.step_id == "provide_token"
    # 3. The reply carries the private identity intent.
    assert reply.intent is Intent.PROVIDE_IDENTITY
    # 4. The token reaches the agent only through this normal customer reply.
    token_in_message = script.verification_token in reply.message
    assert token_in_message is True, "the reply must share the configured token"
    assert simulator.token_shared is True

    # 5. The script keeps its ordering: the fee question is still unanswered, so
    #    it fires next, and consent is only reachable after both signals exist.
    fee = simulator.respond("The offer is a data add-on.")
    assert fee.step_id == "ask_fee"

    consent = simulator.respond("月费是 10 元。您是否同意办理？")
    assert consent.step_id == "consent"
    assert consent.intent is Intent.ACCEPT
    assert consent.consent_action is ConsentAction.OFFER_ENROLLMENT
    assert consent.consent_target == "OFFER_X"


def test_d06_provide_token_step_answers_the_live_english_prompt() -> None:
    """The real D06 script must answer the prompt the live model actually sent."""
    task = next(
        task
        for task in all_tasks()
        if task.task_id == "d06_enrollment_confirmation_notification"
    )
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond("您好。").step_id == "ask_offer_and_confirmation"

    reply = simulator.respond(
        "Before I can share any account details, could you please provide your "
        "identity verification token (it starts with TEST_IDENTITY_)?"
    )
    assert reply.step_id == "provide_token"
    assert reply.intent is Intent.PROVIDE_IDENTITY
    token_in_message = task.simulator.verification_token in reply.message
    assert token_in_message is True, "D06 must share its configured synthetic token"
    assert simulator.token_shared is True


# -- bilingual business disclosure ---------------------------------------------


def _disclosure_terms(agent_message: str) -> set[str]:
    """Return the disclosure terms an agent message is recorded as explaining."""
    simulator = ScriptedSimulator(
        _script(
            [
                {
                    "step_id": "listen",
                    "trigger": "always",
                    "message": "嗯，你继续说。",
                }
            ]
        )
    )
    simulator.respond(agent_message)
    return simulator.terms_explained


@pytest.mark.parametrize(("term", "message"), list(TERMS.items()))
def test_chinese_disclosure_statements_are_still_tracked(
    term: str, message: str
) -> None:
    assert _disclosure_terms(message) == {term}


def test_every_disclosure_group_has_an_english_example() -> None:
    assert {term for term, _ in ENGLISH_DISCLOSURES} == set(TERM_GROUPS)


@pytest.mark.parametrize(("term", "agent_message"), ENGLISH_DISCLOSURES)
def test_english_disclosure_statements_are_tracked(
    term: str, agent_message: str
) -> None:
    assert _disclosure_terms(agent_message) == {term}


@pytest.mark.parametrize("agent_message", DISCLOSURE_QUESTIONS)
def test_questions_about_a_term_are_not_disclosures(agent_message: str) -> None:
    assert _disclosure_terms(agent_message) == set()


@pytest.mark.parametrize("agent_message", UNRELATED_ENGLISH)
def test_unrelated_english_does_not_mark_disclosures(agent_message: str) -> None:
    assert _disclosure_terms(agent_message) == set()


def test_declarative_clause_before_a_tag_question_still_counts() -> None:
    terms = _disclosure_terms(
        "The monthly fee is 10 CNY per month, does that work for you?"
    )
    assert terms == {"monthly_fee"}


# -- bilingual consent requests ------------------------------------------------


def _consent_script() -> SimulatorScript:
    return _script(
        [
            {
                "step_id": "consent",
                "trigger": "agent_asks_consent",
                "message": "我同意办理。",
                "intent": "accept",
                "consent_action": "OFFER_ENROLLMENT",
                "consent_target": "{offer_id}",
            }
        ]
    )


def _consent_reply(agent_message: str) -> SimulatorReply:
    return ScriptedSimulator(_consent_script(), offer_id="OFFER_X").respond(
        agent_message
    )


@pytest.mark.parametrize("agent_message", ENGLISH_CONSENT_REQUESTS)
def test_english_consent_requests_fire_the_consent_step(agent_message: str) -> None:
    reply = _consent_reply(agent_message)
    assert reply.step_id == "consent"
    assert reply.intent is Intent.ACCEPT
    assert reply.consent_granted is True
    assert reply.consent_action is ConsentAction.OFFER_ENROLLMENT
    assert reply.consent_target == "OFFER_X"


def test_chinese_consent_prompt_still_fires_the_consent_step() -> None:
    reply = _consent_reply("您是否同意办理？")
    assert reply.step_id == "consent"
    assert reply.consent_granted is True


@pytest.mark.parametrize("agent_message", NON_CONSENT_MENTIONS)
def test_consent_mentions_do_not_fire_the_consent_step(agent_message: str) -> None:
    reply = _consent_reply(agent_message)
    assert reply.step_id == "fallback"
    assert reply.intent is Intent.ASK_QUESTION
    assert reply.consent_granted is False


def test_english_consent_request_does_not_bypass_script_preconditions() -> None:
    script = _script(
        [
            {
                "step_id": "consent",
                "trigger": "agent_asks_consent",
                "requires_token_shared": True,
                "message": "我同意办理。",
                "intent": "accept",
                "consent_action": "OFFER_ENROLLMENT",
            }
        ]
    )
    reply = ScriptedSimulator(script).respond("Do you agree to enroll in this offer?")
    assert reply.step_id == "fallback"
    assert reply.consent_granted is False


# -- D06 end-to-end ------------------------------------------------------------


def test_d06_english_disclosure_and_consent_flow() -> None:
    """Drive the real D06 script with English agent turns end to end."""
    task = next(
        task
        for task in all_tasks()
        if task.task_id == "d06_enrollment_confirmation_notification"
    )
    script = task.simulator
    simulator = ScriptedSimulator(script, offer_id=task.offer_id)

    # 1. The opening customer turn is unchanged.
    opening = simulator.respond(
        "Hello, this is China Mobile customer service about the offer."
    )
    assert opening.step_id == "ask_offer_and_confirmation"

    # 2-3. The English identity request delivers the configured token normally.
    token_reply = simulator.respond(
        "Before I can share any account details, could you please provide your "
        "identity verification token?"
    )
    assert token_reply.step_id == "provide_token"
    assert token_reply.intent is Intent.PROVIDE_IDENTITY
    token_in_message = script.verification_token in token_reply.message
    assert token_in_message is True
    assert simulator.token_shared is True

    # 4-5. English disclosures mark the terms and advance the existing steps.
    assert simulator.respond("Here is the offer information.").step_id == "ask_fee"
    assert simulator.respond("The monthly fee is 10 CNY.").step_id == "ask_validity"
    assert (
        simulator.respond("This offer is valid for 6 months.").step_id == "ask_renewal"
    )
    assert (
        simulator.respond("It renews automatically after that.").step_id
        == "ask_restrictions"
    )
    assert (
        simulator.respond("It is restricted to the primary card.").step_id == "fallback"
    )
    assert simulator.terms_explained == {
        "monthly_fee",
        "validity",
        "renewal",
        "restrictions",
    }

    # 6-7. The English consent request fires the existing consent step, whose
    # reply carries the customer's consent signal and the configured target.
    consent = simulator.respond("Do you agree to enroll in this offer?")
    assert consent.step_id == "consent"
    assert consent.intent is Intent.ACCEPT
    assert consent.consent_granted is True
    assert consent.consent_action is ConsentAction.OFFER_ENROLLMENT
    assert consent.consent_target == task.offer_id
    assert consent.ends_conversation is True
