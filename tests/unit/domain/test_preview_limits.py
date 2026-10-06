"""Exact admission boundaries for the shared preview budget."""

import pytest

from aws_tui.domain.preview_limits import (
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_RANGE_BYTES,
    PreviewBudget,
    PreviewLimitExceeded,
    PreviewRequestKind,
)

pytestmark = pytest.mark.unit


def test_budget_reserves_final_validation():
    budget = PreviewBudget.start(clock=lambda: 0.0)
    budget.charge_request(kind=PreviewRequestKind.OPEN)
    for _ in range(30):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=1)
    with pytest.raises(PreviewLimitExceeded, match="request budget"):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=1)
    budget.charge_request(kind=PreviewRequestKind.VALIDATE)
    assert budget.requests == 32
    assert budget.bytes_requested == 30


def test_bytes_at_limit_and_plus_one():
    budget = PreviewBudget.start(clock=lambda: 0.0)
    for _ in range(PREVIEW_MAX_BYTES // PREVIEW_MAX_RANGE_BYTES):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=PREVIEW_MAX_RANGE_BYTES)
    with pytest.raises(PreviewLimitExceeded, match="byte budget"):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=1)
    assert budget.bytes_requested == PREVIEW_MAX_BYTES


@pytest.mark.parametrize("length", [-1, PREVIEW_MAX_RANGE_BYTES + 1])
def test_invalid_range_not_charged(length):
    budget = PreviewBudget.start(clock=lambda: 0.0)
    with pytest.raises(PreviewLimitExceeded, match="range"):
        budget.charge_request(kind=PreviewRequestKind.RANGE, length=length)
    assert budget.requests == 0


def test_deadline_boundary():
    now = [0.0]
    budget = PreviewBudget.start(clock=lambda: now[0])
    now[0] = 4.999
    assert budget.remaining_seconds() > 0
    now[0] = 5.0
    with pytest.raises(PreviewLimitExceeded, match="timed out"):
        budget.check()
    now[0] = 5.001
    with pytest.raises(PreviewLimitExceeded, match="timed out"):
        budget.charge_request(kind=PreviewRequestKind.VALIDATE)
    assert budget.requests == 0


def test_only_ranges_charge_bytes():
    budget = PreviewBudget.start(clock=lambda: 0.0)
    with pytest.raises(ValueError, match="only range"):
        budget.charge_request(kind=PreviewRequestKind.OPEN, length=1)
