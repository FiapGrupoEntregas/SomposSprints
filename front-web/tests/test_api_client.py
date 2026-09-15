import httpx

from services.api_client import ApiClient


def test_health_calls_versioned_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health"
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "environment": "test"})

    client = ApiClient(base_url="http://api.test", transport=httpx.MockTransport(handler))

    assert client.health()["status"] == "ok"
