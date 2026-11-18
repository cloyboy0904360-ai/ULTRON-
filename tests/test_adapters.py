import pytest

from ultron.adapters.asana import AsanaAdapter
from ultron.adapters.base import AdapterStatus, BaseAdapter
from ultron.adapters.shopify import ShopifyAdapter
from ultron.errors import (
    IntegrationError,
    IntegrationUnavailableError,
    ValidationError,
)


def scripted_transport(responses):
    """Transport returning queued (status, payload) responses."""
    calls = []
    queue = list(responses)

    def transport(method, url, headers, body, timeout):
        calls.append({"method": method, "url": url, "headers": headers})
        if not queue:
            raise AssertionError(f"Unexpected request: {method} {url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    transport.calls = calls
    return transport


class DummyAdapter(BaseAdapter):
    name = "dummy"

    def health_check(self):
        return self._request("GET", "https://x/health", action="health_check")


def test_unconfigured_adapter_is_blocked():
    adapter = DummyAdapter(token=None)
    assert adapter.status()["status"] == AdapterStatus.BLOCKED.value
    with pytest.raises(IntegrationUnavailableError):
        adapter.health_check()


def test_success_records_last_operation_and_evidence(db):
    adapter = DummyAdapter(
        token="t", transport=scripted_transport([(200, {"ok": True})]), db=db
    )
    adapter.health_check()
    assert adapter.last_successful_operation["action"] == "health_check"
    evidence = db.query_all("SELECT * FROM integration_evidence")
    assert evidence[0]["outcome"] == "success"


def test_retries_on_429_then_succeeds():
    adapter = DummyAdapter(
        token="t",
        transport=scripted_transport(
            [(429, {"_retry_after": "0"}), (200, {"ok": True})]
        ),
    )
    adapter._sleep_before_retry = lambda *_: None  # keep tests fast
    assert adapter.health_check() == {"ok": True}


def test_retries_on_500_then_fails_structured():
    adapter = DummyAdapter(
        token="t", max_retries=2,
        transport=scripted_transport([(500, {"message": "boom"}),
                                      (500, {"message": "boom"}),
                                      (500, {"message": "boom"})]),
    )
    adapter._sleep_before_retry = lambda *_: None
    with pytest.raises(IntegrationError) as exc:
        adapter.health_check()
    assert exc.value.status_code == 500
    assert exc.value.retryable
    assert adapter.last_failed_operation["action"] == "health_check"


def test_401_is_not_retried():
    adapter = DummyAdapter(
        token="t", transport=scripted_transport([(401, {"message": "bad token"})])
    )
    adapter._sleep_before_retry = lambda *_: None
    with pytest.raises(IntegrationError) as exc:
        adapter.health_check()
    assert not exc.value.retryable


def test_asana_task_creation_validates_gid():
    responses = [
        (200, {"data": {"gid": "ws1", "name": "WS"}}),   # verify workspace
        (201, {"data": {"gid": "task1", "permalink_url": "https://app.asana.com/0/0/task1"}}),
    ]
    adapter = AsanaAdapter(
        token="t", workspace_gid="ws1", transport=scripted_transport(responses)
    )
    task = adapter.create_task(name="n", notes="x")
    assert task["gid"] == "task1"


def test_asana_task_creation_rejects_missing_gid():
    responses = [
        (200, {"data": {"gid": "ws1"}}),
        (201, {"data": {}}),
    ]
    adapter = AsanaAdapter(
        token="t", workspace_gid="ws1", transport=scripted_transport(responses)
    )
    with pytest.raises(IntegrationError):
        adapter.create_task(name="n", notes="x")


def test_asana_workspace_verification_mismatch():
    adapter = AsanaAdapter(
        token="t", workspace_gid="ws1",
        transport=scripted_transport([(200, {"data": {"gid": "other"}})]),
    )
    with pytest.raises(IntegrationError):
        adapter.verify_workspace()


def test_shopify_find_variant_by_sku():
    responses = [
        (200, {"products": [
            {"id": 1, "variants": [{"id": 11, "sku": "ABC"}]},
            {"id": 2, "variants": [{"id": 22, "sku": "DEF"}]},
        ]}),
    ]
    adapter = ShopifyAdapter(
        token="t", shop="example", transport=scripted_transport(responses)
    )
    assert adapter.find_variant_by_sku("DEF")["id"] == 22


def test_shopify_find_variant_by_sku_not_found():
    adapter = ShopifyAdapter(
        token="t", shop="example",
        transport=scripted_transport([(200, {"products": []})]),
    )
    assert adapter.find_variant_by_sku("NOPE") is None


def test_shopify_requires_shop():
    adapter = ShopifyAdapter(token="t")
    with pytest.raises(ValidationError):
        adapter.verify_store()


def test_logging_redacts_secrets():
    from ultron.logging_config import redact

    cleaned = redact({
        "access_token": "ghp_secretvalue123456789012345",
        "note": "Bearer abcdef123456",
        "nested": [{"stripe": "sk_live_abcdefghijklmnop"}],
        "safe": "hello",
    })
    assert cleaned["access_token"] == "***REDACTED***"
    assert "abcdef123456" not in cleaned["note"]
    assert "REDACTED" in cleaned["note"]
    assert cleaned["nested"][0]["stripe"] == "***REDACTED***"
    assert cleaned["safe"] == "hello"
