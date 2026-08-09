from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class Family(str, Enum):
    ACT = "act"
    CONTENT = "content"
    UNCLASSIFIED = "unclassified"


class ViolationClass(str, Enum):
    NO_APPROVAL_TOKEN = "act:no_approval_token"  # allowlist-secret: enum value, not a credential
    JURISDICTION_NOT_CONSENTED = "act:jurisdiction_not_consented"
    TOOL_OUTSIDE_GRANT = "act:tool_outside_grant"
    FIGURE_NOT_IN_RECORD = "act:figure_not_in_record"
    SEND_CAP_EXCEEDED = "act:send_cap_exceeded"
    ADVISES_ON_MERITS = "content:advises_on_merits"
    NEGOTIATES_TERMS = "content:negotiates_terms"
    FORWARD_LOOKING_RETURN = "content:forward_looking_return"
    OTHER = "other"

    @property
    def family(self) -> Family:
        prefix = self.value.split(":", 1)[0]
        if prefix == "act":
            return Family.ACT
        if prefix == "content":
            return Family.CONTENT
        return Family.UNCLASSIFIED


@dataclass(frozen=True)
class Message:
    role: str
    body: str


@dataclass(frozen=True)
class Record:
    fields: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        return self.fields.get(name)


@dataclass(frozen=True)
class Draft:
    thread: tuple[Message, ...]
    body: str
    cited_fields: tuple[str, ...]
    recipient_jurisdiction: str
    recipient_domain: str
    tool_name: str | None


@dataclass(frozen=True)
class Finding:
    violation_class: ViolationClass
    detail: str | None
    span: str | None

    def __post_init__(self) -> None:
        if self.violation_class is ViolationClass.OTHER and not self.detail:
            raise ValueError("a finding on OTHER must carry detail text")


class Disposition(str, Enum):
    ALLOW = "allow"
    REDIRECT_REFINABLE = "redirect_refinable"
    REDIRECT_FUTILE = "redirect_futile"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    findings: tuple[Finding, ...]
    disposition: Disposition
    #: Why no detector answer was available, when that is why this denial exists -- `None` on every
    #: denial a detector actually judged. A denial for downtime and a denial for a judgement are the
    #: same shape in every other field, so without this the two are told apart only by prose inside
    #: `Finding.detail`, which the escalation payload does not carry. Defaulted because `decide` sets
    #: it on one branch and the value is passed in: `policy/` reads no clock and calls no checker.
    outage: str | None = None
