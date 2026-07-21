from __future__ import annotations

import pytest

# scraper imports buyee.auth, which imports the playwright package (not the
# browser binaries). Skip the whole module when playwright isn't installed so
# the default test run stays lightweight.
pytest.importorskip("playwright")

from buyee.scraper import _looks_like_login


LOGIN_PAGE = (
    "<html><head><title>Login /【Buyee】</title></head>"
    "<body>Home &gt; Login <form><input type=\"password\" name=\"pw\"></form>"
    "</body></html>"
)

ORDERS_PAGE = (
    "<html><head><title>My Baggages</title></head><body><table>"
    "<tr><td><a href=\"/mybaggages/detail/W2605289159\">baggage W2605289159</a>"
    "</td></tr></table></body></html>"
)


def test_looks_like_login_detects_expired_session_page():
    assert _looks_like_login(LOGIN_PAGE) is True


def test_looks_like_login_false_on_real_orders_page():
    assert _looks_like_login(ORDERS_PAGE) is False


def test_looks_like_login_ignores_password_field_when_baggage_present():
    # A logged-in page can contain a password field (e.g. an account-settings
    # link) — as long as real baggage content is present it is NOT the login wall.
    html = '<input type="password"> my baggage list'
    assert _looks_like_login(html) is False
