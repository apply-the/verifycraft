"""Smoke tests ensuring VerifyCraft core imports and runs cleanly."""

import pytest

import verifycraft
from verifycraft import __version__
from verifycraft.cli import main


def test_package_version() -> None:
    assert __version__ == "0.1.0"
    assert verifycraft.__version__ == "0.1.0"


def test_cli_execution() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
