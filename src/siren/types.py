"""Typed result objects and the webhook event-type catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Literal, Optional, TypedDict, Union


class WebhookEventType(str, Enum):
    """Every subscribable webhook event type. ``ALL`` (``"*"``) subscribes to all.

    Matches the catalog Siren serves at ``GET /webhooks/events``. Siren
    accepts any string in a subscription's ``events``, so a name it does not
    send is stored and never delivered.
    """

    ALL = "*"
    ALLOCATION_COMPLETED = "allocation.completed"
    COLLABORATOR_CREATED = "collaborator.created"
    COLLABORATOR_DEACTIVATED = "collaborator.deactivated"
    COLLABORATOR_REGISTERED = "collaborator.registered"
    COLLABORATOR_UPDATED = "collaborator.updated"
    CONVERSION_APPROVED = "conversion.approved"
    CONVERSION_CREATED = "conversion.created"
    CONVERSION_REJECTED = "conversion.rejected"
    CONVERSION_RENEWED = "conversion.renewed"
    COUPON_APPLIED = "coupon.applied"
    CREDIT_ISSUED = "credit.issued"
    CREDIT_REDEEMED = "credit.redeemed"
    CURRENCY_CREATED = "currency.created"
    CURRENCY_DELETED = "currency.deleted"
    DISTRIBUTION_COMPLETED = "distribution.completed"
    ENGAGEMENT_AWARDED = "engagement.awarded"
    ENGAGEMENT_COMPLETED = "engagement.completed"
    ENGAGEMENT_CREATED = "engagement.created"
    FULFILLMENT_CREATED = "fulfillment.created"
    FULFILLMENT_UPDATED = "fulfillment.updated"
    LEAD_CREATED = "lead.created"
    METRICS_UPDATED = "metrics.updated"
    OBLIGATION_COMPLETED = "obligation.completed"
    OBLIGATION_CREATED = "obligation.created"
    OPPORTUNITY_CREATED = "opportunity.created"
    OPPORTUNITY_INVALIDATED = "opportunity.invalidated"
    PAYOUT_CREATED = "payout.created"
    PAYOUT_PAID = "payout.paid"
    PROGRAM_CREATED = "program.created"
    REFUND_CREATED = "refund.created"
    RENEWAL_CREATED = "renewal.created"
    SALE_CREATED = "sale.created"
    TRANSACTION_COMPLETED = "transaction.completed"
    TRANSACTION_CREATED = "transaction.created"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EventResult:
    """Result of an event ingestion call.

    ``opportunity_id`` is read from the ``X-Siren-OID`` response header, or
    from the body's ``opportunityId`` when the header is missing. It is
    ``None`` when the server did not associate the event with an opportunity.

    ``http_status`` is the response status and ``body`` the JSON object Siren
    answered with, or ``{}`` when the body was empty. A sale answers
    ``{status, created, transactionId, conversions, message}``, with a
    ``reason`` when it earned nothing. A site visit answers ``{status,
    attributed, opportunityId, message}``. While an organization's processing
    is paused, Siren queues the event and answers ``202`` with
    ``{status: "queued", queued: true, message}``. A queued event is recorded
    later, so a refund for an unknown sale is not refused at that point.
    """

    opportunity_id: Optional[int] = None
    http_status: Optional[int] = None
    body: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Optional[str]:
        """The outcome Siren reported, such as ``created``, ``duplicate``,
        ``unattributed``, ``attributed``, ``not_attributed`` or ``queued``."""
        value = self.body.get("status")
        return value if isinstance(value, str) else None

    @property
    def queued(self) -> bool:
        """Whether Siren queued the event for later instead of recording it now."""
        return self.http_status == 202 or self.body.get("queued") is True


@dataclass(frozen=True)
class BrowserToken:
    """A browser token for one custom event, from ``events.browser_token()``.

    Hand all three values to the page. The page posts the event's data as
    JSON to ``url`` with ``?access_token=`` set to ``token``, from a domain
    the event allows. ``expires_in`` is the seconds the token has left.
    """

    token: str
    expires_in: int
    url: str

    def to_dict(self) -> Dict[str, Any]:
        """The JSON the browser token handler answers with: ``{token, expiresIn, url}``."""
        return {"token": self.token, "expiresIn": self.expires_in, "url": self.url}


@dataclass(frozen=True)
class WebhookEvent:
    """A verified, parsed webhook delivery.

    ``type`` is the event type, such as ``conversion.approved``. ``data`` is
    the event's payload. ``delivery_id`` is the event id (``evt_...``), which
    stays the same when Siren retries a delivery, so use it to drop repeats.
    ``raw`` holds the whole parsed body.
    """

    type: str
    data: Dict[str, Any]
    delivery_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class AppItemKind(str, Enum):
    """The kinds of feature an app can add, as Siren stores an item's
    ``kind`` and sends it in an app request's ``item["kind"]``.

    A metric calculation (``METRIC_CALCULATION``) answers
    ``{"outcome": "scores", "results": [{"collaboratorId": 7, "score": 10}]}``.
    Siren takes at most 50 results, each collaborator once, each in the
    organization and not in the trash, and each score a whole number of 0 or
    more. An empty list scores nobody. A metric filter (``METRIC_FILTER``)
    answers ``{"outcome": "allow"}``, or ``{"outcome": "suppress", "reason":
    "..."}`` with a plain text reason of 1 to 200 characters.

    An incentive resolver (``INCENTIVE_RESOLVER``) splits a program's reward
    among the collaborators a sale converted. It answers
    ``{"outcome": "rewards", "units": "USD", "rewards": {"1042": 900, "1043": None}}``,
    with the pool's units and every conversion id of the question, each a
    whole number of 0 or more or ``None``. The rewards wait for the answer,
    at most 24 hours from when the sale's reward began to wait.

    An incentive type (``INCENTIVE_TYPE``) decides which engagements one
    sale, lead or renewal converts on a program, and the pool the program's
    resolver divides. It answers ``{"outcome": "award", "units": "USD",
    "pool": 1500, "engagementIds": [880, 881]}``, or ``{"outcome": "none"}``
    to award nothing. The award waits for the answer, 24 hours from the first
    ask at most.

    An engagement calculation (``ENGAGEMENT_CALCULATION``) answers who earns
    an engagement and its score, ``{"outcome": "engagements", "results":
    [{"collaboratorId": 7, "score": 10}]}``, with the same limits as a metric
    calculation. An empty list makes no engagement. An engagement filter
    (``ENGAGEMENT_FILTER``) answers ``{"outcome": "allow"}``, or
    ``{"outcome": "suppress", "reason": "..."}`` with a plain text reason of
    1 to 200 characters, which Siren notes on the opportunity. Siren makes no
    engagement until the answer, at most 24 hours from when Siren asked. When
    a sale waits on the answer, the ask ends with that sale's wait, 24 hours
    after the sale reached Siren.

    A program group structure (``PROGRAM_GROUP_SORTER``) orders a program
    group's programs for one sale or lead. It answers
    ``{"outcome": "sorted", "programIds": [9, 12]}``, every program id of the
    question exactly once. The first program that converts wins the group,
    which waits for the answer, 24 hours from the sale at most.

    A transaction filter (``TRANSACTION_FILTER``) says which of a sale's own
    lines count, and Siren's catalog calls it a transaction compiler. Siren
    asks it once per sale for each program or distributor that picks it. It
    answers ``{"outcome": "filtered", "include": [5501, 5502]}``, the ids of
    the question's lines to include, each at most once. An empty list
    includes nothing, and Siren refuses an id the question lacks.

    A collaborator group structure (``COLLABORATOR_GROUP_STRUCTURE``) gives a
    collaborator group's shape. Siren asks it once per cascade trigger on the
    group, for a program or a distributor. It answers a chain,
    ``{"outcome": "structure", "shape": "chain", "order": [63, 57, 42]}``,
    with every member's collaborator id once, top first, or a tree,
    ``{"outcome": "structure", "shape": "tree", "parents": {"42": 57, "57": 63, "63": None}}``,
    with every member's id once as a key, each mapped to another member's id
    or ``None`` for a top member, and no cycle.
    """

    POST_CONVERSION_ACTION = "postConversionAction"
    POST_ALLOCATION_ACTION = "postAllocationAction"
    PAYOUT_METHOD = "payoutMethod"
    DISTRIBUTION_RESOLVER = "distributionResolver"
    CUSTOM_EVENT = "customEvent"
    METRIC_CALCULATION = "metricCalculation"
    METRIC_FILTER = "metricFilter"
    INCENTIVE_RESOLVER = "incentiveResolver"
    INCENTIVE_TYPE = "incentiveType"
    ENGAGEMENT_CALCULATION = "engagementCalculation"
    ENGAGEMENT_FILTER = "engagementFilter"
    PROGRAM_GROUP_SORTER = "programGroupSorter"
    TRANSACTION_FILTER = "transactionFilter"
    COLLABORATOR_GROUP_STRUCTURE = "collaboratorGroupStructure"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class AppRequest:
    """A request Siren signed and sent an app, as ``apps.verify_request()``
    returns it once the signature, the time and the envelope check out.

    Siren may deliver one request more than once, so drop a repeat by ``id``,
    the request key, the same on every send. ``mode`` is ``tell``, which
    waits for no answer, or ``ask``, answered with ``apps.requests.answer()``
    by ``answer_by``. ``item`` holds the feature's ``kind``, ``key`` and
    ``app:{n}`` ``id``. ``data`` is the question. ``timestamp`` is the Unix
    time Siren signed the send at. ``delivery_id`` is the
    ``X-Siren-Delivery`` header, one per send. ``raw`` holds the whole body.
    """

    version: int
    id: str
    type: str
    mode: str
    app: str
    item: Dict[str, Any]
    created_at: str
    answer_by: Optional[str]
    answer_url: Optional[str]
    data: Dict[str, Any]
    timestamp: int
    delivery_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_ask(self) -> bool:
        """Whether Siren waits for an answer to this request."""
        return self.mode == "ask"


@dataclass(frozen=True)
class AppAnswerReceipt:
    """Siren's receipt for an app's answer. Siren took it and applies it on
    its own. ``duplicate`` is true when Siren already held this same answer,
    so it changed nothing."""

    status: str
    duplicate: bool


def _created_at(payload: Dict[str, Any]) -> Optional[str]:
    """Siren names a record's creation time ``dateCreated``."""
    value = payload.get("dateCreated")
    return value if value is not None else payload.get("createdAt")


