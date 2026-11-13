"""Parser tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from custom_components.oclc_open import parser

from .conftest import load_fixture


def test_real_account_without_loans() -> None:
    """Captured Iserlohn page: no loans, two paid fees."""
    soup = parser.soup_of(load_fixture("account_fees_only.html"))
    assert parser.is_account_page(soup)
    data = parser.parse_account(soup)

    assert data.loans == []
    assert data.reservations == []
    assert [fee.title for fee in data.fees] == ["Testlied Eins", "Testbuch Vier"]
    assert data.fees[0].author == "Muster, Max"
    assert data.fees[0].media_type == "Tonie"
    assert data.fees[0].amount == Decimal("0.50")
    assert data.fees[0].reason.startswith("Säumnisg.")
    assert data.fees_open == Decimal("5.50")
    assert data.fees_paid == Decimal("5.50")
    assert data.balance == Decimal("0.00")
    assert data.amount_owed == Decimal(0)
    assert data.valid_until == date(2040, 12, 31)
    # The renewal popup's warning is hidden and must not be reported.
    assert data.warning is None
    assert parser.site_name(soup) == "Iserlohn"
    assert parser.open_version(soup) == (11, 1, 0, 10)
    assert parser.logoff_url(soup, "https://opac.iserlohn.de/Mein-Konto") == (
        "http://opac.iserlohn.de/Mein-Konto/ctl/Logoff"
    )


def test_real_loans() -> None:
    """Captured Iserlohn loans table (test titles); renewability comes from the service."""
    soup = parser.soup_of(load_fixture("account_loans.html"))
    data = parser.parse_account(soup)

    assert [loan.title for loan in data.loans] == ["Testbuch Eins", "Testbuch Zwei", "Test-CD Drei"]
    first = data.loans[0]
    assert first.copy_id == "0100001001"
    assert first.media_id == "0100001"
    assert first.author == "Muster, Anna"
    assert first.media_type == "Buch"
    assert first.branch == "Hauptstelle Iserlohn"
    assert first.due_date == date(2026, 12, 4)
    assert first.renewable is None
    assert first.extra == {}
    assert data.loans[2].due_date == date(2026, 12, 11)

    names = [name for name, _ in parser.renew_fields(soup, ["0100002002"])]
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl03$chkSelect" in names
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl02$chkSelect" not in names
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$BtnExtendMediumsBottom" in names
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$BtnExtendMediums" not in names


def test_synthetic_loans_and_reservations() -> None:
    """Loans/reservations layout as handled by opacclient."""
    soup = parser.soup_of(load_fixture("account_loans_synthetic.html"))
    data = parser.parse_account(soup)

    assert len(data.loans) == 2
    first = data.loans[0]
    assert first.title == "Testlied Eins"
    assert first.copy_id == "M1001"
    assert first.media_id == "0100011"
    assert first.author == "Muster, Max"
    assert first.media_type == "Tonie"
    assert first.branch == "Stadtbücherei"
    assert first.due_date == date(2026, 10, 9)
    assert first.extra == {"Verlängerungen": "1"}
    assert first.select_name.endswith("ctl02$chkSelect")

    pending = [r for r in data.reservations if not r.ready]
    ready = [r for r in data.reservations if r.ready]
    assert [r.title for r in pending] == ["Testbuch Vormerkung"]
    assert pending[0].media_id == "0100013"
    assert pending[0].extra == {"Vormerkdatum": "01.10.2026"}
    assert [r.title for r in ready] == ["Testbuch Abholung"]
    assert ready[0].pickup_deadline == date(2026, 10, 13)
    assert ready[0].branch == "Stadtbücherei"

    assert data.amount_owed == Decimal("2.00")
    assert data.warning == "Ihr Ausweis läuft bald ab."


def test_login_fields() -> None:
    """Login form is filled and only the login button is submitted."""
    soup = parser.soup_of(load_fixture("login.html"))
    assert not parser.is_account_page(soup)
    fields = dict(parser.login_fields(soup, "12345678", "01.02.1990"))
    assert fields["dnn$ctr378$Login$Login_COP$txtUsername"] == "12345678"
    assert fields["dnn$ctr378$Login$Login_COP$txtPassword"] == "01.02.1990"
    assert fields["dnn$ctr378$Login$Login_COP$cmdLogin"] == "Anmeldung"
    assert fields["__VIEWSTATE"] == "VS"
    assert "dnn$cookielaw$popupCookieDetailsDialog$btnDefault" not in fields


def test_login_error() -> None:
    """Validation summary is reported."""
    soup = parser.soup_of(
        '<form><div class="dnnFormMessage dnnFormValidationSummary">'
        "Anmeldung fehlgeschlagen</div></form>"
    )
    assert parser.login_error(soup) == "Anmeldung fehlgeschlagen"


def test_renew_fields_selects_requested_copy() -> None:
    """Only the requested checkbox is ticked and the renew button is pressed."""
    soup = parser.soup_of(load_fixture("account_loans_synthetic.html"))
    fields = parser.renew_fields(soup, ["M1002"])
    names = [name for name, _ in fields]
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl03$chkSelect" in names
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$grdViewLoans$ctl02$chkSelect" not in names
    assert "dnn$ctr379$MainView$tpnlLoans$ucLoansView$BtnExtendMediumsBottom" in names

    with pytest.raises(parser.ParseError):
        parser.renew_fields(soup, ["unknown"])


def test_extension_check() -> None:
    """Web service parameters come from the page."""
    soup = parser.soup_of(load_fixture("account_loans_synthetic.html"))
    check = parser.extension_check(soup, "https://opac.iserlohn.de/Mein-Konto", "card")
    assert check.url == (
        "https://opac.iserlohn.de/DesktopModules/OCLC.OPEN.PL.DNN.PatronAccountModule/"
        "PatronAccountService.asmx/IsCatalogueCopyExtendable"
    )
    assert check.portal_id == 0
    assert check.user_name == "RND123"
    assert check.culture == "de-DE"
    assert check.batch

    data = parser.parse_account(soup)
    parser.apply_extension_results(
        data.loans,
        {
            "d": [
                {"CopyId": "M1001", "IsExtendable": True, "StatusMessages": None},
                {"CopyId": "M1002", "IsExtendable": False, "StatusMessages": "<b>Vorbestellt</b>"},
            ]
        },
    )
    assert data.loans[0].renewable is True
    assert data.loans[1].renewable is False
    assert data.loans[1].renew_message == "Vorbestellt"


def test_real_renew_preview() -> None:
    """Captured Iserlohn confirmation dialog: one item, no fee, only the confirm button."""
    preview = parser.parse_renew_preview(parser.soup_of(load_fixture("renew_preview.html")))
    assert len(preview.items) == 1
    item = preview.items[0]
    assert item["Titel"] == "Testbuch Eins"
    assert item["Aktuelle Frist"] == "04.12.2026"
    assert item["Neue Frist - Gilt erst nach Bestätigung der Verlängerung"] == "05.12.2026"
    assert item["Status"] == "Verlängerbar"
    assert preview.fee_total == Decimal("0.00")
    assert preview.message is None
    assert not parser.renew_succeeded(preview.message)
    assert dict(preview.confirm_fields) == {
        "__VIEWSTATE": "VS2",
        "__EVENTVALIDATION": "EV2",
        "dnn$ctr379$MainView$loansExtensionPopup$btnDefault": "Verlängerung durchführen",
    }


def test_renew_preview_and_result() -> None:
    """Confirmation popup with items and fee; success message."""
    html = """
    <form action="/Mein-Konto">
    <input type="hidden" name="__VIEWSTATE" value="VS2">
    <table id="x_loansExtensionPopup_ucLoansExtension_grdViewLoans"><tbody>
    <tr><th abbr="Cover">Cover</th><th abbr="Titel">Titel</th><th abbr="Frist">Frist</th>
        <th abbr="Neue Frist">Neue Frist</th></tr>
    <tr><td></td><td>Testbuch Abholung</td><td>09.10.2026</td><td>06.11.2026</td></tr>
    </tbody></table>
    <span id="x_loansExtensionPopup_ucLoansExtension_LblFeeTotalData">1,00 EUR</span>
    <input type="submit" name="dnn$ctr379$MainView$loansExtensionPopup$btnDefault" id="x_loansExtensionPopup_btnDefault" value="OK">
    <span id="x_messagePopup_lblMessage"></span>
    </form>
    """
    preview = parser.parse_renew_preview(parser.soup_of(html))
    assert preview.items == [{"Titel": "Testbuch Abholung", "Frist": "09.10.2026", "Neue Frist": "06.11.2026"}]
    assert preview.fee_total == Decimal("1.00")
    assert preview.message is None
    assert dict(preview.confirm_fields) == {
        "__VIEWSTATE": "VS2",
        "dnn$ctr379$MainView$loansExtensionPopup$btnDefault": "OK",
    }

    done = parser.soup_of(
        '<span id="a_messagePopup_lblMessage">Ihre Verlängerung wurde erfolgreich '
        "durchgeführt.</span>"
    )
    assert parser.renew_succeeded(parser.renew_message(done))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0,50 EUR", Decimal("0.50")),
        ("-2,00 EUR", Decimal("-2.00")),
        ("1.234,56 EUR", Decimal("1234.56")),
        ("", None),
    ],
)
def test_parse_money(text: str, expected: Decimal | None) -> None:
    """German amounts."""
    assert parser.parse_money(text) == expected
