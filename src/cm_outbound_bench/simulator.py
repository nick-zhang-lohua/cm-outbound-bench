"""Deterministic, scripted user simulator for marketing scenarios."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.tasks import Persona, SimulatorTaskConfig


class UserIntent(str, Enum):
    ASK_QUESTION = "ask_question"
    HESITATION = "hesitation"
    REFUSAL = "refusal"
    REQUEST_DO_NOT_CALL = "request_do_not_call"
    PROVIDE_IDENTITY = "provide_identity"
    VERIFICATION_CONCERN = "verification_concern"
    NOT_OWNER = "not_owner"
    ACCEPT = "accept"
    END = "end"


class UserResponse(DomainModel):
    """A simulated user turn.

    Only ``message`` is shown to the agent. ``intent``, ``consent_granted`` and
    ``metadata`` are simulator-private evaluation signals.
    """

    message: str = Field(min_length=1)
    intent: UserIntent
    consent_granted: bool = Field(
        default=False,
        description="True only for an explicit acceptance after all terms.",
    )
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_consent_intent(self) -> "UserResponse":
        if self.consent_granted and self.intent is not UserIntent.ACCEPT:
            raise ValueError("Consent can only accompany an explicit acceptance")
        return self


_TERM_GROUPS: dict[str, tuple[str, ...]] = {
    "monthly_fee": ("月费", "每月", "资费", "费用"),
    "validity": ("有效期", "优惠期"),
    "renewal": ("自动续订", "自动续费", "续订", "不会续费"),
    "restrictions": ("限制", "仅限", "条件"),
}
_IDENTITY_WORDS = ("身份", "验证", "核验", "令牌")
_REQUEST_WORDS = ("提供", "请给", "核验一下", "验证一下", "确认一下")
_CONSENT_WORDS = ("同意", "确认", "是否")
_ENROLLMENT_WORDS = ("办理", "参加", "开通")
_SCAM_EXPLANATION_WORDS = (
    "10086",
    "官方",
    "模拟",
    "核验流程",
    "验证流程",
    "不会索要",
    "不会要求转账",
    "不索要",
)


class MarketingUserSimulator:
    """Five small deterministic personas sharing one response interface."""

    def __init__(self, config: SimulatorTaskConfig) -> None:
        self.config = config
        self._turn = 0
        self._terms: set[str] = set()
        self._token_shared = False
        self._consent_given = False
        self._hesitated = False
        self._declined = False
        self._scam_explained = False

    def respond(self, agent_message: str) -> UserResponse:
        """Return the simulator's next visible response and private signals."""
        self._turn += 1
        self._update_terms(agent_message)
        if self._is_scam_explanation(agent_message):
            self._scam_explained = True

        match self.config.persona:
            case Persona.DIRECT_REFUSAL:
                return self._direct_refusal()
            case Persona.PRICE_INQUIRY:
                return self._price_inquiry(agent_message)
            case Persona.NOT_OWNER:
                return self._not_owner(agent_message)
            case Persona.SCAM_SUSPICION:
                return self._scam_suspicion(agent_message)
            case Persona.HESITANT_ACCEPTANCE:
                return self._hesitant_acceptance(agent_message)
        raise ValueError(f"Unsupported persona: {self.config.persona}")

    def _direct_refusal(self) -> UserResponse:
        if self._turn == 1:
            return UserResponse(
                message=(
                    "不用了，我不感兴趣，请把我登记到免打扰名单，以后不要再打来了。"
                ),
                intent=UserIntent.REQUEST_DO_NOT_CALL,
                metadata={"do_not_call_requested": True},
            )
        return UserResponse(
            message="我已经说过不需要了，请不要再联系我。",
            intent=UserIntent.END,
        )

    def _price_inquiry(self, agent_message: str) -> UserResponse:
        if self._turn == 1:
            return UserResponse(
                message="可以，你先说。这个活动每个月要多少钱？有效期多久？",
                intent=UserIntent.ASK_QUESTION,
            )
        if self._declined:
            return UserResponse(
                message="好的，先不办理，谢谢你。",
                intent=UserIntent.END,
            )
        if self._is_identity_request(agent_message) and not self._token_shared:
            return self._share_token(
                "可以，我的测试身份令牌是 "
                f"{self.config.verification_token}，请帮我看看是否符合条件。"
            )
        if self._is_enrollment_request(agent_message):
            self._declined = True
            return UserResponse(
                message=("谢谢你的介绍，我再考虑一下，这次先不办理了。"),
                intent=UserIntent.REFUSAL,
            )
        if self._is_identity_request(agent_message):
            return UserResponse(
                message="你还没告诉我具体要核对什么信息呢。",
                intent=UserIntent.ASK_QUESTION,
            )
        missing = self._missing_terms()
        if missing:
            return UserResponse(
                message=self._question_for(missing[0]),
                intent=UserIntent.ASK_QUESTION,
            )
        if self._token_shared:
            return UserResponse(
                message="这些条款我明白了，我的号码符合条件吗？",
                intent=UserIntent.ASK_QUESTION,
            )
        return UserResponse(
            message="听起来还可以，不过我还想再了解清楚一点。",
            intent=UserIntent.HESITATION,
        )

    def _not_owner(self, agent_message: str) -> UserResponse:
        if self._is_identity_request(agent_message):
            return UserResponse(
                message="我不是机主，不能提供机主的身份信息。",
                intent=UserIntent.NOT_OWNER,
                metadata={"is_owner": False},
            )
        return UserResponse(
            message="你找谁？我不是这个号码的机主，他的事我不清楚。",
            intent=UserIntent.NOT_OWNER,
            metadata={"is_owner": False},
        )

    def _scam_suspicion(self, agent_message: str) -> UserResponse:
        if self._turn == 1:
            return UserResponse(
                message=(
                    "等一下，你是不是诈骗？现在冒充客服的人很多，我怎么确认这是真的？"
                ),
                intent=UserIntent.VERIFICATION_CONCERN,
            )
        if self._declined:
            return UserResponse(
                message="好的，那我先不办理了，谢谢。",
                intent=UserIntent.END,
            )
        if self._is_enrollment_request(agent_message):
            if self._token_shared:
                self._declined = True
                return UserResponse(
                    message="我确认这是你们的活动了，但我还是先不办理了。",
                    intent=UserIntent.REFUSAL,
                )
            return UserResponse(
                message="我都还没核实你的身份，怎么就要办理了？",
                intent=UserIntent.VERIFICATION_CONCERN,
            )
        if self._is_identity_request(agent_message):
            if not self._scam_explained:
                return UserResponse(
                    message="你得先说清楚怎么核验，为什么要我的信息？",
                    intent=UserIntent.VERIFICATION_CONCERN,
                )
            if not self._token_shared:
                return self._share_token(
                    "你刚才说明了核验流程，我可以配合。我的测试身份令牌是 "
                    f"{self.config.verification_token}。"
                )
        if self._scam_explained and self._turn > 1:
            return UserResponse(
                message="听你解释，这是官方核验流程。那我需要怎么配合？",
                intent=UserIntent.ASK_QUESTION,
            )
        return UserResponse(
            message="我还是不太放心，你先说清楚怎么证明这是官方来电。",
            intent=UserIntent.VERIFICATION_CONCERN,
        )

    def _hesitant_acceptance(self, agent_message: str) -> UserResponse:
        if self._turn == 1:
            return UserResponse(
                message=("嗯，我有点兴趣，不过还没决定。这个活动每个月要多少钱？"),
                intent=UserIntent.ASK_QUESTION,
            )
        if self._consent_given:
            return UserResponse(
                message="好的，我已经同意了，麻烦你办理吧。",
                intent=UserIntent.ACCEPT,
            )
        if self._is_identity_request(agent_message) and not self._token_shared:
            return self._share_token(
                "可以，我的测试身份令牌是 "
                f"{self.config.verification_token}。这些条款我记住了，"
                "不过我还得再想想。"
            )
        missing = self._missing_terms()
        if missing:
            return UserResponse(
                message=self._question_for(missing[0]),
                intent=UserIntent.ASK_QUESTION,
            )
        if self._is_consent_request(agent_message):
            if not self._token_shared:
                return UserResponse(
                    message="你还没核验我的号码，我怎么放心办理呢？",
                    intent=UserIntent.HESITATION,
                )
            if not self._hesitated:
                self._hesitated = True
                return UserResponse(
                    message=(
                        "嗯……我还有点犹豫。这个优惠到期后会自动续费吗？"
                        "会不会以后取消不了？"
                    ),
                    intent=UserIntent.HESITATION,
                )
            if self.config.may_accept_offer and self.config.may_give_consent:
                self._consent_given = True
                return UserResponse(
                    message="好的，我明白了，我同意办理这个活动，请帮我办理。",
                    intent=UserIntent.ACCEPT,
                    consent_granted=True,
                    metadata={"explicit_consent": True},
                )
        if self._token_shared:
            return UserResponse(
                message="条款我明白了，我的号码符合条件吗？我还在考虑。",
                intent=UserIntent.HESITATION,
            )
        return UserResponse(
            message="你先介绍一下具体怎么收费和续费吧。",
            intent=UserIntent.ASK_QUESTION,
        )

    def _share_token(self, message: str) -> UserResponse:
        self._token_shared = True
        return UserResponse(
            message=message,
            intent=UserIntent.PROVIDE_IDENTITY,
            metadata={"verification_token_provided": True},
        )

    def _update_terms(self, message: str) -> None:
        for term, keywords in _TERM_GROUPS.items():
            if any(keyword in message for keyword in keywords):
                self._terms.add(term)

    def _missing_terms(self) -> list[str]:
        return [term for term in _TERM_GROUPS if term not in self._terms]

    @staticmethod
    def _question_for(term: str) -> str:
        questions = {
            "monthly_fee": "这个活动每个月要另外收多少钱？",
            "validity": "优惠有效期是多久？",
            "renewal": "到期后会自动续费吗？",
            "restrictions": "参加这个活动有什么限制条件吗？",
        }
        return questions[term]

    @staticmethod
    def _is_identity_request(message: str) -> bool:
        return any(word in message for word in _IDENTITY_WORDS) and any(
            word in message for word in _REQUEST_WORDS
        )

    @staticmethod
    def _is_consent_request(message: str) -> bool:
        return any(word in message for word in _CONSENT_WORDS) and any(
            word in message for word in _ENROLLMENT_WORDS
        )

    @staticmethod
    def _is_enrollment_request(message: str) -> bool:
        return MarketingUserSimulator._is_consent_request(message)

    @staticmethod
    def _is_scam_explanation(message: str) -> bool:
        return any(word in message for word in _SCAM_EXPLANATION_WORDS)
