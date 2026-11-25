"""Typed result objects and the webhook event-type catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generic, Iterator, List, Literal, Mapping, Optional, Tuple, TypedDict, TypeVar, Union


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


class FeatureKind(str, Enum):
    """The kinds of feature an app can add, as Siren stores a feature's
    ``kind`` and sends it in an app request's ``feature["kind"]``. Each
    member's docs give the name Siren Cloud's screens show, from the kind
    snapshot the SDK ships (``app_kinds.json``), the term Siren's code uses
    where it differs, and whether Siren asks or tells.

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

    # Generated from app_kinds.json by scripts/feature_kinds.py. Do not edit.
    POST_CONVERSION_ACTION = "postConversionAction"
    """Post-conversion action. Siren tells the app when a conversion is
    approved in a program that picks this action. A tell: Siren lets the app
    know.
    """

    POST_ALLOCATION_ACTION = "postAllocationAction"
    """Post-allocation action. Siren tells the app when an allocation completes
    for a distributor that picks this action. A tell: Siren lets the app
    know.
    """

    PAYOUT_METHOD = "payoutMethod"
    """Payout method. Siren asks the app to pay a payout, in the currencies it
    names, when an autofulfiller picks this method. An ask: Siren waits for
    the app to answer.
    """

    DISTRIBUTION_RESOLVER = "distributionResolver"
    """Distribution resolver. Siren asks the app how to divide a distribution's
    pool when its distributor picks this resolver. An ask: Siren waits for
    the app to answer.
    """

    CUSTOM_EVENT = "customEvent"
    """Custom event. The app sends Siren an event of its own at /event/{key},
    with a short-lived token its key gets. Programs and distributors can
    credit it.
    """

    METRIC_CALCULATION = "metricCalculation"
    """Metric calculation. Siren's code calls it the calculation strategy.
    Siren asks the app who scores, and how much, each time a metric triggers
    for a distributor whose metric type picks this calculation. An ask:
    Siren waits for the app to answer.
    """

    METRIC_FILTER = "metricFilter"
    """Customer filter for distributors. Siren's code calls it the metric
    filter. Siren asks the app whether a metric may score each time one
    triggers for a distributor that picks this filter. An ask: Siren waits
    for the app to answer.
    """

    INCENTIVE_RESOLVER = "incentiveResolver"
    """Attribution model. Siren's code calls it the incentive resolver. Siren
    asks the app how to split a program's reward among the collaborators a
    sale or lead converted, when the program picks this resolver. An ask:
    Siren waits for the app to answer.
    """

    TRANSACTION_FILTER = "transactionFilter"
    """Transaction filter. Siren's code calls it the transaction compiler.
    Siren asks the app which of a sale's lines count, once per sale for each
    program or distributor that picks this filter. An ask: Siren waits for
    the app to answer.
    """

    INCENTIVE_TYPE = "incentiveType"
    """Incentive structure. Siren's code calls it the incentive type. Siren
    asks the app which engagements a sale, lead or renewal converts on a
    program that picks this structure, and the reward pool they share. An
    ask: Siren waits for the app to answer.
    """

    ENGAGEMENT_CALCULATION = "engagementCalculation"
    """Engagement calculation. Siren's code calls it the calculation strategy.
    Siren asks the app who earns an engagement, and its score, each time one
    triggers for a program whose engagement type picks this calculation. An
    ask: Siren waits for the app to answer.
    """

    ENGAGEMENT_FILTER = "engagementFilter"
    """Customer filter for programs. Siren's code calls it the engagement
    filter. Siren asks the app whether an engagement may be written each
    time one triggers for a program that picks this filter. An ask: Siren
    waits for the app to answer.
    """

    PROGRAM_GROUP_SORTER = "programGroupSorter"
    """Program group structure. Siren's code calls it the group sorter. Siren
    asks the app which program of a program group a sale or lead tries
    first, when the group picks this structure. An ask: Siren waits for the
    app to answer.
    """

    COLLABORATOR_GROUP_STRUCTURE = "collaboratorGroupStructure"
    """Collaborator group structure. Siren asks the app who sits above and
    below whom in a collaborator group each time a cascade runs over a group
    that picks this structure. An ask: Siren waits for the app to answer.
    """
    # End of the generated members.

    def __str__(self) -> str:
        return self.value


