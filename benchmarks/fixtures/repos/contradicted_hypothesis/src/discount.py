"""Discount calculation module."""


def apply_discount(amount: float, discount: float) -> float:
    if discount < 0 or discount > 1:
        raise ValueError("Invalid discount rate")
    return round(amount * (1.0 - discount), 2)
