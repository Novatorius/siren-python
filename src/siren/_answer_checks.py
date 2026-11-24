"""Siren's own checks of an ask's answer, ported for ``simulate()``.

Siren's answer route refuses an answer that doesn't fit its request with 422
``app_answer_invalid`` and one sentence. :func:`siren_refusal` gives that
sentence for an answer, word for word, so a test sees Siren's refusal before
Siren does. The live handler never runs it: it posts, and Siren decides.

Each rule cites the Siren file and method it comes from, on Siren's
feat/apps branch at 6d2abd936a. Siren reads the answer after PHP's
``json_decode(..., true)``, so these helpers read a Python value the way PHP
would read the same JSON: an object's key that is a plain integer becomes an
integer, ``{}`` becomes an empty list, and only a JSON number with no
fraction is an integer.

Siren checks a few things this can't, because they need Siren's records: a
calculation's collaborator that is gone from the organization or in the
trash, a collaborator merged into another since the question, an ask that
passed its 24-hour limit, and a collaborator group too large to ask about.
Those pass here and Siren refuses them on the post.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = ["siren_refusal"]

#: lib/Apps/Service/Services/AppRequestService.php, MAX_ANSWER_BYTES.
MAX_ANSWER_BYTES = 16384

_PHP_INT_MAX = 2**63 - 1
_PHP_INT_KEY = re.compile(r"-?(?:0|[1-9][0-9]*)")
_FILTER_INT = re.compile(r"[+-]?(?:0|[1-9][0-9]*)")
_PHP_TRIM = " \t\n\r\0\x0b"
_MARKUP_OR_CONTROL = re.compile(r"[\x00-\x1F\x7F]|<[^>]*>")

Pairs = List[Tuple[Any, Any]]


# PHP's view of a decoded value ------------------------------------------------


def _is_int(value: Any) -> bool:
    """PHP's ``is_int()`` on a decoded JSON value."""
    return isinstance(value, int) and not isinstance(value, bool)


def _php_key(key: str) -> Any:
    """An object's key as a PHP array holds it: a plain decimal integer in
    range becomes an int, and anything else stays text."""
    if _PHP_INT_KEY.fullmatch(key) and key != "-0":
        number = int(key)
        if -_PHP_INT_MAX - 1 <= number <= _PHP_INT_MAX:
            return number
    return key


def _array(value: Any) -> Optional[Pairs]:
    """A decoded JSON object or list as PHP's array, as (key, value) pairs in
    order, or ``None`` when PHP's ``is_array()`` is false."""
    if isinstance(value, dict):
        return [(_php_key(str(key)), item) for key, item in value.items()]
    if isinstance(value, list):
        return list(enumerate(value))
    return None


def _is_list(pairs: Pairs) -> bool:
    """PHP's ``array_is_list()``: the keys are 0, 1, 2 and on, in order."""
    return all(_is_int(key) and key == i for i, (key, _) in enumerate(pairs))


def _text(value: Any) -> str:
    """PHP's ``(string)`` cast of a scalar."""
    if isinstance(value, bool):
        return "1" if value else ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    return str(value)


def _encoded(value: Any) -> str:
    """PHP's ``json_encode()`` with no flags: ASCII only, and ``/`` escaped."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).replace("/", "\\/")


def _get(mapping: Any, *path: str) -> Any:
    for name in path:
        if not isinstance(mapping, dict):
            return None
        mapping = mapping.get(name)
    return mapping


def _ids(rows: Any, field: str) -> List[int]:
    """The ``field`` of each row of a question's list, as ``(int)``, for the
    rows where PHP's ``isset()`` holds."""
    ids: List[int] = []
    for row in rows if isinstance(rows, list) else []:
        value = row.get(field) if isinstance(row, dict) else None
        if value is not None:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                ids.append(0)
    return ids


# The checks, kind by kind -----------------------------------------------------