#: Siren's kind registry, as the snapshot the SDK ships holds it (Siren
#: docs/api/app-kinds.json, ``AppItemKindCatalog::catalog()``): each kind's
#: ``kind``, ``term``, ``name``, ``description``, ``mode`` and ``direction``,
#: in the order of Siren's "New feature" chooser.
APP_KINDS: Tuple[Dict[str, str], ...] = tuple(
    json.loads((Path(__file__).parent / "app_kinds.json").read_text(encoding="utf-8"))
)

#: The 13 kinds Siren sends an app a request for, every kind of the snapshot
#: whose mode is ``tell`` or ``ask``, in its order. The request handler's
#: ``on()`` takes these, and the kinds in its ``extra_kinds`` option.
#: ``customEvent`` is not one: the app sends a custom event, and Siren never
#: sends one.
APP_REQUEST_KINDS: Tuple[str, ...] = tuple(entry["kind"] for entry in APP_KINDS if entry["mode"] in ("tell", "ask"))


class AppFeature(TypedDict, total=False):
    """One of an app's features, as ``/apps/{app}/features`` answers it.
    ``uses``, ``usedBy`` and ``openRequests`` come with one feature's
    routes, and on ``GET /apps/{key}`` for a person who may change the
    app."""

    id: int
    ref: str
    appId: int
    kind: str
    key: str
    name: str
    description: Optional[str]
    settings: Dict[str, Any]
    status: str
    dateCreated: str
    dateModified: str
    eventTypeId: Optional[int]
    entitled: bool
    requiredCapability: Optional[str]
    requiredPlan: Optional[str]
    uses: int
    usedBy: List[Dict[str, Any]]
    openRequests: int


class RemovedAppFeature(TypedDict):
    """What ``apps.features.delete()`` returns: ``archived`` is ``True``
    with the feature when a record still picks it, and ``False`` with
    ``None`` when Siren deleted it."""

    archived: bool
    feature: Optional[AppFeature]


class AppRequestFeature(TypedDict):
    """The feature a request is for, as its envelope's ``feature`` holds it:
    the ``kind``, the app's own ``key`` for it, and its ``id``,
    ``app:{n}``."""

    kind: str
    key: str
    id: str


#: A request's ``data``: the kind's question for an ask, or its data for a tell.
D = TypeVar("D")


@dataclass(frozen=True)
class AppRequest(Generic[D]):
    """A request Siren signed and sent an app, as ``apps.verify_request()``
    returns it once the signature, the time and the envelope check out: an
    :class:`AppAsk` or an :class:`AppTell`. ``D`` is the type of its
    ``data``, such as ``AppAsk[DistributionResolverQuestion]``.

    Three ids, and one to dedupe on:

    - ``id`` is the request's own id, ``apr_`` and 32 hex characters, the
      same on every send of this request. Siren may send one request more
      than once, so write once by ``id``.
    - ``feature["id"]`` names the feature, ``app:{n}``, the same for every
      request to it.
    - ``delivery_id`` is the ``X-Siren-Delivery`` header, which names one
      delivery and changes on every send.

    ``mode`` is ``tell``, which waits for no answer, or ``ask``, answered by
    ``answer_by``, an ISO 8601 time as Siren sends it. ``feature`` holds the feature's ``kind``, ``key`` and ``id``.
    ``data`` is the question. ``timestamp`` is the Unix time Siren signed
    the send at. ``raw`` holds the whole body.

    ``test`` is true for a test request, such as ``siren trigger ask`` makes
    Siren send, and false on every real one. Siren checks a test ask's
    answer by the kind's rules and applies nothing, so never move money or
    write records for one. Answer it as usual and stop there.
    """

    version: int
    id: str
    type: str
    mode: str
    app: str
    feature: AppRequestFeature
    created_at: str
    answer_by: Optional[str]
    answer_url: Optional[str]
    data: D
    timestamp: int
    delivery_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    test: bool = False

    @property
    def is_ask(self) -> bool:
        """Whether Siren waits for an answer to this request."""
        return self.mode == "ask"


@dataclass(frozen=True)
class AppAsk(AppRequest[D]):
    """A request that waits for the app's answer: ``mode`` is ``ask``, and
    ``answer_by`` and ``answer_url`` say when and where. ``D`` is the kind's
    question, such as ``AppAsk[DistributionResolverQuestion]``."""


@dataclass(frozen=True)
class AppTell(AppRequest[D]):
    """A request that needs no answer: ``mode`` is ``tell``. ``D`` is the
    kind's data, such as ``AppTell[PostConversionActionTell]``."""


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

    ``signing_secret`` is ``None`` on a listed subscription, since Siren shows
    an address's secret once, when the app's address is made through
    ``/apps``. ``raw`` holds the untouched API payload.
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