@dataclass(frozen=True)
class WebhookSubscription:
    """A webhook subscription.

    ``signing_secret`` is populated only on the response from
    ``webhooks.subscriptions.create`` — it is returned exactly once and cannot
    be retrieved later. ``raw`` holds the untouched API payload.
    """

    id: Optional[int] = None
    target_url: Optional[str] = None
    events: List[str] = field(default_factory=list)
    description: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    signing_secret: Optional[str] = None
    property_id: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "WebhookSubscription":
        return cls(
            id=payload.get("id"),
            target_url=payload.get("targetUrl"),
            events=list(payload.get("events") or []),
            description=payload.get("description"),
            status=payload.get("status"),
            created_at=_created_at(payload),
            signing_secret=payload.get("signingSecret"),
            property_id=payload.get("propertyId"),
            raw=payload,
        )


@dataclass(frozen=True)
class ApiKey:
    """An API key record.

    ``raw_key`` (the plaintext ``sk_live_...`` credential) is populated only on
    the response from ``api_keys.create`` — it is returned exactly once and
    cannot be retrieved later. ``raw`` holds the untouched API payload.
    """

    id: Optional[int] = None
    key_prefix: Optional[str] = None
    label: Optional[str] = None
    status: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    raw_key: Optional[str] = None
    property_id: Optional[int] = None
    revoked_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ApiKey":
        return cls(
            id=payload.get("id"),
            key_prefix=payload.get("keyPrefix"),
            label=payload.get("label"),
            status=payload.get("status"),
            scopes=list(payload.get("scopes") or []),
            created_at=_created_at(payload),
            expires_at=payload.get("expiresAt"),
            last_used_at=payload.get("lastUsedAt"),
            raw_key=payload.get("rawKey"),
            property_id=payload.get("propertyId"),
            revoked_at=payload.get("revokedAt"),
            raw=payload,
        )


