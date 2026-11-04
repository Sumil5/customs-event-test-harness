import sys
from pathlib import Path

import pytest

from customs_harness.config import Settings
from customs_harness.generator.factory import DeclarationFactory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))  # lets the contract tests import the services' rules


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture
def factory() -> DeclarationFactory:
    return DeclarationFactory(seed=1234)