class ApiKeyScopeEntry(TypedDict):
    """One scope an app's key may hold, as ``GET /apps/scopes`` lists it
    (``ApiKeyScopeProviderService``)."""

    scope: str
    """The scope to give the key, such as ``ledger:payouts:read``."""
    resource: str
    """The group it belongs to, such as ``events`` or ``ledger``."""
    action: str
    """What it allows within the group, such as ``payouts:read``."""
    label: str
    """A readable description."""


#: The scopes an app's key may hold, grouped by resource, as
#: ``GET /apps/scopes`` answers them (``ApiKeyScopePolicy::issuableGroups()``):
#: ``events``, ``ledger`` and the ``records:`` groups.
ApiKeyScopeGroups = Dict[str, List[ApiKeyScopeEntry]]


class AppRecord(TypedDict, total=False):
    """An app as Siren's ``/apps`` routes answer it (``AppSerializer``).
    ``url`` and ``events`` are its address, ``scopes`` and ``apiKeyPrefix``
    its key's, each ``None`` or empty when it has none."""

    id: int
    key: str
    name: str
    description: str
    status: str
    source: str
    url: Optional[str]
    events: List[str]
    scopes: List[str]
    apiKeyPrefix: Optional[str]
    apiKey: Optional[Dict[str, Any]]
    address: Optional[Dict[str, Any]]
    features: List[Dict[str, Any]]
    eventsAccess: Dict[str, Any]
    needsAttention: int
    dateCreated: Optional[str]
    dateModified: Optional[str]


class AppWithRawKey(AppRecord, total=False):
    """An app with its new key's ``rawKey``, which Siren shows this once and
    keeps only as a hash."""

    rawKey: str


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
# Siren's own key names. Each kind's types are named from the kind: the kind
# in PascalCase, then ``Tell`` for a tell's data, ``Question`` for an ask's
# question and ``Answer`` for its answer, the same names the Node and PHP
# SDKs use. Each shape follows the Siren builder named in its docstring, on
# Siren's feat/apps. Siren's fixture
# tests/Fixtures/apps-feature-contracts/requests.json holds an example of
# each, and tests/test_typecheck.py holds every example to these types.


class AppCurrency(TypedDict):
    """A currency as Siren's API sends one (``CurrencyAdapter::toArray()``).
    A shown amount is ``value / 10 ** scale``, and the smallest part a
    person sees is ``10 ** (scale - decimalPlaces)`` in the stored form.
    ``position`` is ``before`` or ``after``, and ``type`` is ``monetary`` for
    money or ``internal`` for points or store credit."""

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


class AppIdName(TypedDict):
    """A record Siren names in a question, such as a program, a
    distributor or a collaborator: its ``id`` and its ``name``."""

    id: int
    name: str


class AppId(TypedDict):
    """A record Siren names in a question by its ``id`` alone, such as an
    opportunity or a distribution."""

    id: int


class AppCollaboratorScore(TypedDict):
    """One result of a calculation's answer: a collaborator and a score, a
    whole number of 0 or more."""

    collaboratorId: int
    score: int


# postConversionAction and postAllocationAction, Siren's AppActionSubjects.


class PostConversionActionConversion(TypedDict):
    """The approved conversion in a ``postConversionAction`` tell.
    ``transactionId`` is ``None`` for a lead, and ``obligationId`` when the
    conversion owes nothing."""

    id: int
    type: str
    status: str
    engagementId: int
    opportunityId: int
    collaboratorId: int
    transactionId: Optional[int]
    obligationId: Optional[int]


class PostConversionActionTell(TypedDict):
    """``postConversionAction`` (a tell): a conversion was approved
    (``AppActionSubjects::forConversion()``). ``reward`` is the stored
    amount the conversion earned, or ``None`` when it has no reward."""

    conversion: PostConversionActionConversion
    reward: Optional[AppAmount]
    program: AppIdName


class PostAllocationActionAllocation(TypedDict):
    """The completed allocation in a ``postAllocationAction`` tell.
    ``value`` is a stored whole number in ``units``."""

    distributionId: int
    obligationId: int
    collaboratorId: int
    status: str
    value: int
    units: str
    currency: AppCurrency


class PostAllocationActionTell(TypedDict):
    """``postAllocationAction`` (a tell): an allocation completed
    (``AppActionSubjects::forAllocation()``). The subject id is
    ``{distributionId}:{obligationId}``."""

    allocation: PostAllocationActionAllocation
    distributor: AppIdName


