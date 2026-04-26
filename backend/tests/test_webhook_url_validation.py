import pytest

from app.services import webhook_service as wh


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://10.0.0.5/hook",
        "http://172.16.0.5/hook",
        "http://192.168.1.5/hook",
        "http://[::1]/hook",
    ],
)
def test_private_webhook_targets_are_blocked(url):
    with pytest.raises(ValueError):
        wh.validate_webhook_url(url)


def test_webhook_url_requires_http_or_https():
    with pytest.raises(ValueError):
        wh.validate_webhook_url("ftp://example.com/hook")


def test_public_webhook_target_is_allowed():
    assert wh.validate_webhook_url("https://example.com/hook") == "https://example.com/hook"
