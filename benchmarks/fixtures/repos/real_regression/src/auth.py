"""Regression fixture: missing NoneType check in user role retrieval."""


def get_user_role(user: dict | None) -> str:
    # Regression: accessing subscript on None without null check
    return user["role"]