def _distribution_resolver(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/Resolvers/AppDistributionQuestion.php, unfitness(),
    rules 1 to 9 in its order. AppDistributionAnswerHandler::validate()
    calls it."""
    pool_value: Any = question.get("pool")
    offer: Dict[str, Any] = pool_value if isinstance(pool_value, dict) else {}
    units = _text(offer.get("units", ""))
    currency_value: Any = offer.get("currency")
    currency: Dict[str, Any] = currency_value if isinstance(currency_value, dict) else {}
    asked = _ids(question.get("metrics"), "metricId")

    # Rule 1.
    if answer.get("outcome") != "allocated":
        return "outcome must be allocated"
    # Rule 2.
    if answer.get("units") != units or not isinstance(answer.get("units"), str):
        return f"units must be {units}, the units of the pool"
    # Rule 3.
    shares = _array(answer.get("shares"))
    if shares is None:
        return "shares must be an object of metric id to amount"
    # Rule 4. A key that is not a plain integer names no metric.
    for metric_id, _ in shares:
        if not _is_int(metric_id) or metric_id not in asked:
            return f"shares names metric {metric_id}, which this distribution does not hold"
    # Rule 5.
    for metric_id, amount in shares:
        if not _is_int(amount):
            return f"the share for metric {metric_id} must be a whole number of the smallest unit"
    # Rule 6.
    for metric_id, amount in shares:
        if amount < 0:
            return f"the share for metric {metric_id} must not be below 0"
    # Rule 7. 1 for USD and for points, 100 for JPY.
    scale = int(currency.get("scale", 0) or 0)
    places = int(currency.get("decimalPlaces", scale) or 0)
    step = 10 ** max(0, scale - places)
    for metric_id, amount in shares:
        if amount % step != 0:
            return f"the share for metric {metric_id} must be a multiple of {step}, as {units} amounts have no smaller part"
    # Rule 8.
    held = {metric_id for metric_id, _ in shares}
    for metric_id in asked:
        if metric_id not in held:
            return f"shares has no amount for metric {metric_id}, and every metric needs one, 0 included"
    # Rule 9. Past PHP_INT_MAX the sum is a float, and Siren leaves it out.
    total = sum(amount for _, amount in shares)
    pool = int(offer.get("value", 0) or 0)
    if total > pool:
        if total > _PHP_INT_MAX:
            return f"the shares add up to more than the pool of {pool}"
        return f"the shares add up to {total}, more than the pool of {pool}"
    return None


def _incentive_resolver(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/Rewards/AppRewardQuestion.php, validate(), which
    AppRewardAnswerHandler::validate() calls."""
    if answer.get("outcome") != "rewards":
        return 'The answer\'s outcome must be "rewards".'
    units = _text(_get(question, "pool", "units") or "")
    if not isinstance(answer.get("units"), str) or answer.get("units") != units:
        return f'The answer\'s units must be "{units}", the units of the pool Siren offered.'
    rewards = _array(answer.get("rewards"))
    if rewards is None or (rewards != [] and _is_list(rewards)):
        return "The answer's rewards must be an object with one key per conversion id in the question."
    asked = _ids(question.get("conversions"), "conversionId")
    pool = int(_get(question, "pool", "value") or 0)
    for key, value in rewards:
        if not _is_int(key) or key not in asked:
            return f"The answer names conversion {key}, which the question does not hold."
        if value is not None and (not _is_int(value) or value < 0):
            return f"The reward for conversion {key} must be null or a whole number at or above 0, in the stored integer form."
        if value is not None and value > pool:
            return f"The reward for conversion {key} is {value}, more than the pool of {pool} Siren offered."
    held = {key for key, _ in rewards}
    for conversion_id in asked:
        if conversion_id not in held:
            return (
                f"The answer has no reward for conversion {conversion_id}. "
                "Give every conversion of the question a key, null for one the app did not choose."
            )
    return None


def _scalar_or_json(value: Any) -> str:
    """``is_scalar($id) ? (string) $id : json_encode($id)``."""
    if isinstance(value, (bool, int, float, str)):
        return _text(value)
    return _encoded(value)


def _incentive_type(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/IncentiveTypes/AppIncentiveTypeQuestion.php,
    validate(), which AppIncentiveTypeAnswerHandler::validate() calls."""
    outcome = answer.get("outcome")
    if outcome == "none" and isinstance(outcome, str):
        extra = [str(key) for key in answer if key != "outcome"]
        return None if not extra else f'An answer of "none" carries nothing else, and this one has {", ".join(extra)}.'
    if outcome != "award" or not isinstance(outcome, str):
        return 'The answer\'s outcome must be "award" or "none".'
    units = _text(_get(question, "program", "units") or "")
    if not isinstance(answer.get("units"), str) or answer.get("units") != units:
        return f'The answer\'s units must be "{units}", the program\'s units.'
    pool: Any = answer.get("pool")
    if not _is_int(pool) or pool < 0:
        return "The answer's pool must be a whole number at or above 0, in the stored integer form."
    ids = _array(answer.get("engagementIds"))
    if ids is None or not _is_list(ids) or ids == []:
        return "The answer's engagementIds must be a list of at least one engagement id from the question."
    asked = _ids(question.get("engagements"), "engagementId")
    seen = set()
    for _, engagement_id in ids:
        if not _is_int(engagement_id) or engagement_id not in asked:
            return f"The answer names engagement {_scalar_or_json(engagement_id)}, which the question does not hold."
        if engagement_id in seen:
            return f"The answer names engagement {engagement_id} more than once."
        seen.add(engagement_id)
    extra = [str(key) for key in answer if key not in ("outcome", "units", "pool", "engagementIds")]
    return None if not extra else f"The answer has fields Siren does not read: {', '.join(extra)}."


def _calculation(outcome: str) -> Callable[[Dict[str, Any], Dict[str, Any]], Optional[str]]:
    """lib/Apps/Service/Handlers/AppMetricCalculationAnswerHandler.php and
    lib/Apps/Service/Engagements/AppEngagementCalculationAnswerHandler.php,
    validate(), the same rules with their own outcome. MAX_RESULTS is 50 in
    both. The last rule of each, a collaborator gone from the organization,
    needs Siren's records, so it is left to Siren."""

    def check(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
        if answer.get("outcome") != outcome:
            return f"outcome must be {outcome}."
        results = _array(answer.get("results"))
        if results is None or not _is_list(results):
            return "results must be a list of {collaboratorId, score}."
        if len(results) > 50:
            return f"results may hold at most 50 entries, and this holds {len(results)}."
        seen = set()
        for i, result in results:
            collaborator_id: Any = result.get("collaboratorId") if isinstance(result, dict) else None
            score: Any = result.get("score") if isinstance(result, dict) else None
            if not _is_int(collaborator_id) or collaborator_id < 1:
                return f"results[{i}].collaboratorId must be a collaborator id."
            if not _is_int(score) or score < 0:
                return f"results[{i}].score must be a whole number of 0 or more."
            if collaborator_id in seen:
                return f"results names collaborator {collaborator_id} more than once."
            seen.add(collaborator_id)
        return None

    return check


def _filter(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/Handlers/AppMetricFilterAnswerHandler.php and
    lib/Apps/Service/Engagements/AppEngagementFilterAnswerHandler.php,
    validate(), the same rules. MAX_REASON is 200 in both."""
    outcome = answer.get("outcome")
    if outcome == "allow":
        return None
    if outcome != "suppress":
        return "outcome must be allow or suppress."
    reason = answer.get("reason")
    if not isinstance(reason, str) or reason.strip(_PHP_TRIM) == "":
        return "A suppress answer gives a reason, in plain text."
    if len(reason) > 200:
        return f"reason may be at most 200 characters, and this is {len(reason)}."
    if _MARKUP_OR_CONTROL.search(reason):
        return "reason must be plain text, with no markup or control characters."
    return None


def _program_group_sorter(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/GroupOrders/AppGroupOrderQuestion.php, validate(),
    which AppGroupOrderAnswerHandler::validate() calls."""
    if answer.get("outcome") != "sorted":
        return "outcome must be sorted."
    order = _array(answer.get("programIds"))
    if order is None or not _is_list(order):
        return "programIds must be a list of the question's program ids."
    asked = _ids(question.get("programs"), "id")
    seen = set()
    for _, program_id in order:
        if not _is_int(program_id):
            return "Each program id is a whole number."
        if program_id not in asked:
            return f"Program {program_id} is not one of the programs Siren asked about."
        if program_id in seen:
            return f"Program {program_id} is listed more than once."
        seen.add(program_id)
    for program_id in asked:
        if program_id not in seen:
            return f"The answer leaves out program {program_id}. List every program Siren asked about."
    return None


def _transaction_filter(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/TransactionFilters/AppTransactionFilterQuestion.php,
    validate(), which AppProgramLinesAnswerHandler::validate() and
    AppDistributorLinesAnswerHandler::validate() both call."""
    if answer.get("outcome") != "filtered":
        return 'The answer\'s outcome must be "filtered".'
    include = _array(answer.get("include"))
    if include is None or not _is_list(include):
        return "The answer's include must be a list of line ids from the question."
    asked = _ids(question.get("lines"), "id")
    seen = set()
    for _, line_id in include:
        if not _is_int(line_id):
            return "Each id in the answer's include must be a whole number, a line id from the question."
        if line_id not in asked:
            return f"The answer includes line {line_id}, which is not a line of the sale Siren asked about."
        if line_id in seen:
            return f"The answer includes line {line_id} more than once."
        seen.add(line_id)
    return None


def _members(question: Dict[str, Any]) -> List[int]:
    """lib/Apps/Service/Items/AppGroupStructureQuestion.php, memberIds(),
    once each, as problem() takes them."""
    members: List[int] = []
    rows: Any = question.get("members")
    for member in rows if isinstance(rows, list) else []:
        collaborator_id: Any = member.get("collaboratorId") if isinstance(member, dict) else None
        if _is_int(collaborator_id) and collaborator_id not in members:
            members.append(collaborator_id)
    return members


def _chain_problem(order_value: Any, members: List[int]) -> Optional[str]:
    """AppGroupStructureAnswerHandler::chainProblem()."""
    order = _array(order_value)
    if order is None or not _is_list(order):
        return "order must be a list of the members' collaborator ids, top first."
    seen: List[int] = []
    for i, collaborator_id in order:
        if not _is_int(collaborator_id):
            return f"order[{i}] must be a collaborator id."
        if collaborator_id not in members:
            return f"order[{i}] names collaborator {collaborator_id}, who is not a member in the question."
        if collaborator_id in seen:
            return f"order names collaborator {collaborator_id} more than once."
        seen.append(collaborator_id)
    missing = [member for member in members if member not in seen]
    return None if not missing else f"order leaves out member {missing[0]}. List every member of the question once."


def _tree_problem(parents_value: Any, members: List[int]) -> Optional[str]:
    """AppGroupStructureAnswerHandler::treeProblem()."""
    parents = _array(parents_value)
    if parents is None:
        return "parents must be an object of each member's collaborator id to its parent's, or null at the top."
    parent_of: Dict[int, Optional[int]] = {}
    for key, parent_id in parents:
        # filter_var($key, FILTER_VALIDATE_INT).
        collaborator_id: Optional[int] = key if _is_int(key) else None
        if collaborator_id is None and isinstance(key, str) and _FILTER_INT.fullmatch(key.strip()):
            collaborator_id = int(key.strip())
        if collaborator_id is None or collaborator_id not in members:
            return f"parents names {key}, which is not a member in the question."
        if parent_id is not None and not _is_int(parent_id):
            return f'parents["{collaborator_id}"] must be a member\'s collaborator id or null.'
        if parent_id is not None and parent_id not in members:
            return f'parents["{collaborator_id}"] names collaborator {parent_id}, who is not a member in the question.'
        if parent_id == collaborator_id:
            return f'parents["{collaborator_id}"] names the member as their own parent.'
        parent_of[collaborator_id] = parent_id
    missing = [member for member in members if member not in parent_of]
    if missing:
        return f"parents leaves out member {missing[0]}. Name every member of the question once, with null at the top."
    # Each walk up ends at the top or at a member already known to end there.
    ends_at_top: Dict[int, bool] = {}
    for start in parent_of:
        path: Dict[int, bool] = {}
        at: Optional[int] = start
        while at is not None and at not in ends_at_top:
            if at in path:
                return f"parents form a cycle through member {at}. Every member must reach the top."
            path[at] = True
            at = parent_of[at]
        ends_at_top.update(path)
    return None


def _collaborator_group_structure(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/Handlers/AppGroupStructureAnswerHandler.php,
    problem(), which each group structure handler's validate() calls. Its
    first rule, a group too large to ask about, and its merge redirects need
    Siren's records."""
    if answer.get("outcome") != "structure":
        return "outcome must be structure."
    members = _members(question)
    shape = answer.get("shape")
    if shape == "chain" and isinstance(shape, str):
        return _chain_problem(answer.get("order"), members)
    if shape == "tree" and isinstance(shape, str):
        return _tree_problem(answer.get("parents"), members)
    return "shape must be chain or tree."


def _payout_method(question: Dict[str, Any], answer: Dict[str, Any]) -> Optional[str]:
    """lib/Apps/Service/Payouts/AppPayoutQuestion.php, validate(), which
    AppPayoutAnswerHandler::validate() calls. MAX_REFERENCE is 191 and
    MAX_REASON 500."""
    outcome = answer.get("outcome")
    if outcome == "paid":
        reference = answer.get("reference")
        if not isinstance(reference, str) or reference.strip(_PHP_TRIM) == "":
            return 'A paid answer names the payment\'s reference in your system, as "reference".'
        if len(reference.strip(_PHP_TRIM)) > 191:
            return "A reference is at most 191 characters."
        return None
    if outcome == "failed":
        reason = answer.get("reason")
        if not isinstance(reason, str) or reason.strip(_PHP_TRIM) == "":
            return 'A failed answer says why in plain words, as "reason".'
        if len(reason.strip(_PHP_TRIM)) > 500:
            return "A reason is at most 500 characters."
        return None
    return 'The outcome is "paid" or "failed".'


_CHECKS: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Optional[str]]] = {
    "payoutMethod": _payout_method,
    "distributionResolver": _distribution_resolver,
    "incentiveResolver": _incentive_resolver,
    "incentiveType": _incentive_type,
    "metricCalculation": _calculation("scores"),
    "metricFilter": _filter,
    "transactionFilter": _transaction_filter,
    "engagementCalculation": _calculation("engagements"),
    "engagementFilter": _filter,
    "programGroupSorter": _program_group_sorter,
    "collaboratorGroupStructure": _collaborator_group_structure,
}


def siren_refusal(kind: str, question: Mapping[str, Any], answer: Mapping[str, Any]) -> Optional[str]:
    """The words Siren refuses an ask's answer with, as its 422
    ``app_answer_invalid`` sends them, or ``None`` when Siren's checks that
    need no records pass.

    lib/Apps/Service/Services/AppRequestService.php, answer(), runs the size
    limit before the kind's own check. A kind this SDK has no check for, such
    as one taken through ``extra_kinds``, passes here.
    """
    decoded = json.loads(json.dumps(answer))
    if len(_encoded(decoded).encode("utf-8")) > MAX_ANSWER_BYTES:
        return f"The answer is larger than {MAX_ANSWER_BYTES} bytes of JSON."
    check = _CHECKS.get(kind)
    if check is None:
        return None
    return check(dict(question), decoded)