@dataclass(frozen=True)
class ListPage:
    """One page of records from a reconciliation reader.

    Siren answers a list with ``{items, total, page, perPage, totalPages}``.
    ``data`` holds ``items``, and the other fields come from the same body.
    When a body is a bare array instead, ``page`` and ``per_page`` echo the
    request and ``total`` comes from the ``x-siren-estimated-count`` header.
    Iterating (or indexing) a ``ListPage`` iterates its ``data``.
    """

    data: List[Dict[str, Any]] = field(default_factory=list)
    page: Optional[int] = None
    per_page: Optional[int] = None
    total: Optional[int] = None
    total_pages: Optional[int] = None

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.data[index]


# What Siren asks each kind of app feature, and what it takes back. Each is
# the request's ``data``, or the answer an ask's function returns, with
# Siren's own key names. Siren's fixture
# tests/Fixtures/apps-feature-contracts/requests.json holds an example of each.


class AppCurrency(TypedDict):
    """A currency as Siren's API sends one. A shown amount is
    ``value / 10 ** scale``, and the smallest part a person sees is
    ``10 ** (scale - decimalPlaces)`` in the stored form."""

    id: str
    symbol: str
    position: str
    name: str
    scale: int
    decimalPlaces: int
    type: str


class AppAmount(TypedDict):
    """An amount in the stored integer form, with its currency."""

    value: int
    units: str
    currency: AppCurrency