# distributionResolver, Siren's AppDistributionQuestion::build().


class DistributionResolverDistribution(TypedDict):
    """The period a distribution question divides, ISO 8601 times."""

    id: int
    distributorId: int
    periodStart: str
    periodEnd: str


class AppDistributionPool(AppAmount):
    """What a distribution question offers. ``value`` is ``total`` less
    ``paid``, what the distribution's collaborators already hold from it."""

    total: int
    paid: int


class AppDistributionMetric(TypedDict):
    """One collaborator's metric in the period: the score and how many
    times a trigger counted."""

    metricId: int
    collaboratorId: int
    score: int
    triggerCount: int


class DistributionResolverQuestion(TypedDict):
    """``distributionResolver`` (an ask): split ``pool`` among ``metrics``
    (``AppDistributionQuestion::build()``)."""

    distribution: DistributionResolverDistribution
    distributor: AppIdName
    pool: AppDistributionPool
    metrics: List[AppDistributionMetric]


class DistributionResolverAnswer(TypedDict):
    """One share per metric id, 0 or more, each a whole step of the
    currency, adding up to at most ``pool.value``. ``outcome`` is
    ``allocated`` and ``units`` the pool's units."""

    outcome: Literal["allocated"]
    units: str
    shares: Mapping[str, int]


# incentiveResolver, Siren's AppRewardQuestion::build().


class IncentiveResolverProgram(TypedDict):
    """The program in an ``incentiveResolver`` ask, with its ``status`` as
    it was at the sale."""

    id: int
    name: str
    status: str


class AppTransaction(TypedDict):
    """The sale an ``incentiveResolver`` ask is about: its ``id``, its
    ``units`` and its currency."""

    id: int
    units: str
    currency: AppCurrency


class IncentiveResolverConversion(TypedDict):
    """One conversion in an ``incentiveResolver`` ask.
    ``collaboratorStatus`` is as it was at the sale, and
    ``engagementCreatedDate`` an ISO 8601 time."""

    conversionId: int
    engagementId: int
    collaboratorId: int
    collaboratorStatus: str
    score: int
    engagementCreatedDate: str


class IncentiveResolverQuestion(TypedDict):
    """``incentiveResolver`` (an ask): reward the ``conversions`` one award
    made from ``pool`` (``AppRewardQuestion::build()``). ``reason`` is
    ``sale``, or ``refund`` when a partial refund asks again.
    ``transaction`` is ``None`` for a lead."""

    reason: Literal["sale", "refund"]
    program: IncentiveResolverProgram
    conversionType: str
    transaction: Optional[AppTransaction]
    pool: AppAmount
    conversions: List[IncentiveResolverConversion]


class IncentiveResolverAnswer(TypedDict):
    """A key for every conversion id of the question, ``None`` for one the
    app did not choose or a whole number of 0 or more. ``outcome`` is
    ``rewards`` and ``units`` the pool's units."""

    outcome: Literal["rewards"]
    units: str
    rewards: Mapping[str, Optional[int]]


# incentiveType, Siren's AppIncentiveTypeQuestion::build().


class IncentiveTypeProgram(TypedDict):
    """The program in an ``incentiveType`` ask, with the ``units`` its pool
    is in."""

    id: int
    name: str
    units: str


class IncentiveTypeLine(TypedDict):
    """One line of the transaction in an ``incentiveType`` ask, refund
    lines included. ``value`` is a stored whole number."""

    id: int
    type: str
    name: str
    quantity: int
    value: int


class IncentiveTypeTransaction(TypedDict):
    """The sale in an ``incentiveType`` ask. ``total`` is the sum of its
    lines in the stored form. ``currency`` is ``None`` when Siren has no
    currency for the sale's units."""

    id: int
    status: str
    units: str
    currency: Optional[AppCurrency]
    total: int
    lines: List[IncentiveTypeLine]


class IncentiveTypeBinding(TypedDict):
    """What a lead is bound by, in place of a transaction."""

    type: str
    id: str


class IncentiveTypeConversion(TypedDict):
    """One conversion of the award a refund asks again about."""

    conversionId: int
    engagementId: int


class AppIncentiveTypeEngagement(TypedDict):
    """One engagement in an ``incentiveType`` ask. ``createdDate`` is an
    ISO 8601 time."""

    engagementId: int
    collaboratorId: int
    score: int
    createdDate: str


