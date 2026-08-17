"""Merging an imported transaction into a hand-entered stand-in.

Card payments take days to reach the download, so they get entered by hand to
keep the balance honest — and charges get tagged against them. When the real
one imports it must upgrade that row rather than insert beside it.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions
from app.services.transaction_service import bulk_mark_paid


@pytest_asyncio.fixture
async def card(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Amex", type="credit_card",
        balance=Decimal("-500"), currency="GBP",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _manual(session, test_user, test_workspace, account, *,
                  amount="200.00", tx_type="credit", tx_date=None, description="Amex payment"):
    tx = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account.id, description=description, amount=Decimal(amount),
        currency="GBP", date=tx_date or date.today(), effective_date=tx_date or date.today(),
        type=tx_type, source="manual", status="posted",
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


def _incoming(*, amount="200.00", tx_type="credit", tx_date=None,
              description="PAYMENT RECEIVED - THANK YOU", external_id="FIT-1"):
    return TransactionImport(
        description=description, amount=Decimal(amount), date=tx_date or date.today(),
        type=tx_type, external_id=external_id, currency="GBP",
    )


async def _run(session, test_workspace, test_user, card, rows):
    return await import_transactions(
        session, test_workspace.id, test_user.id, card.id, rows, "import",
        filename="amex.qfx", detected_format="ofx",
    )


async def _count(session, card) -> int:
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == card.id)
    )
    return len(result.scalars().all())


async def test_import_upgrades_the_manual_stand_in(
    session, test_user, test_workspace, card
):
    manual = await _manual(session, test_user, test_workspace, card)

    imported, skipped, excluded, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming()]
    )

    assert (merged, imported) == (1, 0)
    assert await _count(session, card) == 1, "must upgrade in place, not insert"
    await session.refresh(manual)
    assert manual.external_id == "FIT-1"
    assert manual.source == "import"
    # The description you chose is kept — the bank's wording is usually worse.
    assert manual.description == "Amex payment"


async def test_the_tagging_survives_the_merge(
    session, test_user, test_workspace, card
):
    """The whole point: charges tagged against the manual payment must still
    point at it afterwards, rather than being stranded by a second row."""
    payment = await _manual(session, test_user, test_workspace, card)
    charge = await _manual(
        session, test_user, test_workspace, card,
        amount="75.00", tx_type="debit", description="TESCO",
    )
    await bulk_mark_paid(
        session, test_workspace.id, [charge.id], covered_by_payment_id=payment.id
    )

    await _run(session, test_workspace, test_user, card, [_incoming()])

    await session.refresh(charge)
    assert charge.is_paid is True
    assert charge.covered_by_payment_id == payment.id


async def test_merge_tolerates_the_bank_posting_a_few_days_later(
    session, test_user, test_workspace, card
):
    entered = date.today() - timedelta(days=3)
    manual = await _manual(session, test_user, test_workspace, card, tx_date=entered)

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming(tx_date=date.today())]
    )

    assert merged == 1
    await session.refresh(manual)
    # The date stays put so the row can't jump to a different statement.
    assert manual.date == entered


async def test_a_distant_manual_row_is_not_claimed(
    session, test_user, test_workspace, card
):
    await _manual(session, test_user, test_workspace, card,
                  tx_date=date.today() - timedelta(days=30))

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming(tx_date=date.today())]
    )

    assert merged == 0
    assert await _count(session, card) == 2


@pytest.mark.parametrize("amount,tx_type", [("199.99", "credit"), ("200.00", "debit")])
async def test_a_different_amount_or_direction_is_not_claimed(
    session, test_user, test_workspace, card, amount, tx_type
):
    await _manual(session, test_user, test_workspace, card, amount=amount, tx_type=tx_type)

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming()]
    )

    assert merged == 0
    assert await _count(session, card) == 2


async def test_a_stand_in_is_claimed_only_once(
    session, test_user, test_workspace, card
):
    """Two identical payments in one file must not both claim one manual row."""
    await _manual(session, test_user, test_workspace, card)

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card,
        [_incoming(external_id="FIT-1"), _incoming(external_id="FIT-2")],
    )

    assert merged == 1
    assert await _count(session, card) == 2


async def test_an_already_imported_row_is_not_claimed(
    session, test_user, test_workspace, card
):
    """Only rows still lacking a bank id are stand-ins; a previously imported
    transaction must fall through to ordinary duplicate detection."""
    tx = await _manual(session, test_user, test_workspace, card)
    tx.source = "import"
    tx.external_id = "FIT-OLD"
    await session.commit()

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming(external_id="FIT-NEW")]
    )

    assert merged == 0
    assert await _count(session, card) == 2


async def test_an_ignored_stand_in_is_left_alone(
    session, test_user, test_workspace, card
):
    tx = await _manual(session, test_user, test_workspace, card)
    tx.is_ignored = True
    await session.commit()

    _, _, _, merged, _ = await _run(
        session, test_workspace, test_user, card, [_incoming()]
    )

    assert merged == 0