class AppDistributionPool(AppAmount):
    """What a distribution question offers. ``value`` is ``total`` less
    ``paid``, what the distribution's collaborators already hold from it."""

    total: int
    paid: int


class AppDistributionMetric(TypedDict):
    metricId: int
    collaboratorId: int
    score: float
    triggerCount: int


class AppDistributionQuestion(TypedDict):
    """``distributionResolver`` (an ask): split ``pool`` among ``metrics``."""

    distribution: Dict[str, Any]
    distributor: Dict[str, Any]
    pool: AppDistributionPool
    metrics: List[AppDistributionMetric]


class AppDistributionAnswer(TypedDict):
    """One share per metric id, 0 or more, each a whole step of the
    currency, adding up to at most ``pool.value``. ``outcome`` is
    ``allocated`` and ``units`` the pool's units."""

    outcome: str
    units: str
    shares: Dict[str, int]


class AppRewardConversion(TypedDict):
    conversionId: int
    engagementId: int
    collaboratorId: int
    collaboratorStatus: str
    score: float
    engagementCreatedDate: str


class AppRewardQuestion(TypedDict):
    """``incentiveResolver`` (an ask): reward the ``conversions`` one award
    made from ``pool``. ``reason`` is ``sale``, or ``refund`` when a partial
    refund asks again. ``transaction`` is ``None`` for a lead."""

    reason: str
    program: Dict[str, Any]
    conversionType: str
    transaction: Optional[Dict[str, Any]]
    pool: AppAmount
    conversions: List[AppRewardConversion]


class AppRewardAnswer(TypedDict):
    """A key for every conversion id of the question, ``None`` for one the
    app did not choose or a whole number of 0 or more. ``outcome`` is
    ``rewards`` and ``units`` the pool's units."""

    outcome: str
    units: str
    rewards: Dict[str, Optional[int]]


class AppIncentiveTypeEngagement(TypedDict):
    engagementId: int
    collaboratorId: int
    score: float
    createdDate: str


class _AppIncentiveTypeQuestionFields(TypedDict):
    reason: str
    program: Dict[str, Any]
    conversionType: str
    opportunity: Dict[str, int]
    transaction: Optional[Dict[str, Any]]
    binding: Optional[Dict[str, str]]
    engagements: List[AppIncentiveTypeEngagement]


class AppIncentiveTypeQuestion(_AppIncentiveTypeQuestionFields, total=False):
    """``incentiveType`` (an ask): which engagements one sale, lead or
    renewal converts on the ``program``, and the pool its resolver divides.
    ``reason`` is ``sale`` until the award is made, and ``refund`` when a
    partial refund asks again about an award already made, which then lists
    the award's ``conversions``. ``transaction`` is ``None`` for a lead,
    which names its ``binding`` instead. Its ``total`` and each line's
    ``value`` are stored whole numbers."""

    conversions: List[Dict[str, int]]


class AppIncentiveTypeAwardAnswer(TypedDict):
    """The engagements of the question that convert, each once, and the
    ``pool``, a whole number of 0 or more in the program's ``units``.
    ``outcome`` is ``award``."""

    outcome: str
    units: str
    pool: int
    engagementIds: List[int]


class AppIncentiveTypeNoneAnswer(TypedDict):
    """Award nothing on the program. ``outcome`` is ``none``."""

    outcome: str


AppIncentiveTypeAnswer = Union[AppIncentiveTypeAwardAnswer, AppIncentiveTypeNoneAnswer]
"""The answer to an ``incentiveType`` ask."""


