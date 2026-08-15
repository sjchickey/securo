"""Payment tracking for credit-card transactions.

Marking charges paid/unpaid, linking the payment that settled them, filtering
by paid status, and the paid/unpaid split on the account summary.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.account_service import get_account_summary, get_unpaid_by_category
from app.services.transaction_service import (
    bulk_mark_paid,
    bulk_mark_unpaid,
    delete_transaction,
    get_payment_coverage,
    get_transactions,
)


@pytest_asyncio.fixture
async def card(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Cartão",
        type="credit_card",
        balance=Decimal("-500"),
        currency="BRL",
        statement_close_day=20,
        payment_due_day=28,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def other_card(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Outro Cartão",
        type="credit_card",
        balance=Decimal("-100"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def checking(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Corrente",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _charge(
    session: AsyncSession,
    test_user,
    test_workspace: Workspace,
    account: Account,
    *,
    amount: str = "100.00",
    tx_type: str = "debit",
    tx_date: date | None = None,
    is_paid: bool = False,
    category=None,
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Compra",
        amount=Decimal(amount),
        currency="BRL",
        date=tx_date or date.today(),
        effective_date=tx_date or date.today(),
        type=tx_type,
        source="manual",
        status="posted",
        is_paid=is_paid,
        category_id=category.id if category is not None else None,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


# --------------------------------------------------------------------------
# service: mark paid / unpaid
# --------------------------------------------------------------------------


async def test_mark_card_charge_paid(session, test_user, test_workspace, card):
    tx = await _charge(session, test_user, test_workspace, card)

    updated, skipped = await bulk_mark_paid(session, test_workspace.id, [tx.id])

    assert (updated, skipped) == (1, 0)
    await session.refresh(tx)
    assert tx.is_paid is True
    assert tx.paid_date is not None


async def test_mark_many_paid(session, test_user, test_workspace, card):
    txs = [await _charge(session, test_user, test_workspace, card) for _ in range(3)]

    updated, skipped = await bulk_mark_paid(session, test_workspace.id, [t.id for t in txs])

    assert (updated, skipped) == (3, 0)
    for tx in txs:
        await session.refresh(tx)
        assert tx.is_paid is True


async def test_mark_unpaid_clears_date_and_link(session, test_user, test_workspace, card):
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
    )

    updated, skipped = await bulk_mark_unpaid(session, test_workspace.id, [tx.id])

    assert (updated, skipped) == (1, 0)
    await session.refresh(tx)
    assert tx.is_paid is False
    assert tx.paid_date is None
    assert tx.covered_by_payment_id is None


async def test_empty_id_list_is_a_noop(session, test_workspace):
    assert await bulk_mark_paid(session, test_workspace.id, []) == (0, 0)
    assert await bulk_mark_unpaid(session, test_workspace.id, []) == (0, 0)


# --------------------------------------------------------------------------
# service: credit-card scoping
# --------------------------------------------------------------------------


async def test_non_card_transactions_are_skipped(
    session, test_user, test_workspace, card, checking
):
    card_tx = await _charge(session, test_user, test_workspace, card)
    checking_tx = await _charge(session, test_user, test_workspace, checking)

    updated, skipped = await bulk_mark_paid(
        session, test_workspace.id, [card_tx.id, checking_tx.id]
    )

    assert (updated, skipped) == (1, 1)
    await session.refresh(checking_tx)
    assert checking_tx.is_paid is False


async def test_unpaid_also_skips_non_card(session, test_user, test_workspace, checking):
    tx = await _charge(session, test_user, test_workspace, checking, is_paid=True)

    assert await bulk_mark_unpaid(session, test_workspace.id, [tx.id]) == (0, 1)


async def test_other_workspace_transactions_are_skipped(
    session, test_user, test_workspace, card
):
    tx = await _charge(session, test_user, test_workspace, card)
    stranger = uuid.uuid4()

    assert await bulk_mark_paid(session, stranger, [tx.id]) == (0, 1)
    await session.refresh(tx)
    assert tx.is_paid is False


# --------------------------------------------------------------------------
# service: the covering-payment link
# --------------------------------------------------------------------------


async def test_payment_link_is_recorded(session, test_user, test_workspace, card):
    payment = await _charge(
        session, test_user, test_workspace, card, amount="1000.00", tx_type="credit"
    )
    tx = await _charge(session, test_user, test_workspace, card, amount="500.00")

    updated, _ = await bulk_mark_paid(
        session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
    )

    assert updated == 1
    await session.refresh(tx)
    assert tx.is_paid is True
    assert tx.covered_by_payment_id == payment.id


async def test_marking_paid_again_keeps_an_existing_link(
    session, test_user, test_workspace, card
):
    """A plain "mark as paid" must not silently unlink the covering payment."""
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
    )

    await bulk_mark_paid(session, test_workspace.id, [tx.id])

    await session.refresh(tx)
    assert tx.covered_by_payment_id == payment.id


async def test_clearing_the_link_requires_asking_for_it(
    session, test_user, test_workspace, card
):
    """Omitting the id keeps the link; clear_payment_link drops it."""
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
    )

    await bulk_mark_paid(session, test_workspace.id, [tx.id], clear_payment_link=True)

    await session.refresh(tx)
    assert tx.is_paid is True
    assert tx.covered_by_payment_id is None


async def test_api_explicit_null_unlinks_the_payment(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
    )

    # Omitted → link survives.
    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={"transaction_ids": [str(tx.id)]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(tx)
    assert tx.covered_by_payment_id == payment.id

    # Explicit null → unlinked, still paid.
    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={"transaction_ids": [str(tx.id)], "covered_by_payment_id": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(tx)
    assert tx.is_paid is True
    assert tx.covered_by_payment_id is None


async def test_payment_from_another_account_is_rejected(
    session, test_user, test_workspace, card, other_card
):
    payment = await _charge(session, test_user, test_workspace, other_card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)

    with pytest.raises(ValueError, match="same account"):
        await bulk_mark_paid(
            session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
        )


async def test_payment_from_another_workspace_is_rejected(
    session, test_user, test_workspace, card
):
    tx = await _charge(session, test_user, test_workspace, card)
    foreign = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.uuid4(),
        account_id=card.id,
        description="Alheia",
        amount=Decimal("50"),
        currency="BRL",
        date=date.today(),
        effective_date=date.today(),
        type="credit",
        source="manual",
        status="posted",
    )
    session.add(foreign)
    await session.commit()

    with pytest.raises(ValueError, match="Payment transaction not found"):
        await bulk_mark_paid(
            session, test_workspace.id, [tx.id], covered_by_payment_id=foreign.id
        )


async def test_a_payment_cannot_cover_itself(session, test_user, test_workspace, card):
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")

    with pytest.raises(ValueError, match="cannot cover itself"):
        await bulk_mark_paid(
            session, test_workspace.id, [payment.id], covered_by_payment_id=payment.id
        )


async def test_non_card_payment_is_rejected(
    session, test_user, test_workspace, card, checking
):
    payment = await _charge(session, test_user, test_workspace, checking, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)

    with pytest.raises(ValueError, match="Payment transaction not found"):
        await bulk_mark_paid(
            session, test_workspace.id, [tx.id], covered_by_payment_id=payment.id
        )


async def test_custom_paid_date_is_persisted(session, test_user, test_workspace, card):
    tx = await _charge(session, test_user, test_workspace, card)
    when = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)

    await bulk_mark_paid(session, test_workspace.id, [tx.id], paid_date=when)

    await session.refresh(tx)
    stored = tx.paid_date
    if stored.tzinfo is None:  # SQLite drops the offset
        stored = stored.replace(tzinfo=timezone.utc)
    assert stored == when


# --------------------------------------------------------------------------
# service: filtering
# --------------------------------------------------------------------------


async def test_filter_by_paid_status(session, test_user, test_workspace, card):
    paid = await _charge(session, test_user, test_workspace, card, is_paid=True)
    unpaid = await _charge(session, test_user, test_workspace, card)

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, is_paid=True, skip_pagination=True,
    )
    assert [r.id for r in rows] == [paid.id]

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, is_paid=False, skip_pagination=True,
    )
    assert [r.id for r in rows] == [unpaid.id]


async def test_omitting_the_filter_returns_both(session, test_user, test_workspace, card):
    await _charge(session, test_user, test_workspace, card, is_paid=True)
    await _charge(session, test_user, test_workspace, card)

    rows, total, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, skip_pagination=True,
    )
    assert total == 2


@pytest.mark.parametrize("sort_key", ["paid", "is_paid"])
async def test_sort_by_paid(session, test_user, test_workspace, card, sort_key):
    """`paid` is the grid column id the UI sends; `is_paid` is the payload name."""
    await _charge(session, test_user, test_workspace, card, is_paid=True)
    await _charge(session, test_user, test_workspace, card)

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, sort_by=sort_key, sort_dir="desc", skip_pagination=True,
    )
    assert [r.is_paid for r in rows] == [True, False]


# --------------------------------------------------------------------------
# account summary: paid / unpaid split
# --------------------------------------------------------------------------


async def test_summary_splits_paid_and_unpaid(session, test_user, test_workspace, card):
    today = date.today()
    await _charge(session, test_user, test_workspace, card,
                  amount="100.00", tx_date=today, is_paid=True)
    await _charge(session, test_user, test_workspace, card,
                  amount="40.00", tx_date=today)

    summary = await get_account_summary(
        session, card.id, test_workspace.id,
        date_from=today.replace(day=1), date_to=today,
    )

    assert summary["paid_total"] == pytest.approx(100.0)
    assert summary["unpaid_total"] == pytest.approx(40.0)
    # The two halves must reconstruct the bill total the user sees.
    assert summary["paid_total"] + summary["unpaid_total"] == pytest.approx(
        summary["monthly_expenses"]
    )


async def test_summary_omits_the_split_for_non_card_accounts(
    session, test_user, test_workspace, checking
):
    today = date.today()
    await _charge(session, test_user, test_workspace, checking, tx_date=today)

    summary = await get_account_summary(
        session, checking.id, test_workspace.id,
        date_from=today.replace(day=1), date_to=today,
    )

    assert summary["paid_total"] is None
    assert summary["unpaid_total"] is None


async def test_summary_unpaid_drops_to_zero_once_paid(
    session, test_user, test_workspace, card
):
    today = date.today()
    tx = await _charge(session, test_user, test_workspace, card,
                       amount="75.00", tx_date=today)

    await bulk_mark_paid(session, test_workspace.id, [tx.id])

    summary = await get_account_summary(
        session, card.id, test_workspace.id,
        date_from=today.replace(day=1), date_to=today,
    )
    assert summary["unpaid_total"] == pytest.approx(0.0)
    assert summary["paid_total"] == pytest.approx(75.0)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


async def test_api_mark_paid_and_unpaid(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    tx = await _charge(session, test_user, test_workspace, card)

    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={"transaction_ids": [str(tx.id)]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1, "skipped": 0}

    resp = await client.patch(
        "/api/transactions/bulk-mark-unpaid",
        json={"transaction_ids": [str(tx.id)]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1, "skipped": 0}


async def test_api_reports_skipped_non_card_rows(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, checking
):
    tx = await _charge(session, test_user, test_workspace, checking)

    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={"transaction_ids": [str(tx.id)]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 0, "skipped": 1}


async def test_api_bad_payment_link_is_a_400(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card, other_card
):
    payment = await _charge(session, test_user, test_workspace, other_card, tx_type="credit")
    tx = await _charge(session, test_user, test_workspace, card)

    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={
            "transaction_ids": [str(tx.id)],
            "covered_by_payment_id": str(payment.id),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "same account" in resp.json()["detail"]


async def test_api_requires_auth(client: AsyncClient):
    resp = await client.patch(
        "/api/transactions/bulk-mark-paid",
        json={"transaction_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 401


async def test_api_list_filters_by_is_paid(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    paid = await _charge(session, test_user, test_workspace, card, is_paid=True)
    await _charge(session, test_user, test_workspace, card)

    resp = await client.get(
        f"/api/transactions?account_id={card.id}&is_paid=true", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [str(paid.id)]
    assert body["items"][0]["is_paid"] is True


# --------------------------------------------------------------------------
# payment coverage (both directions)
# --------------------------------------------------------------------------


async def test_coverage_resolves_both_directions(
    session, test_user, test_workspace, card
):
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    a = await _charge(session, test_user, test_workspace, card, amount="10.00")
    b = await _charge(session, test_user, test_workspace, card, amount="20.00")
    await bulk_mark_paid(
        session, test_workspace.id, [a.id, b.id], covered_by_payment_id=payment.id
    )

    # From a charge: the payment that settled it, and nothing under it.
    covered_by, covers = await get_payment_coverage(session, test_workspace.id, a.id)
    assert covered_by is not None and covered_by.id == payment.id
    assert covers == []

    # From the payment: every charge it settled, and nothing above it.
    covered_by, covers = await get_payment_coverage(session, test_workspace.id, payment.id)
    assert covered_by is None
    assert {tx.id for tx in covers} == {a.id, b.id}


async def test_coverage_is_empty_for_an_unlinked_transaction(
    session, test_user, test_workspace, card
):
    tx = await _charge(session, test_user, test_workspace, card)

    assert await get_payment_coverage(session, test_workspace.id, tx.id) == (None, [])


async def test_coverage_returns_none_outside_the_workspace(
    session, test_user, test_workspace, card
):
    tx = await _charge(session, test_user, test_workspace, card)

    assert await get_payment_coverage(session, uuid.uuid4(), tx.id) is None


async def test_api_payment_coverage(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    charge = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [charge.id], covered_by_payment_id=payment.id
    )

    resp = await client.get(
        f"/api/transactions/{charge.id}/payment-coverage", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["covered_by"]["id"] == str(payment.id)

    resp = await client.get(
        f"/api/transactions/{payment.id}/payment-coverage", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["covered_by"] is None
    assert [tx["id"] for tx in body["covers"]] == [str(charge.id)]


async def test_api_payment_coverage_404s_for_unknown_id(client: AsyncClient, auth_headers):
    resp = await client.get(
        f"/api/transactions/{uuid.uuid4()}/payment-coverage", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_deleting_the_payment_unlinks_but_keeps_paid(
    session, test_user, test_workspace, card
):
    """ON DELETE SET NULL: the charge stays settled, it just loses the link."""
    payment = await _charge(session, test_user, test_workspace, card, tx_type="credit")
    charge = await _charge(session, test_user, test_workspace, card)
    await bulk_mark_paid(
        session, test_workspace.id, [charge.id], covered_by_payment_id=payment.id
    )

    await delete_transaction(session, payment.id, test_workspace.id, test_user.id)

    await session.refresh(charge)
    assert charge.is_paid is True
    assert charge.covered_by_payment_id is None


# --------------------------------------------------------------------------
# unpaid by category
# --------------------------------------------------------------------------


async def test_unpaid_by_category_groups_and_sorts(
    session, test_user, test_workspace, card, test_categories
):
    food, transport = test_categories[0], test_categories[1]
    await _charge(session, test_user, test_workspace, card, amount="30.00", category=food)
    await _charge(session, test_user, test_workspace, card, amount="20.00", category=food)
    await _charge(session, test_user, test_workspace, card, amount="80.00", category=transport)

    rows = await get_unpaid_by_category(session, card.id, test_workspace.id)

    assert [(r["name"], r["total"], r["count"]) for r in rows] == [
        (transport.name, 80.0, 1),
        (food.name, 50.0, 2),
    ]


async def test_unpaid_by_category_excludes_paid_and_buckets_uncategorized(
    session, test_user, test_workspace, card, test_categories
):
    food = test_categories[0]
    await _charge(session, test_user, test_workspace, card, amount="30.00", category=food)
    await _charge(session, test_user, test_workspace, card, amount="15.00")  # no category
    paid = await _charge(session, test_user, test_workspace, card, amount="99.00", category=food)
    await bulk_mark_paid(session, test_workspace.id, [paid.id])

    rows = await get_unpaid_by_category(session, card.id, test_workspace.id)

    by_name = {r["name"]: r["total"] for r in rows}
    assert by_name == {food.name: 30.0, None: 15.0}


async def test_unpaid_by_category_ignores_the_statement_window(
    session, test_user, test_workspace, card, test_categories
):
    """The whole point: an unpaid charge from months ago still has to show."""
    food = test_categories[0]
    await _charge(session, test_user, test_workspace, card, amount="40.00",
                  tx_date=date(2026, 1, 5), category=food)
    await _charge(session, test_user, test_workspace, card, amount="10.00",
                  tx_date=date.today(), category=food)

    rows = await get_unpaid_by_category(session, card.id, test_workspace.id)

    assert [r["total"] for r in rows] == [50.0]


async def test_unpaid_by_category_nets_refunds(
    session, test_user, test_workspace, card, test_categories
):
    food = test_categories[0]
    await _charge(session, test_user, test_workspace, card, amount="50.00", category=food)
    await _charge(session, test_user, test_workspace, card, amount="20.00",
                  tx_type="credit", category=food)

    rows = await get_unpaid_by_category(session, card.id, test_workspace.id)

    assert [r["total"] for r in rows] == [30.0]


async def test_unpaid_by_category_is_empty_for_non_card_accounts(
    session, test_user, test_workspace, checking, test_categories
):
    await _charge(session, test_user, test_workspace, checking,
                  amount="30.00", category=test_categories[0])

    assert await get_unpaid_by_category(session, checking.id, test_workspace.id) == []


async def test_api_unpaid_by_category(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card, test_categories
):
    await _charge(session, test_user, test_workspace, card,
                  amount="30.00", category=test_categories[0])

    resp = await client.get(
        f"/api/accounts/{card.id}/unpaid-by-category", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == test_categories[0].name
    assert body[0]["total"] == 30.0
    assert body[0]["color"] == test_categories[0].color


async def test_api_unpaid_by_category_404s_for_unknown_account(
    client: AsyncClient, auth_headers
):
    resp = await client.get(
        f"/api/accounts/{uuid.uuid4()}/unpaid-by-category", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_api_export_includes_paid_columns(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    await _charge(session, test_user, test_workspace, card, is_paid=True)

    resp = await client.get(
        f"/api/transactions/export?account_id={card.id}&is_paid=true", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    header, row = resp.text.lstrip("﻿").splitlines()[:2]
    assert "is_paid" in header.split(",")
    assert "paid_date" in header.split(",")
    assert "true" in row


async def test_api_patch_cannot_set_paid_fields(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    """Payment tracking is not writable through the generic patch route."""
    tx = await _charge(session, test_user, test_workspace, card)

    resp = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"is_paid": True, "covered_by_payment_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(tx)
    assert tx.is_paid is False
    assert tx.covered_by_payment_id is None