class _IncentiveTypeQuestionFields(TypedDict):
    reason: Literal["sale", "refund"]
    program: IncentiveTypeProgram
    conversionType: str
    opportunity: AppId
    transaction: Optional[IncentiveTypeTransaction]
    binding: Optional[IncentiveTypeBinding]
    engagements: List[AppIncentiveTypeEngagement]


class IncentiveTypeQuestion(_IncentiveTypeQuestionFields, total=False):
    """``incentiveType`` (an ask): which engagements one sale, lead or
    renewal converts on the ``program``, and the pool its resolver divides
    (``AppIncentiveTypeQuestion::build()``). ``reason`` is ``sale`` until
    the award is made, and ``refund`` when a partial refund asks again about
    an award already made, which then lists the award's ``conversions``.
    ``transaction`` is ``None`` for a lead, which names its ``binding``
    instead."""

    conversions: List[IncentiveTypeConversion]


class IncentiveTypeAwardAnswer(TypedDict):
    """The engagements of the question that convert, each once, and the
    ``pool``, a whole number of 0 or more in the program's ``units``.
    ``outcome`` is ``award``."""

    outcome: Literal["award"]
    units: str
    pool: int
    engagementIds: List[int]


class IncentiveTypeNoneAnswer(TypedDict):
    """Award nothing on the program. ``outcome`` is ``none``."""

    outcome: Literal["none"]


IncentiveTypeAnswer = Union[IncentiveTypeAwardAnswer, IncentiveTypeNoneAnswer]
"""The answer to an ``incentiveType`` ask."""


# metricCalculation and metricFilter, Siren's AppMetricQuestion.


class AppMetricCause(TypedDict):
    """What caused a metric, in a metric ask. ``id`` is the metric event's
    key. For a sale, ``type`` is ``transaction`` and ``causeId`` the
    transaction id as text. Both are ``None`` when the cause has no record."""

    id: str
    type: Optional[str]
    causeId: Optional[str]


class AppMetricSale(TypedDict):
    """The sale behind a metric, in a metric ask. ``excludedLineIds`` lists
    the sale's lines the metric leaves out."""

    transactionId: int
    conversionId: Optional[int]
    excludedLineIds: List[int]


class MetricCalculationDistribution(TypedDict):
    """The period a metric is scored in. ``periodEnd`` is an ISO 8601 time."""

    id: int
    periodEnd: str


class MetricCalculationQuestion(TypedDict):
    """``metricCalculation`` (an ask): score one metric for a distributor's
    period (``AppMetricQuestion``). ``distributorMetricTypeId`` is the
    distributor's metric type row that picks the calculation.
    ``opportunityId`` and ``points`` may be ``None``, and ``sale`` is
    ``None`` when no sale caused the metric."""

    distributor: AppIdName
    distribution: MetricCalculationDistribution
    metricType: str
    distributorMetricTypeId: int
    triggeringCollaborator: AppIdName
    opportunityId: Optional[int]
    points: Optional[int]
    cause: AppMetricCause
    sale: Optional[AppMetricSale]


class MetricCalculationAnswer(TypedDict):
    """Who scores and how much: at most 50 results, each collaborator once,
    each in the organization and not in the trash, and each score a whole
    number of 0 or more. An empty list scores nobody. ``outcome`` is
    ``scores``."""

    outcome: Literal["scores"]
    results: List[AppCollaboratorScore]


class MetricFilterQuestion(TypedDict):
    """``metricFilter`` (an ask): whether one ``collaborator``'s metric
    counts for a distributor (``AppMetricQuestion``). ``opportunityId`` and
    ``points`` may be ``None``, and ``sale`` is ``None`` when no sale caused
    the metric."""

    distributor: AppIdName
    distribution: AppId
    collaborator: AppIdName
    metricType: str
    opportunityId: Optional[int]
    points: Optional[int]
    cause: AppMetricCause
    sale: Optional[AppMetricSale]


class MetricFilterAllowAnswer(TypedDict):
    """Count the metric. ``outcome`` is ``allow``."""

    outcome: Literal["allow"]


class MetricFilterSuppressAnswer(TypedDict):
    """Drop the metric, with a plain text ``reason`` of 1 to 200
    characters. ``outcome`` is ``suppress``."""

    outcome: Literal["suppress"]
    reason: str


MetricFilterAnswer = Union[MetricFilterAllowAnswer, MetricFilterSuppressAnswer]
"""The answer to a ``metricFilter`` ask: allow, or suppress with a reason."""