class AppEngagementCause(TypedDict):
    """The trigger behind an engagement ask. ``id`` is the trigger's own
    random id, ``event`` the engagement type that fired, and ``occurredAt``
    an ISO 8601 time that dates the engagements the answer makes."""

    id: str
    event: str
    occurredAt: str


class AppEngagementCalculationQuestion(TypedDict):
    """``engagementCalculation`` (an ask): who earns an engagement, and its
    score, when a program's engagement type triggers. ``args`` holds the
    settings a person gave the engagement type, as text by name.
    ``opportunity["customerId"]`` is the customer Siren matched, or ``None``."""

    program: Dict[str, Any]
    engagementType: str
    programEngagementTypeId: int
    args: Dict[str, str]
    opportunity: Dict[str, Any]
    triggeringCollaborator: Dict[str, Any]
    cause: AppEngagementCause


class AppEngagementCalculationAnswer(TypedDict):
    """Who earns an engagement and its score, at most 50 results, each
    collaborator once and each score a whole number of 0 or more.
    ``outcome`` is ``engagements``."""

    outcome: str
    results: List[Dict[str, int]]


class AppEngagementFilterQuestion(TypedDict):
    """``engagementFilter`` (an ask): whether an engagement may be made for
    ``collaborator`` on ``program``."""

    program: Dict[str, Any]
    collaborator: Dict[str, Any]
    opportunity: Dict[str, Any]
    engagementType: str
    cause: AppEngagementCause


class AppEngagementFilterAnswer(TypedDict, total=False):
    """``outcome`` is ``allow`` or ``suppress``. A suppress gives a plain
    text ``reason`` of 1 to 200 characters."""

    outcome: str
    reason: str


class AppGroupOrderEngagement(TypedDict):
    engagementId: int
    collaboratorId: int
    score: float
    createdDate: str


class AppGroupOrderProgram(TypedDict):
    id: int
    name: str
    engagements: List[AppGroupOrderEngagement]


class AppGroupOrderQuestion(TypedDict):
    """``programGroupSorter`` (an ask): order the group's ``programs`` for
    one sale or lead. ``programs`` holds each program of the group that takes
    the conversion once, in the group's order, with its active engagements on
    the opportunity, oldest first. ``transaction`` is ``None`` for a lead, and
    its ``total`` is a stored whole number."""

    group: Dict[str, Any]
    conversionType: str
    opportunity: Dict[str, Any]
    transaction: Optional[Dict[str, Any]]
    programs: List[AppGroupOrderProgram]


class AppGroupOrderAnswer(TypedDict):
    """Every program id of the question exactly once, in the order Siren
    tries them. ``outcome`` is ``sorted``."""

    outcome: str
    programIds: List[int]


class AppTransaction(TypedDict):
    """The sale a transaction filter question is about: its ``id``, its
    ``units`` and its currency, the same currency an incentive resolver
    question carries."""

    id: int
    units: str
    currency: AppCurrency


class AppTransactionLine(TypedDict):
    """One of a sale's own lines. ``value`` is the line's unit value, a
    stored whole number in the transaction's units, and ``quantity`` a whole
    number. ``productId`` is ``None`` when the line names no product."""

    id: int
    type: Literal["lineItem", "discount", "fee", "shipping", "tax"]
    name: str
    quantity: int
    value: int
    productId: Optional[str]


# ``for`` is a Python keyword, so the question uses the functional form.
AppTransactionFilterQuestion = TypedDict(
    "AppTransactionFilterQuestion",
    {
        "for": Dict[str, Any],
        "transaction": AppTransaction,
        "lines": List[AppTransactionLine],
        "distribution": Optional[Dict[str, Any]],
        "refundDistribution": Optional[Dict[str, Any]],
    },
)
AppTransactionFilterQuestion.__doc__ = """``transactionFilter`` (an ask): say which
of the sale's own ``lines`` count, once per sale for each program or
distributor that picks the filter. ``for`` holds ``type``, ``program`` or
``distributor``, its ``id`` and its ``name``. The subject id is
``{opportunityId}:{programId}:t{transactionId}`` for a program's lines and
``{distributorId}:t{transactionId}`` for a distributor's. ``distribution`` is
``None`` for a program, and for a distributor ``{"id": ...}``, the period the
sale's value lands in. ``refundDistribution`` is ``None``, or the later period
a refund came in while the ask was open. A refund reuses the stored answer,
so Siren never sends refund lines."""


