"""Test exercising anonymous user role."""

from src.auth import get_user_role


def test_anonymous_user():
    assert get_user_role(None) == "guest"
