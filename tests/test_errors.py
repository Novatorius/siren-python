"""Error mapping by HTTP status, and error payload extraction."""

from __future__ import annotations

import pytest

import siren
from tests.conftest import API_KEY


def _no_retry_client() -> siren.Siren:
    return siren.Siren(api_key=API_KEY, max_retries=0)


@pytest.mark.parametrize(
    ("status", "error_cls"),
    [
        (400, siren.BadRequestError),
        (401, siren.AuthenticationError),
        (403, siren.PermissionError),
        (404, siren.NotFoundError),
        (409, siren.ConflictError),
        (422, siren.ValidationError),
        (429, siren.RateLimitError),
        (500, siren.ApiError),
        (502, siren.ApiError),
        (503, siren.ApiError),
    ],
)
def test_status_maps_to_error_class(mocked_api, status, error_cls):
    mocked_api.get("/conversions").respond(
        status, json={"error": {"message": "nope", "code": "some_code"}}
    )

    with pytest.raises(error_cls) as excinfo:
        _no_retry_client().conversions.list()

    error = excinfo.value
    assert isinstance(error, siren.SirenError)
    assert error.status_code == status
    assert error.message == "nope"
    assert error.code == "some_code"


def test_string_error_body_maps_message_and_null_code(mocked_api):
    # The common form: {"error": "<plain string message>"}
    mocked_api.get("/conversions").respond(404, json={"error": "Not found."})

    with pytest.raises(siren.NotFoundError) as excinfo:
        _no_retry_client().conversions.list()

    assert excinfo.value.message == "Not found."
    assert excinfo.value.code is None
    assert excinfo.value.data is None
    assert excinfo.value.status_code == 404


def test_object_error_body_maps_message_code_and_data(mocked_api):
    # The structured form (login/entitlement): {"error": {...}}
    mocked_api.get("/conversions").respond(
        403,
        json={"error": {"message": "Upgrade required.", "code": "entitlement_required",
                        "data": {"tier": "plus"}}},
    )

    with pytest.raises(siren.PermissionError) as excinfo:
        _no_retry_client().conversions.list()

    assert excinfo.value.message == "Upgrade required."
    assert excinfo.value.code == "entitlement_required"
    assert excinfo.value.data == {"tier": "plus"}


def test_string_error_validation_has_empty_field_errors(mocked_api):
    mocked_api.post("/event/sale").respond(422, json={"error": "Invalid."})

    with pytest.raises(siren.ValidationError) as excinfo:
        _no_retry_client().events.sale(
            source="s", external_id="x", total=1.0, tracking_id=1
        )

    assert excinfo.value.message == "Invalid."
    assert excinfo.value.field_errors == {}  # only the object form carries field errors


def test_missing_error_key_falls_back_to_status_text(mocked_api):
    mocked_api.get("/conversions").respond(500, json={"unexpected": "shape"})

    with pytest.raises(siren.ApiError) as excinfo:
        _no_retry_client().conversions.list()

    assert "500" in excinfo.value.message
    assert excinfo.value.code is None


def test_validation_error_exposes_field_errors(mocked_api):
    field_errors = {"trackingId": ["trackingId is required"]}
    mocked_api.post("/event/sale").respond(
        422,
        json={
            "error": {
                "message": "Validation failed.",
                "code": "validation_failed",
                "data": field_errors,
            }
        },
    )

    with pytest.raises(siren.ValidationError) as excinfo:
        _no_retry_client().events.sale(
            source="stripe", external_id="x", total=1.0, tracking_id=0
        )

    assert excinfo.value.field_errors == field_errors
    assert excinfo.value.data == field_errors


def test_rate_limit_error_exposes_retry_after(mocked_api):
    mocked_api.get("/conversions").respond(
        429,
        headers={"Retry-After": "17"},
        json={"error": {"message": "Too many requests", "code": "rate_limited"}},
    )

    with pytest.raises(siren.RateLimitError) as excinfo:
        _no_retry_client().conversions.list()

    assert excinfo.value.retry_after == 17


def test_rate_limit_error_without_retry_after_header(mocked_api):
    mocked_api.get("/conversions").respond(
        429, json={"error": {"message": "Too many requests"}}
    )

    with pytest.raises(siren.RateLimitError) as excinfo:
        _no_retry_client().conversions.list()

    assert excinfo.value.retry_after is None


def test_non_json_error_body_degrades_gracefully(mocked_api):
    mocked_api.get("/conversions").respond(500, text="<html>Bad Gateway</html>")

    with pytest.raises(siren.ApiError) as excinfo:
        _no_retry_client().conversions.list()

    assert excinfo.value.status_code == 500
    assert "500" in excinfo.value.message
    assert excinfo.value.code is None


def test_signature_verification_error_is_siren_error():
    assert issubclass(siren.SignatureVerificationError, siren.SirenError)


def test_every_http_error_subclasses_siren_error():
    for error_cls in (
        siren.BadRequestError,
        siren.AuthenticationError,
        siren.PermissionError,
        siren.NotFoundError,
        siren.ConflictError,
        siren.ValidationError,
        siren.RateLimitError,
        siren.ApiError,
        siren.ConnectionError,
    ):
        assert issubclass(error_cls, siren.SirenError)
