"""Deterministic, explainable, multi-label domain classification.

This is a *candidate* labelling pipeline. Nothing here produces ground truth and
nothing here may be reported as business-intent prevalence.

Method (fully reproducible from the corpus database):

1. **Eligibility.** Only ``SPEECH`` records with non-empty masked text of at
   least four characters are eligible. Event records and empty-after-masking
   records stay unclassified: they carry no lexical evidence.
2. **Lexical evidence.** Each domain has curated *strong* and *weak* term
   groups. Terms are matched as whole phrases (never single characters), with a
   short negation window in front of every match: ``没有优惠`` does not evidence a
   promotion.
3. **Threshold.** A record receives a domain when it has at least one strong
   group or at least two distinct weak term matches. Weak evidence alone never
   assigns a domain.
4. **Multi-label.** Domains are scored independently, so a record can carry
   several. A cancellation of a promotion is genuinely both D02 and D04.
5. **Confidence.** ``min(0.95, 0.45 + 0.15 * score)`` where a strong group scores
   1.0 and a weak group scores 0.5, with strong capped at 3 and weak at 2.
6. **Unit rollout.** A transcript unit inherits every domain supported by its
   records; the domain with the most supporting records becomes ``is_primary``.
7. **Ambiguity.** Records with two or more domains whose confidences differ by
   less than 0.10 are counted as ambiguous. They keep all labels; they are never
   collapsed to one.

Every stored assignment records ``label_method = RULE_CANDIDATE`` and
``review_status = CANDIDATE``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import BaseModel, Field

from cm_outbound_bench.corpus.paths import resolve_database_path
from cm_outbound_bench.corpus.schema import TAXONOMY_VERSION

ASSIGNMENT_VERSION = "1.0"
LABEL_METHOD = "RULE_CANDIDATE"
REVIEW_STATUS = "CANDIDATE"
MODEL_NAME = "cm-outbound-lexical-v1"

MIN_TEXT_LENGTH = 4
STRONG_WEIGHT = 1.0
WEAK_WEIGHT = 0.5
ASSIGN_THRESHOLD = 1.0
AMBIGUITY_DELTA = 0.10


class ClassificationError(RuntimeError):
    """Raised when classification cannot run."""


@dataclass(frozen=True)
class TermGroup:
    """One curated evidence group inside a domain."""

    group_id: str
    tier: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class DomainLexicon:
    domain_id: str
    strong: tuple[TermGroup, ...]
    weak: tuple[TermGroup, ...]


def _compile(terms: Iterable[str]) -> re.Pattern[str]:
    ordered = sorted({term for term in terms if term}, key=len, reverse=True)
    if not ordered:
        raise ValueError("A lexicon group needs at least one term")
    return re.compile("|".join(re.escape(term) for term in ordered))


#: Compiled patterns, built once per term group and once per domain.
_GROUP_PATTERNS: dict[str, re.Pattern[str]] = {}
_DOMAIN_PATTERNS: dict[str, re.Pattern[str]] = {}


def _group_pattern(group: TermGroup) -> re.Pattern[str]:
    pattern = _GROUP_PATTERNS.get(group.group_id)
    if pattern is None:
        pattern = _compile(group.terms)
        _GROUP_PATTERNS[group.group_id] = pattern
    return pattern


def _domain_pattern(lexicon: DomainLexicon) -> re.Pattern[str]:
    pattern = _DOMAIN_PATTERNS.get(lexicon.domain_id)
    if pattern is None:
        pattern = _compile(
            [
                term
                for group in (*lexicon.strong, *lexicon.weak)
                for term in group.terms
            ]
        )
        _DOMAIN_PATTERNS[lexicon.domain_id] = pattern
    return pattern


#: Negation cues that cancel a lexical match when they end immediately before it.
_NEGATION_CUES: tuple[str, ...] = (
    "没有",
    "没",
    "无",
    "不是",
    "不需要",
    "不用",
    "不要",
    "不想",
    "未",
    "不",
)
_NEGATION_WINDOW = 3


LEXICONS: tuple[DomainLexicon, ...] = (
    DomainLexicon(
        domain_id="D01",
        strong=(
            TermGroup(
                group_id="plan_product",
                tier="strong",
                terms=(
                    "套餐",
                    "流量包",
                    "语音包",
                    "定向流量",
                    "通用流量",
                    "副卡",
                    "亲情号",
                    "彩铃",
                    "宽带",
                    "包月",
                    "月租",
                    "不限量",
                    "流量",
                    "通话分钟",
                    "语音分钟",
                ),
            ),
            TermGroup(
                group_id="plan_content",
                tier="strong",
                terms=(
                    "包含多少",
                    "套餐内容",
                    "套餐资费",
                    "套餐价格",
                    "套餐费",
                    "套餐内",
                ),
            ),
        ),
        weak=(
            TermGroup(
                group_id="plan_generic",
                tier="weak",
                terms=("多少流量", "多少分钟", "包含", "内容", "价格"),
            ),
        ),
    ),
    DomainLexicon(
        domain_id="D02",
        strong=(
            TermGroup(
                group_id="promotion_offer",
                tier="strong",
                terms=(
                    "优惠",
                    "赠送",
                    "免费",
                    "折扣",
                    "减免",
                    "返费",
                    "返现",
                    "红包",
                    "优惠券",
                    "特惠",
                    "促销",
                    "礼品",
                    "积分",
                    "兑换",
                    "权益",
                    "体验",
                    "首月",
                    "合约期",
                    "活动",
                ),
            ),
            TermGroup(
                group_id="offer_lifecycle",
                tier="strong",
                terms=("有效期", "到期", "续订", "自动续费", "名额", "限时"),
            ),
        ),
        weak=(
            TermGroup(
                group_id="enrollment_action",
                tier="weak",
                terms=("参加", "办理", "订购", "开通", "领取", "报名"),
            ),
            TermGroup(
                group_id="offer_qualifier",
                tier="weak",
                terms=("专属", "福利", "条件", "限制", "资格"),
            ),
        ),
    ),
    DomainLexicon(
        domain_id="D03",
        strong=(
            TermGroup(
                group_id="billing_core",
                tier="strong",
                terms=(
                    "话费",
                    "余额",
                    "欠费",
                    "充值",
                    "扣费",
                    "账单",
                    "缴费",
                    "发票",
                    "月结",
                    "出账",
                    "欠款",
                    "消费明细",
                    "扣款",
                ),
            ),
            TermGroup(
                group_id="billing_charge",
                tier="strong",
                terms=("费用", "收费", "资费", "多收", "乱扣", "退费"),
            ),
        ),
        weak=(
            TermGroup(
                group_id="billing_amount",
                tier="weak",
                terms=("多少钱", "多少钱一个月", "预存", "赠费", "押金", "到账"),
            ),
        ),
    ),
    DomainLexicon(
        domain_id="D04",
        strong=(
            TermGroup(
                group_id="lifecycle_core",
                tier="strong",
                terms=(
                    "变更",
                    "退订",
                    "取消",
                    "升级",
                    "降级",
                    "停机",
                    "复机",
                    "销户",
                    "过户",
                    "解约",
                    "退网",
                    "转网",
                    "换套餐",
                    "改套餐",
                    "关闭",
                    "解绑",
                ),
            ),
            TermGroup(
                group_id="lifecycle_effect",
                tier="strong",
                terms=("次月生效", "立即生效", "生效时间", "恢复使用", "暂停服务"),
            ),
        ),
        weak=(
            TermGroup(
                group_id="lifecycle_status",
                tier="weak",
                terms=("状态", "停用", "启用", "开通", "办理", "合约"),
            ),
        ),
    ),
    DomainLexicon(
        domain_id="D05",
        strong=(
            TermGroup(
                group_id="identity_core",
                tier="strong",
                terms=(
                    "机主",
                    "实名",
                    "实名制",
                    "身份证",
                    "本人",
                    "非本人",
                    "授权",
                    "代办",
                    "验证码",
                    "身份验证",
                    "身份核验",
                    "证件",
                    "户主",
                ),
            ),
            TermGroup(
                group_id="ownership",
                tier="strong",
                terms=("号码归属", "使用人", "机主信息", "过户"),
            ),
        ),
        weak=(
            TermGroup(
                group_id="identity_soft",
                tier="weak",
                terms=("资料", "确认身份", "证明材料", "关系证明"),
            ),
        ),
    ),
    DomainLexicon(
        domain_id="D06",
        strong=(
            TermGroup(
                group_id="notification_core",
                tier="strong",
                terms=(
                    "短信",
                    "下发",
                    "回执",
                    "送达",
                    "确认短信",
                    "到账提醒",
                    "工单",
                    "派单",
                    "推送",
                ),
            ),
            TermGroup(
                group_id="fulfilment",
                tier="strong",
                terms=("上门安装", "配送", "物流", "装机", "开通通知"),
            ),
        ),
        weak=(
            TermGroup(
                group_id="notification_soft",
                tier="weak",
                terms=("通知", "提醒", "告知", "发送", "邮件"),
            ),
        ),
    ),
)

LEXICON_BY_DOMAIN: dict[str, DomainLexicon] = {
    lexicon.domain_id: lexicon for lexicon in LEXICONS
}


@dataclass(frozen=True)
class DomainScore:
    domain_id: str
    confidence: float
    strong_groups: tuple[str, ...]
    weak_groups: tuple[str, ...]
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "domain_id": self.domain_id,
            "confidence": self.confidence,
            "strong_groups": list(self.strong_groups),
            "weak_groups": list(self.weak_groups),
            "matched_terms": list(self.matched_terms),
        }


@dataclass
class ClassificationResult:
    """Explainable per-record outcome."""

    domains: list[DomainScore] = field(default_factory=list)
    eligible: bool = True
    reason: str = ""

    @property
    def domain_ids(self) -> list[str]:
        return [item.domain_id for item in self.domains]

    @property
    def is_ambiguous(self) -> bool:
        if len(self.domains) < 2:
            return False
        confidences = sorted(
            (item.confidence for item in self.domains), reverse=True
        )
        return confidences[0] - confidences[1] < AMBIGUITY_DELTA


def _is_negated(text: str, start: int) -> bool:
    window_start = max(0, start - _NEGATION_WINDOW)
    window = text[window_start:start]
    return any(window.endswith(cue) for cue in _NEGATION_CUES)


def _matches(group: TermGroup, text: str) -> list[str]:
    found: list[str] = []
    for match in _group_pattern(group).finditer(text):
        if _is_negated(text, match.start()):
            continue
        found.append(match.group(0))
    return found


def classify_text(
    text: str | None,
    *,
    min_length: int = MIN_TEXT_LENGTH,
) -> ClassificationResult:
    """Classify one record's masked text with no external model."""
    if text is None:
        return ClassificationResult(eligible=False, reason="no_text")
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ClassificationResult(eligible=False, reason="empty_text")
    if len(normalized) < min_length:
        return ClassificationResult(eligible=False, reason="text_too_short")

    scores: list[DomainScore] = []
    for lexicon in LEXICONS:
        if _domain_pattern(lexicon).search(normalized) is None:
            continue
        strong_hits: list[str] = []
        weak_hits: list[str] = []
        weak_matches = 0
        matched_terms: list[str] = []
        for group in lexicon.strong:
            found = _matches(group, normalized)
            if found:
                strong_hits.append(group.group_id)
                matched_terms.extend(found)
        for group in lexicon.weak:
            found = _matches(group, normalized)
            if found:
                weak_hits.append(group.group_id)
                weak_matches += len(found)
                matched_terms.extend(found)

        if not strong_hits and weak_matches < 2:
            continue

        score = min(len(strong_hits), 3) * STRONG_WEIGHT + min(
            weak_matches, 2
        ) * WEAK_WEIGHT
        confidence = round(min(0.95, 0.45 + 0.15 * score), 3)
        scores.append(
            DomainScore(
                domain_id=lexicon.domain_id,
                confidence=confidence,
                strong_groups=tuple(strong_hits),
                weak_groups=tuple(weak_hits),
                matched_terms=tuple(sorted(set(matched_terms))),
            )
        )

    scores.sort(key=lambda item: (-item.confidence, item.domain_id))
    return ClassificationResult(domains=scores, eligible=True)


