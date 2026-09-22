from decimal import Decimal

import pytest

from tallyguard.consolidation import transfer_amount


def test_consolidation_leaves_fee_reserve_and_rounds_down():
    assert transfer_amount(
        Decimal("20.1234567"),
        reserve=Decimal("0.005"),
        maximum=Decimal("50"),
    ) == Decimal("20.118456")


def test_consolidation_skips_balance_that_cannot_cover_reserve():
    assert transfer_amount(
        Decimal("0.005"),
        reserve=Decimal("0.005"),
        maximum=Decimal("50"),
    ) is None


def test_consolidation_refuses_source_above_operator_ceiling():
    with pytest.raises(ValueError, match="exceeds the consolidation ceiling"):
        transfer_amount(
            Decimal("50.01"),
            reserve=Decimal("0.005"),
            maximum=Decimal("50"),
        )