# engagementCalculation and engagementFilter, Siren's AppEngagementQuestion.


class AppEngagementCause(TypedDict):
    """The trigger behind an engagement ask. ``id`` is the trigger's own
    random id, ``event`` the engagement type that fired, and ``occurredAt``
    an ISO 8601 time that dates the engagements the answer makes."""

    id: str
    event: str
    occurredAt: str


class AppEngagementOpportunity(TypedDict):
    """The opportunity in an engagement ask, with the customer Siren
    matched it to, or ``None``."""

    id: int
    customerId: Optional[int]


class EngagementCalculationQuestion(TypedDict):
    """``engagementCalculation`` (an ask): who earns an engagement, and its
    score, when a program's engagement type triggers
    (``AppEngagementQuestion::forCalculation()``). ``args`` holds the
    settings a person gave the engagement type, as text by name. Siren
    sends an empty ``args`` as ``[]`` today, and the SDK reads that as
    ``{}``."""

    program: AppIdName
    engagementType: str
    programEngagementTypeId: int
    args: Dict[str, str]
    opportunity: AppEngagementOpportunity
    triggeringCollaborator: AppIdName
    cause: AppEngagementCause


class EngagementCalculationAnswer(TypedDict):
    """Who earns an engagement and its score, at most 50 results, each
    collaborator once and each score a whole number of 0 or more.
    ``outcome`` is ``engagements``."""

    outcome: Literal["engagements"]
    results: List[AppCollaboratorScore]


class EngagementFilterQuestion(TypedDict):
    """``engagementFilter`` (an ask): whether an engagement may be made for
    ``collaborator`` on ``program`` (``AppEngagementQuestion::forFilter()``)."""

    program: AppIdName
    collaborator: AppIdName
    opportunity: AppEngagementOpportunity
    engagementType: str
    cause: AppEngagementCause


class EngagementFilterAllowAnswer(TypedDict):
    """Make the engagement. ``outcome`` is ``allow``."""

    outcome: Literal["allow"]


class EngagementFilterSuppressAnswer(TypedDict):
    """Stop the engagement, with a plain text ``reason`` of 1 to 200
    characters, which Siren notes on the opportunity. ``outcome`` is
    ``suppress``."""

    outcome: Literal["suppress"]
    reason: str


EngagementFilterAnswer = Union[EngagementFilterAllowAnswer, EngagementFilterSuppressAnswer]
"""The answer to an ``engagementFilter`` ask: allow, or suppress with a
reason."""


# programGroupSorter, Siren's AppGroupOrderQuestion::build().


class ProgramGroupSorterEngagement(TypedDict):
    """One active engagement a program holds on the opportunity at the
    sale. ``createdDate`` is an ISO 8601 time."""

    engagementId: int
    collaboratorId: int
    score: int
    createdDate: str


class ProgramGroupSorterProgram(TypedDict):
    """One program of the group, with its engagements oldest first."""

    id: int
    name: str
    engagements: List[ProgramGroupSorterEngagement]


class ProgramGroupSorterTransaction(TypedDict):
    """The sale a ``programGroupSorter`` ask is about. ``total`` is a
    stored whole number."""

    id: int
    units: str
    total: int


class ProgramGroupSorterQuestion(TypedDict):
    """``programGroupSorter`` (an ask): order the group's ``programs`` for
    one sale or lead (``AppGroupOrderQuestion::build()``). ``programs``
    holds each program of the group that takes the conversion once, in the
    group's order. ``transaction`` is ``None`` for a lead."""

    group: AppIdName
    conversionType: str
    opportunity: AppId
    transaction: Optional[ProgramGroupSorterTransaction]
    programs: List[ProgramGroupSorterProgram]


class ProgramGroupSorterAnswer(TypedDict):
    """Every program id of the question exactly once, in the order Siren
    tries them. ``outcome`` is ``sorted``."""

    outcome: Literal["sorted"]
    programIds: List[int]


# transactionFilter, Siren's AppTransactionFilterQuestion::build().


class TransactionFilterTransaction(TypedDict):
    """The sale a ``transactionFilter`` ask is about. ``currency`` is
    ``None`` for a sale with no lines, which Siren's builder allows
    (``'currency' => $first === null ? null : ...``)."""

    id: int
    units: str
    currency: Optional[AppCurrency]


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


class TransactionFilterFor(TypedDict):
    """The program or distributor a ``transactionFilter`` ask is for."""

    type: Literal["program", "distributor"]
    id: int
    name: str


