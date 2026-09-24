from __future__ import annotations

import copy
from pathlib import Path

import pytest

from risk_audit.checks.registry import build_registry
from risk_audit.configuration.loader import load_pack


PROJECT = Path(__file__).resolve().parents[2]
AUDITOR = PROJECT / "审核器"


@pytest.fixture
def project_root() -> Path:
    return PROJECT


@pytest.fixture
def pack() -> dict:
    value = load_pack(AUDITOR / "rulepacks/releases/1.0.0")
    value["manifest"] = {**value["manifest"], "status": "draft"}
    return copy.deepcopy(value)


@pytest.fixture
def registry():
    return build_registry()

