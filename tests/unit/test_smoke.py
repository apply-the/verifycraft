"""Smoke tests ensuring VerifyCraft core imports cleanly."""

import verifycraft
from verifycraft import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0a1"
    assert verifycraft.__version__ == "0.1.0a1"
