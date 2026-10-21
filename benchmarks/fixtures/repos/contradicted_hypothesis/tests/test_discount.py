"""Test exercising discount on float values."""

from src.discount import apply_discount


def test_float_discount():
    assert apply_discount(100.0, 0.25) == 75.0