class ClassificationReport(BaseModel):
    database_path: str
    assignment_version: str
    label_method: str
    review_status: str
    model_name: str
    engine_version: str = TAXONOMY_VERSION
    started_at: str
    completed_at: str
    eligible_records: int = 0
    skipped_records: int = 0
    preserved_reviewed_records: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    record_assignments: int = 0
    unit_assignments: int = 0
    ambiguous_records: int = 0
    unclassified_records: int = 0
    unclassified_units: int = 0
    records_by_domain: dict[str, int] = Field(default_factory=dict)
    units_by_domain: dict[str, int] = Field(default_factory=dict)
    multi_label_records: int = 0
    max_labels_per_record: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect_writable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def classify_corpus(
    database_path: str | Path | None = None,
    *,
    replace: bool = True,
    batch_size: int = 5000,
) -> ClassificationReport:
    """Run the rule-based classifier and persist candidate assignments."""
    path = resolve_database_path(database_path)
    if not path.is_file():
        raise ClassificationError(f"Corpus database not found: {path}")

    started_at = _utc_now()
    connection = _connect_writable(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if replace:
            connection.execute(
                """
                DELETE FROM domain_assignments
                 WHERE assignment_version = ? AND label_method = ?
                """,
                (ASSIGNMENT_VERSION, LABEL_METHOD),
            )

        report = ClassificationReport(
            database_path=str(path),
            assignment_version=ASSIGNMENT_VERSION,
            label_method=LABEL_METHOD,
            review_status=REVIEW_STATUS,
            model_name=MODEL_NAME,
            started_at=started_at,
            completed_at="",
        )
        protected = _protected_targets(connection)
        _classify_records(
            connection, report, batch_size=batch_size, protected=protected
        )
        _rollup_units(connection, report, protected=protected)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    report.completed_at = _utc_now()
    return report


def _protected_targets(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    """Targets already carrying a human decision must never be overwritten."""
    rows = connection.execute(
        """
        SELECT target_type, target_id FROM domain_assignments
         WHERE review_status <> 'CANDIDATE'
        """
    ).fetchall()
    return {(row["target_type"], row["target_id"]) for row in rows}


def _iter_eligible_rows(
    connection: sqlite3.Connection, batch_size: int
) -> Iterator[sqlite3.Row]:
    cursor = connection.execute(
        """
        SELECT transcript_id, seq, payload_type, text_masked, asr_flags_json
          FROM transcript_records
         WHERE payload_type = 'SPEECH'
         ORDER BY transcript_id, seq
        """
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield from rows


def _classify_records(
    connection: sqlite3.Connection,
    report: ClassificationReport,
    *,
    batch_size: int,
    protected: set[tuple[str, str]],
) -> None:
    rows_buffer: list[tuple[object, ...]] = []
    record_counts: dict[str, int] = {}
    created_at = _utc_now()

    for row in _iter_eligible_rows(connection, batch_size):
        target_id = f"{row['transcript_id']}:{row['seq']}"
        if ("RECORD", target_id) in protected:
            report.preserved_reviewed_records += 1
            continue
        result = classify_text(row["text_masked"])
        if not result.eligible:
            report.skipped_records += 1
            report.skipped_reasons[result.reason] = (
                report.skipped_reasons.get(result.reason, 0) + 1
            )
            continue

        report.eligible_records += 1
        if not result.domains:
            report.unclassified_records += 1
            continue
        if len(result.domains) > 1:
            report.multi_label_records += 1
        report.max_labels_per_record = max(
            report.max_labels_per_record, len(result.domains)
        )
        if result.is_ambiguous:
            report.ambiguous_records += 1

        primary = result.domains[0].domain_id
        for score in result.domains:
            rows_buffer.append(
                (
                    "RECORD",
                    target_id,
                    score.domain_id,
                    LABEL_METHOD,
                    score.confidence,
                    REVIEW_STATUS,
                    1 if score.domain_id == primary else 0,
                    ASSIGNMENT_VERSION,
                    MODEL_NAME,
                    created_at,
                )
            )
            record_counts[score.domain_id] = record_counts.get(score.domain_id, 0) + 1
        report.record_assignments += len(result.domains)

        if len(rows_buffer) >= batch_size:
            _flush_assignments(connection, rows_buffer)
            rows_buffer.clear()

    _flush_assignments(connection, rows_buffer)
    report.records_by_domain = dict(sorted(record_counts.items()))


def _flush_assignments(
    connection: sqlite3.Connection, rows: list[tuple[object, ...]]
) -> None:
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO domain_assignments (
            target_type, target_id, domain_id, label_method, confidence,
            review_status, is_primary, assignment_version, reviewer_or_model,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _rollup_units(
    connection: sqlite3.Connection,
    report: ClassificationReport,
    *,
    protected: set[tuple[str, str]],
) -> None:
    unit_rows = connection.execute(
        """
        SELECT target_id, domain_id, COUNT(*) AS support,
               MAX(confidence) AS best
          FROM domain_assignments
         WHERE target_type = 'RECORD' AND assignment_version = ?
           AND label_method = ?
         GROUP BY target_id, domain_id
        """,
        (ASSIGNMENT_VERSION, LABEL_METHOD),
    ).fetchall()

    per_unit: dict[str, dict[str, tuple[int, float]]] = {}
    for row in unit_rows:
        transcript_id = str(row["target_id"]).rsplit(":", 1)[0]
        domain_id = row["domain_id"]
        support = int(row["support"])
        best = float(row["best"])
        domains_for_unit = per_unit.setdefault(transcript_id, {})
        previous = domains_for_unit.get(domain_id)
        if previous is None:
            domains_for_unit[domain_id] = (support, best)
        else:
            domains_for_unit[domain_id] = (
                previous[0] + support,
                max(previous[1], best),
            )

    created_at = _utc_now()
    insert_rows: list[tuple[object, ...]] = []
    units_by_domain: dict[str, int] = {}
    for transcript_id, domains_for_unit in sorted(per_unit.items()):
        if ("TRANSCRIPT_UNIT", transcript_id) in protected:
            continue
        entries = sorted(
            domains_for_unit.items(),
            key=lambda item: (-item[1][0], -item[1][1], item[0]),
        )
        for position, (domain_id, (support, _best)) in enumerate(entries):
            confidence = round(min(0.95, 0.5 + 0.1 * min(support, 4)), 3)
            insert_rows.append(
                (
                    "TRANSCRIPT_UNIT",
                    transcript_id,
                    domain_id,
                    LABEL_METHOD,
                    confidence,
                    REVIEW_STATUS,
                    1 if position == 0 else 0,
                    ASSIGNMENT_VERSION,
                    MODEL_NAME,
                    created_at,
                )
            )
            units_by_domain[domain_id] = units_by_domain.get(domain_id, 0) + 1

    connection.executemany(
        """
        INSERT INTO domain_assignments (
            target_type, target_id, domain_id, label_method, confidence,
            review_status, is_primary, assignment_version, reviewer_or_model,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_rows,
    )
    report.unit_assignments = len(insert_rows)
    report.units_by_domain = dict(sorted(units_by_domain.items()))
    total_units = connection.execute(
        "SELECT COUNT(*) FROM transcript_units"
    ).fetchone()[0]
    report.unclassified_units = int(total_units) - len(per_unit)


def explain_record(
    transcript_id: str,
    seq: int,
    *,
    database_path: str | Path | None = None,
) -> dict[str, object]:
    """Recompute and explain the classification of one record.

    Returns matched groups and terms plus negation-filtered evidence, but never
    the transcript text itself.
    """
    path = resolve_database_path(database_path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT transcript_id, seq, payload_type, text_masked
              FROM transcript_records
             WHERE transcript_id = ? AND seq = ?
            """,
            (transcript_id, seq),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise KeyError(f"Record not found: {transcript_id}:{seq}")

    result = classify_text(row["text_masked"])
    return {
        "transcript_id": transcript_id,
        "seq": seq,
        "payload_type": row["payload_type"],
        "eligible": result.eligible,
        "reason": result.reason,
        "ambiguous": result.is_ambiguous,
        "domains": [score.to_dict() for score in result.domains],
    }


def lexicon_manifest() -> dict[str, object]:
    """Return the full rule set so classifications can be reproduced exactly."""
    return {
        "engine_version": TAXONOMY_VERSION,
        "assignment_version": ASSIGNMENT_VERSION,
        "model_name": MODEL_NAME,
        "min_text_length": MIN_TEXT_LENGTH,
        "strong_weight": STRONG_WEIGHT,
        "weak_weight": WEAK_WEIGHT,
        "assign_threshold": ASSIGN_THRESHOLD,
        "ambiguity_delta": AMBIGUITY_DELTA,
        "negation_cues": list(_NEGATION_CUES),
        "negation_window": _NEGATION_WINDOW,
        "domains": [
            {
                "domain_id": lexicon.domain_id,
                "strong": [
                    {"group_id": group.group_id, "terms": list(group.terms)}
                    for group in lexicon.strong
                ],
                "weak": [
                    {"group_id": group.group_id, "terms": list(group.terms)}
                    for group in lexicon.weak
                ],
            }
            for lexicon in LEXICONS
        ],
    }


def write_lexicon_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(lexicon_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
