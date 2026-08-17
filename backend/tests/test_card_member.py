"""Cardholder tracking for cards with supplementary holders.

Amex reports who spent: a "Card Member" column in CSV, and inside the MEMO
in OFX/QFX as "MR SEAN HICKEY-41006".
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.import_service import _extract_card_member, parse_csv, parse_ofx
from app.services.transaction_service import get_card_members, get_transactions


@pytest_asyncio.fixture
async def card(session: AsyncSession, test_user) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Amex",
        type="credit_card",
        balance=Decimal("-100"),
        currency="GBP",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _charge(session, test_user, test_workspace, account, *, member=None, amount="10.00"):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Compra",
        amount=Decimal(amount),
        currency="GBP",
        date=date.today(),
        effective_date=date.today(),
        type="debit",
        source="manual",
        status="posted",
        card_member=member,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


# --------------------------------------------------------------------------
# memo parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("memo,expected", [
    ("MR SEAN HICKEY-41006", "MR SEAN HICKEY"),
    ("MRS NAOMI HICKEY-41014", "MRS NAOMI HICKEY"),
    ("MISS A O'BRIEN-1234", "MISS A O'BRIEN"),
    ("SEAN HICKEY-41006", "SEAN HICKEY"),
    ("  MR SEAN HICKEY - 41006  ", "MR SEAN HICKEY"),
    # Amex appends a spend tag to some rows.
    ("MR SEAN HICKEY-41006-GOODS", "MR SEAN HICKEY"),
    ("MRS NAOMI HICKEY-41014-SERVICES", "MRS NAOMI HICKEY"),
    ("MR SEAN HICKEY - 41006 - GOODS", "MR SEAN HICKEY"),
])
def test_cardholder_memos_are_recognised(memo, expected):
    assert _extract_card_member(memo) == expected


@pytest.mark.parametrize("memo", [
    None,
    "",
    "TESCO STORES 3456",
    "AMAZON UK RETAIL",
    # Long digit runs are references, not card numbers.
    "PAYPAL *SPOTIFY-1234567890",
    # No trailing card digits — too weak a signal to claim as a cardholder.
    "MR SEAN HICKEY",
    "Uber   *Trip-help.uber.com",
])
def test_merchant_memos_are_left_alone(memo):
    """A false positive here would blank out a real description, so the
    pattern has to stay strict."""
    assert _extract_card_member(memo) is None


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------


def test_csv_card_member_column_is_detected():
    content = (
        "Date,Description,Amount,Card Member\n"
        "01/03/2026,TESCO,12.50,MR SEAN HICKEY\n"
        "02/03/2026,BOOTS,8.00,MRS NAOMI HICKEY\n"
    ).encode()

    rows = parse_csv(content, date_format="DD/MM/YYYY")

    assert [r.card_member for r in rows] == ["MR SEAN HICKEY", "MRS NAOMI HICKEY"]
    # The cardholder must not leak into the description.
    assert [r.description for r in rows] == ["TESCO", "BOOTS"]


def test_csv_without_a_cardholder_column_is_unaffected():
    content = b"Date,Description,Amount\n01/03/2026,TESCO,12.50\n"

    rows = parse_csv(content, date_format="DD/MM/YYYY")

    assert rows[0].card_member is None


# --------------------------------------------------------------------------
# filtering
# --------------------------------------------------------------------------


async def test_filter_by_cardholder(session, test_user, test_workspace, card):
    mine = await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")
    await _charge(session, test_user, test_workspace, card, member="MRS NAOMI HICKEY")

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, card_member="MR SEAN HICKEY", skip_pagination=True,
    )

    assert [r.id for r in rows] == [mine.id]


async def test_distinct_cardholders_are_listed(session, test_user, test_workspace, card):
    await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")
    await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")
    await _charge(session, test_user, test_workspace, card, member="MRS NAOMI HICKEY")
    await _charge(session, test_user, test_workspace, card)

    assert await get_card_members(session, test_workspace.id) == [
        "MR SEAN HICKEY", "MRS NAOMI HICKEY",
    ]


async def test_cardholders_do_not_leak_across_workspaces(
    session, test_user, test_workspace, card
):
    await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")

    assert await get_card_members(session, uuid.uuid4()) == []


@pytest.mark.parametrize("sort_key", ["cardMember", "card_member"])
async def test_sort_by_cardholder(session, test_user, test_workspace, card, sort_key):
    """`cardMember` is the grid column id; `card_member` is the payload name."""
    await _charge(session, test_user, test_workspace, card, member="MRS NAOMI HICKEY")
    await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")

    rows, _, _ = await get_transactions(
        session, test_workspace.id, test_user.id,
        account_id=card.id, sort_by=sort_key, sort_dir="asc", skip_pagination=True,
    )

    assert [r.card_member for r in rows] == ["MR SEAN HICKEY", "MRS NAOMI HICKEY"]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


async def test_api_lists_and_filters_by_cardholder(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    await _charge(session, test_user, test_workspace, card, member="MR SEAN HICKEY")
    await _charge(session, test_user, test_workspace, card, member="MRS NAOMI HICKEY")

    resp = await client.get("/api/transactions/card-members", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == ["MR SEAN HICKEY", "MRS NAOMI HICKEY"]

    resp = await client.get(
        "/api/transactions?card_member=MRS+NAOMI+HICKEY", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["card_member"] == "MRS NAOMI HICKEY"


async def test_api_import_persists_the_cardholder(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    """The import round trip rebuilds the payload field by field, so the
    cardholder has to survive preview -> confirm, not just parsing."""
    resp = await client.post(
        "/api/transactions/import",
        json={
            "account_id": str(card.id),
            "filename": "amex.qfx",
            "detected_format": "ofx",
            "detect_duplicates": False,
            "transactions": [{
                "description": "TESCO STORES",
                "amount": "12.50",
                "date": "2026-03-05",
                "type": "debit",
                "card_member": "MRS NAOMI HICKEY",
            }],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["imported"] == 1

    members = await get_card_members(session, test_workspace.id)
    assert members == ["MRS NAOMI HICKEY"]


async def test_api_can_set_cardholder_by_hand(
    client: AsyncClient, auth_headers, session, test_user, test_workspace, card
):
    """Manually entered charges have no import to inherit it from."""
    tx = await _charge(session, test_user, test_workspace, card)

    resp = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"card_member": "MR SEAN HICKEY"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["card_member"] == "MR SEAN HICKEY"


# --------------------------------------------------------------------------
# OFX / QFX import
# --------------------------------------------------------------------------


def _make_ofx(transactions_sgml: str) -> bytes:
    """Helper to wrap transaction SGML in a valid OFX structure."""
    return (
        "OFXHEADER:100\n"
        "DATA:OFXSGML\n"
        "VERSION:102\n"
        "SECURITY:NONE\n"
        "ENCODING:USASCII\n"
        "CHARSET:1252\n"
        "COMPRESSION:NONE\n"
        "OLDFILEUID:NONE\n"
        "NEWFILEUID:NONE\n"
        "\n"
        "<OFX>\n"
        "<SIGNONMSGSRSV1>\n"
        "<SONRS>\n"
        "<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        "<DTSERVER>20260101\n"
        "<LANGUAGE>POR\n"
        "</SONRS>\n"
        "</SIGNONMSGSRSV1>\n"
        "<BANKMSGSRSV1>\n"
        "<STMTTRNRS>\n"
        "<TRNUID>1001\n"
        "<STATUS><CODE>0<SEVERITY>INFO</STATUS>\n"
        "<STMTRS>\n"
        "<CURDEF>BRL\n"
        "<BANKACCTFROM>\n"
        "<BANKID>0001\n"
        "<ACCTID>12345\n"
        "<ACCTTYPE>CHECKING\n"
        "</BANKACCTFROM>\n"
        "<BANKTRANLIST>\n"
        "<DTSTART>20260101\n"
        "<DTEND>20260131\n"
        f"{transactions_sgml}\n"
        "</BANKTRANLIST>\n"
        "</STMTRS>\n"
        "</STMTTRNRS>\n"
        "</BANKMSGSRSV1>\n"
        "</OFX>\n"
    ).encode("ascii")


def _ofx_txn(name: str, memo: str) -> str:
    return (
        "<STMTTRN>\n"
        "<TRNTYPE>DEBIT\n"
        "<DTPOSTED>20260305\n"
        "<TRNAMT>-12.50\n"
        "<FITID>TX-1\n"
        f"<NAME>{name}\n"
        f"<MEMO>{memo}\n"
        "</STMTTRN>"
    )


def test_ofx_memo_cardholder_is_lifted_out_of_the_description():
    """Amex puts the merchant in NAME and the cardholder in MEMO. The parser
    prefers MEMO for the description, so without this every Amex row would be
    named after the person who spent rather than what they bought."""
    content = _make_ofx(_ofx_txn("TESCO STORES 3456", "MR SEAN HICKEY-41006"))

    rows = parse_ofx(content)

    assert len(rows) == 1
    assert rows[0].card_member == "MR SEAN HICKEY"
    assert rows[0].description == "TESCO STORES 3456"


def test_ofx_ordinary_memo_still_wins_the_description():
    """Other banks put the real description in MEMO — that must not regress."""
    content = _make_ofx(_ofx_txn("CARD PURCHASE", "CONTACTLESS TESCO EXPRESS"))

    rows = parse_ofx(content)

    assert rows[0].card_member is None
    assert rows[0].description == "CONTACTLESS TESCO EXPRESS"


def test_ofx_cardholder_memo_without_a_payee_keeps_the_memo():
    """With no NAME to fall back on, dropping the memo would leave the row
    with no description at all."""
    content = _make_ofx(
        "<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>20260305\n<TRNAMT>-12.50\n"
        "<FITID>TX-2\n<MEMO>MR SEAN HICKEY-41006\n</STMTTRN>"
    )

    rows = parse_ofx(content)

    assert rows[0].card_member == "MR SEAN HICKEY"
    assert rows[0].description == "MR SEAN HICKEY-41006"