# ``for`` is a Python keyword, so the question uses the functional form.
TransactionFilterQuestion = TypedDict(
    "TransactionFilterQuestion",
    {
        "for": TransactionFilterFor,
        "transaction": TransactionFilterTransaction,
        "lines": List[AppTransactionLine],
        "distribution": Optional[AppId],
        "refundDistribution": Optional[AppId],
    },
)
TransactionFilterQuestion.__doc__ = """``transactionFilter`` (an ask): say which
of the sale's own ``lines`` count, once per sale for each program or
distributor that picks the filter (``AppTransactionFilterQuestion::build()``).
The subject id is ``{opportunityId}:{programId}:t{transactionId}`` for a
program's lines and ``{distributorId}:t{transactionId}`` for a
distributor's. ``distribution`` is ``None`` for a program, and for a
distributor the period the sale's value lands in. ``refundDistribution`` is
``None``, or the later period a refund came in while the ask was open. A
refund reuses the stored answer, so Siren never sends refund lines."""


class TransactionFilterAnswer(TypedDict):
    """The ids of the question's lines that count, each at most once. An
    empty list includes nothing. A line counts when any filter the record
    picks includes it. Siren refuses an id the question lacks with 422
    ``app_answer_invalid``. ``outcome`` is ``filtered``."""

    outcome: Literal["filtered"]
    include: List[int]


# collaboratorGroupStructure, Siren's AppGroupStructureQuestion.


class AppCollaboratorGroupMember(TypedDict):
    """One member of a collaborator group in a group structure question.
    ``status`` is the collaborator's status at the trigger, such as
    ``active`` or ``inactive``, which Siren credits the member by.
    ``metadata`` is the member's metadata as the group holds it, which has
    no fixed shape."""

    collaboratorId: int
    name: str
    status: str
    metadata: Dict[str, Any]


class CollaboratorGroupStructureFor(TypedDict):
    """The program (an engagement cascade) or distributor (a metric
    cascade) a group structure question is for."""

    type: Literal["program", "distributor"]
    id: int


# ``for`` is a Python keyword, so the shared fields use the functional form.
_CollaboratorGroupStructureFields = TypedDict(
    "_CollaboratorGroupStructureFields",
    {
        "group": AppIdName,
        "members": List[AppCollaboratorGroupMember],
        "for": CollaboratorGroupStructureFor,
        "triggeringCollaborator": AppIdName,
    },
)


class CollaboratorGroupStructureDistributorQuestion(_CollaboratorGroupStructureFields):
    """``collaboratorGroupStructure`` (an ask) for a distributor's metric
    cascade (``AppGroupStructureQuestion``). It carries a metric
    calculation's keys beside the group's, and its ``cause`` is a
    metric's. ``opportunityId``, ``points`` and ``sale`` may be ``None``."""

    distributor: AppIdName
    distribution: MetricCalculationDistribution
    metricType: str
    distributorMetricTypeId: int
    opportunityId: Optional[int]
    points: Optional[int]
    cause: AppMetricCause
    sale: Optional[AppMetricSale]


class CollaboratorGroupStructureProgramQuestion(_CollaboratorGroupStructureFields):
    """``collaboratorGroupStructure`` (an ask) for a program's engagement
    cascade (``AppGroupStructureQuestion::forEngagement()``). It carries an
    engagement calculation's keys beside the group's, and its ``cause`` is
    an engagement's. ``args`` holds the settings a person gave the
    engagement type, as text by name, and the SDK reads an empty ``[]`` as
    ``{}``."""

    program: AppIdName
    engagementType: str
    programEngagementTypeId: int
    args: Dict[str, str]
    opportunity: AppEngagementOpportunity
    cause: AppEngagementCause


CollaboratorGroupStructureQuestion = Union[
    CollaboratorGroupStructureDistributorQuestion, CollaboratorGroupStructureProgramQuestion
]
"""``collaboratorGroupStructure`` (an ask): give the ``group``'s shape among
its ``members``, once per cascade trigger on the group. ``for`` says which
side asks: ``distributor`` (a metric cascade) or ``program`` (an engagement
cascade). ``cause["id"]`` is 16 hex characters, the trigger's own random
id."""


class CollaboratorGroupStructureChainAnswer(TypedDict):
    """A chain answer: ``order`` lists every member's ``collaboratorId`` of
    the question once, top of the chain first. Siren reads it as its
    built-in linear chain. ``outcome`` is ``structure``."""

    outcome: Literal["structure"]
    shape: Literal["chain"]
    order: List[int]