class AppTransactionFilterAnswer(TypedDict):
    """The ids of the question's lines that count, each at most once. An
    empty list includes nothing. A line counts when any filter the record
    picks includes it. Siren refuses an id the question lacks with 422
    ``app_answer_invalid``. ``outcome`` is ``filtered``."""

    outcome: Literal["filtered"]
    include: List[int]


class AppCollaboratorGroupMember(TypedDict):
    """One member of a collaborator group in a group structure question.
    ``status`` is the collaborator's status at the trigger, such as
    ``active`` or ``inactive``, which Siren credits the member by."""

    collaboratorId: int
    name: str
    status: str
    metadata: Dict[str, Any]


# ``for`` is a Python keyword, so the question uses the functional form.
AppCollaboratorGroupStructureQuestion = TypedDict(
    "AppCollaboratorGroupStructureQuestion",
    {
        "group": Dict[str, Any],
        "members": List[AppCollaboratorGroupMember],
        "for": Dict[str, Any],
        "triggeringCollaborator": Dict[str, Any],
        "cause": Dict[str, Any],
    },
)
AppCollaboratorGroupStructureQuestion.__doc__ = """``collaboratorGroupStructure`` (an ask): give
the ``group``'s shape among its ``members``, once per cascade trigger on the
group. ``for`` holds ``type``, ``program`` (an engagement cascade) or
``distributor`` (a metric cascade), and ``id``. ``cause["id"]`` is 16 hex
characters, the trigger's own random id. The question also carries the keys
of the trigger it interrupts, which differ by side and are not listed here.
:class:`AppCollaboratorGroupStructureDistributorQuestion` lists a
distributor's and :class:`AppCollaboratorGroupStructureProgramQuestion` a
program's."""


class AppCollaboratorGroupStructureDistributorQuestion(AppCollaboratorGroupStructureQuestion):
    """A group structure question for a distributor's metric cascade. It
    carries a metric calculation's keys beside the group's, and its
    ``cause`` holds ``id``, ``type`` and ``causeId`` as a metric's does.
    ``opportunityId``, ``points`` and ``sale`` may be ``None``."""

    distributor: Dict[str, Any]
    distribution: Dict[str, Any]
    metricType: str
    distributorMetricTypeId: int
    opportunityId: Optional[int]
    points: Optional[int]
    sale: Optional[Dict[str, Any]]


class AppCollaboratorGroupStructureProgramQuestion(AppCollaboratorGroupStructureQuestion):
    """A group structure question for a program's engagement cascade. It
    carries an engagement calculation's keys beside the group's, and its
    ``cause`` is an engagement's. ``args`` holds the settings a person gave
    the engagement type, as text by name, or an empty dict.
    ``opportunity["customerId"]`` may be ``None``."""

    program: Dict[str, Any]
    engagementType: str
    programEngagementTypeId: int
    args: Dict[str, str]
    opportunity: Dict[str, Any]
    cause: AppEngagementCause


class AppCollaboratorGroupChain(TypedDict):
    """A chain answer: ``order`` lists every member's ``collaboratorId`` of
    the question once, top of the chain first. Siren reads it as its
    built-in linear chain. ``outcome`` is ``structure``."""

    outcome: Literal["structure"]
    shape: Literal["chain"]
    order: List[int]


class AppCollaboratorGroupTree(TypedDict):
    """A tree answer: ``parents`` has every member's ``collaboratorId`` of
    the question as text once, each mapped to another member's id or
    ``None`` for a top member, with no cycle. Siren reads it as its parent
    and child structure. ``outcome`` is ``structure``."""

    outcome: Literal["structure"]
    shape: Literal["tree"]
    parents: Dict[str, Optional[int]]


AppCollaboratorGroupStructureAnswer = Union[AppCollaboratorGroupChain, AppCollaboratorGroupTree]
"""The answer to a ``collaboratorGroupStructure`` ask, a chain or a tree.
Siren refuses one that breaks the rules with 422 ``app_answer_invalid``."""


class AppConversionActionData(TypedDict):
    """``postConversionAction`` (a tell): the conversion, its reward and
    its program."""

    conversion: Dict[str, Any]
    reward: AppAmount
    program: Dict[str, Any]


class AppAllocationActionData(TypedDict):
    """``postAllocationAction`` (a tell): the allocation and its
    distributor."""

    allocation: Dict[str, Any]
    distributor: Dict[str, Any]