class CollaboratorGroupStructureTreeAnswer(TypedDict):
    """A tree answer: ``parents`` has every member's ``collaboratorId`` of
    the question as text once, each mapped to another member's id or
    ``None`` for a top member, with no cycle. Siren reads it as its parent
    and child structure. ``outcome`` is ``structure``."""

    outcome: Literal["structure"]
    shape: Literal["tree"]
    parents: Mapping[str, Optional[int]]


CollaboratorGroupStructureAnswer = Union[CollaboratorGroupStructureChainAnswer, CollaboratorGroupStructureTreeAnswer]
"""The answer to a ``collaboratorGroupStructure`` ask, a chain or a tree.
Siren refuses one that breaks the rules with 422 ``app_answer_invalid``."""


# payoutMethod, Siren's AppPayoutQuestion.


class PayoutMethodPayout(TypedDict):
    """The payout to pay: its ``id``, its fulfillment, and ``attempt``,
    which starts at 1 and rises when Siren asks again about it."""

    id: int
    fulfillmentId: int
    attempt: int


class PayoutMethodCollaborator(TypedDict):
    """Who to pay. ``email`` is ``None`` when they have none."""

    id: int
    name: str
    email: Optional[str]


class PayoutMethodQuestion(TypedDict):
    """``payoutMethod`` (an ask): pay one payout (``AppPayoutQuestion``).
    ``amount`` is what to pay, a stored whole number in the currency's
    smallest unit. Pay each payout id at most once, and when Siren asks
    again about a payout the app already paid, answer ``paid`` with the
    first reference."""

    payout: PayoutMethodPayout
    amount: AppAmount
    collaborator: PayoutMethodCollaborator


class PayoutMethodPaidAnswer(TypedDict):
    """The payout was paid. ``reference`` is the payment's id in the app's
    own system, 1 to 191 characters."""

    outcome: Literal["paid"]
    reference: str


class PayoutMethodFailedAnswer(TypedDict):
    """The app couldn't pay. ``reason`` says why in plain words, 1 to 500
    characters, and Siren marks the payout failed."""

    outcome: Literal["failed"]
    reason: str


PayoutMethodAnswer = Union[PayoutMethodPaidAnswer, PayoutMethodFailedAnswer]
"""The answer to a ``payoutMethod`` ask: paid with a reference, or failed
with a reason."""


# Requests as Siren's request routes list them.

#: The statuses of a request Siren sent an app
#: (lib/Apps/Core/Models/AppRequest.php). A tell ends at ``delivered``. An
#: ask ends at ``applied``, once its answer has done its work.
#: ``needs_attention`` waits for a person or a late answer. An ask that
#: holds a reward ends at ``dropped`` when the app didn't answer within 24
#: hours.
APP_REQUEST_STATUSES: Tuple[str, ...] = (
    "pending", "sent", "delivered", "answered", "applied", "needs_attention", "dropped",
)


class AppRequestSubject(TypedDict):
    """What a request is about, such as ``{"type": "distribution", "id": "21"}``."""

    type: str
    id: str


class AppRequestRecord(TypedDict, total=False):
    """A request Siren sent an app, as ``GET /apps/requests`` lists it
    (``AppRequestSerializer``). ``id`` is the request key, the same on every
    send. ``feature`` is ``app:{id}`` of the feature Siren acted for.
    ``answeredBy`` is ``app:{appId}`` or ``user:{userId}``. ``test`` is true
    for a test request, and absent on a real one. The data and the answer
    stay out of the list."""

    id: str
    appId: int
    feature: str
    kind: str
    mode: str
    test: bool
    subject: AppRequestSubject
    status: str
    sendCount: int
    answerBy: Optional[str]
    applyLease: Optional[str]
    answeredBy: Optional[str]
    answeredAt: Optional[str]
    lastError: Optional[str]
    dropsAt: Optional[str]
    createdAt: str


class AppRequestUser(TypedDict):
    """The person who answered a request by hand."""

    id: int
    email: str


class AppRequestDetail(AppRequestRecord, total=False):
    """One request as ``GET /apps/requests/{id}`` answers it
    (``AppRequestSerializer::toDetail()``): the list's fields, the ``data``
    Siren sent, the ``answer`` it took or ``None``, and ``answeredByUser``,
    the person behind a ``user:{id}`` answer, or ``None``."""

    data: Dict[str, Any]
    answer: Optional[Dict[str, Any]]
    answeredByUser: Optional[AppRequestUser]
