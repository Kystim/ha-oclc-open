"""HTML parsing for OCLC OPEN (BIBLIOTHECA web OPAC) account pages.

Pure functions only; no I/O. Selectors follow the opacclient OpenAccountScraper
and the markup of OPEN 11.x.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_MONEY_RE = re.compile(r"(-?)\s*(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})")
_POSTBACK_RE = re.compile(r"__doPostBack\('([^']*)','([^']*)'\)")
_EXTENSIONS_RE = re.compile(r'LoadExtensionsAsync\("([^"]*)",[^0-9,]*([0-9]+)[^0-9,]*,')

NO_DATA_SELECTOR = ".GridViewInnerBorderNoData, span[id$=LblNoDataReturned]"

LOANS_TABLE = "[id$=tpnlLoans_ucLoansView_grdViewLoans]"
REMOTE_LOANS_TABLE = "[id$=tpnlRemoteLoans_ucRemoteLoansView_grdViewRemoteLoans]"
RESERVATIONS_TABLE = "[id$=tpnlReservations_ucReservationsView_grdViewReservations]"
READY_TABLE = "[id$=tpnlReservations_ucReservationsView_grdViewReadyForPickups]"
REMOTE_READY_TABLE = (
    "[id$=tpnlRemoteLoans_ucRemoteLoansView_grdViewRemoteReadyForPickups]"
)
FEES_TABLE = "[id$=tpnlFees_ucFeesView_grdViewFees]"
EXTENSION_TABLE = (
    "[id$=loansExtensionPopup_ucLoansExtension_grdViewLoansExtension], "
    "[id$=loansExtensionPopup_ucLoansExtension_grdViewLoans], "
    "[id$=ucExtendCatalogueCopiesPopupView_grdViewLoans]"
)
RENEW_MESSAGE = (
    "[id$=ucExtensionFailedMessagePopupView_LblPopupMessage], "
    "[id$=messagePopup_lblMessage]"
)
RENEW_SUCCESS_MESSAGES = (
    "Ihre Verlängerung wurde erfolgreich durchgeführt.",
    "Ihre Verlängerung wurde durchgeführt.",
)

# Sort keys used in the postback links of sortable column headers.
_SORT_KEYS = {
    "Sort$Title": "title",
    "Sort$Author": "author",
    "Sort$MediaGroup": "media_type",
    "Sort$DueDate": "due_date",
    "Sort$Branch": "branch",
}
# Fallback: header text fragments (lower case), checked in order.
_HEADER_TEXT_KEYS = (
    ("cover", "cover"),
    ("titel", "title"),
    ("verfasser", "author"),
    ("autor", "author"),
    ("mediengruppe", "media_type"),
    ("medienart", "media_type"),
    ("zweigstelle", "branch"),
    ("standort", "branch"),
    ("grund", "reason"),
    ("gebühr", "amount"),
    ("frist", "due_date"),
    ("rückgabe", "due_date"),
    ("fällig", "due_date"),
    ("verlängerbar", "renewable"),
)


class ParseError(Exception):
    """The page does not have the expected structure."""


@dataclass(slots=True)
class Loan:
    """A borrowed item."""

    title: str
    copy_id: str | None = None
    media_id: str | None = None
    author: str | None = None
    media_type: str | None = None
    branch: str | None = None
    due_date: date | None = None
    renewable: bool | None = None
    renew_message: str | None = None
    select_name: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for state attributes."""
        return {
            "title": self.title,
            "author": self.author,
            "media_type": self.media_type,
            "branch": self.branch,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "renewable": self.renewable,
            "renew_message": self.renew_message,
            "copy_id": self.copy_id,
            "media_id": self.media_id,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass(slots=True)
class Reservation:
    """A reserved item, either pending or ready for pickup."""

    title: str
    ready: bool
    media_id: str | None = None
    author: str | None = None
    media_type: str | None = None
    branch: str | None = None
    pickup_deadline: date | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for state attributes."""
        return {
            "title": self.title,
            "author": self.author,
            "media_type": self.media_type,
            "branch": self.branch,
            "ready": self.ready,
            "pickup_deadline": (
                self.pickup_deadline.isoformat() if self.pickup_deadline else None
            ),
            "media_id": self.media_id,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass(slots=True)
class Fee:
    """A single fee line."""

    title: str
    author: str | None = None
    media_type: str | None = None
    reason: str | None = None
    amount: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for state attributes."""
        return {
            "title": self.title,
            "author": self.author,
            "media_type": self.media_type,
            "reason": self.reason,
            "amount": float(self.amount) if self.amount is not None else None,
        }


@dataclass(slots=True)
class AccountData:
    """Everything parsed from the account page."""

    loans: list[Loan] = field(default_factory=list)
    reservations: list[Reservation] = field(default_factory=list)
    fees: list[Fee] = field(default_factory=list)
    fees_open: Decimal | None = None
    fees_paid: Decimal | None = None
    balance: Decimal | None = None
    valid_until: date | None = None
    warning: str | None = None

    @property
    def amount_owed(self) -> Decimal:
        """Fees not yet paid; never negative."""
        if self.fees_open is not None:
            owed = self.fees_open - (self.fees_paid or Decimal(0))
            return max(owed, Decimal(0))
        if self.balance is not None:
            return abs(self.balance)
        return Decimal(0)


@dataclass(slots=True)
class ExtensionCheck:
    """Parameters for the IsCatalogueCopyExtendable web service."""

    url: str
    portal_id: int
    culture: str
    user_name: str
    batch: bool


@dataclass(slots=True)
class RenewPreview:
    """The confirmation popup shown after selecting items for renewal."""

    items: list[dict[str, str]]
    fee_total: Decimal | None
    confirm_fields: list[tuple[str, str]] | None
    message: str | None


# --- helpers -----------------------------------------------------------------


def soup_of(html: str) -> BeautifulSoup:
    """Parse HTML."""
    return BeautifulSoup(html, "html.parser")


def parse_date(text: str | None) -> date | None:
    """Return the first dd.mm.yyyy date found in text."""
    if not text:
        return None
    if match := _DATE_RE.search(text):
        try:
            return datetime.strptime(match.group(1), "%d.%m.%Y").date()  # noqa: DTZ007
        except ValueError:
            return None
    return None


def parse_money(text: str | None) -> Decimal | None:
    """Parse '1.234,50 EUR' style amounts."""
    if not text:
        return None
    if not (match := _MONEY_RE.search(text)):
        return None
    sign, whole, cents = match.groups()
    try:
        value = Decimal(f"{whole.replace('.', '')}.{cents}")
    except InvalidOperation:
        return None
    return -value if sign else value


def _text(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).replace("\xa0", " ").split())


def _cell_text(cell: Tag) -> str:
    """Cell text without the inline labels used in the mobile layout."""
    clone = BeautifulSoup(str(cell), "html.parser")
    for label in clone.select(".oclc-module-label, .oclc-module-view-small"):
        label.decompose()
    return _text(clone)


def _is_hidden(el: Tag) -> bool:
    for node in [el, *el.parents]:
        style = node.get("style") if isinstance(node, Tag) else None
        if style and re.search(r"display\s*:\s*none", str(style)):
            return True
    return False


def _media_id(cell: Tag | None) -> str | None:
    if cell is None:
        return None
    link = cell.find("a", href=True)
    if link is None:
        return None
    values = parse_qs(urlparse(str(link["href"])).query).get("id")
    return values[0].strip() if values else None


def _table_is_empty(table: Tag) -> bool:
    return bool(table.select(NO_DATA_SELECTOR))


def _header_key(th: Tag) -> str | None:
    if th.find("input") is not None:
        return "select"
    html = str(th)
    for sort_key, key in _SORT_KEYS.items():
        if sort_key in html:
            return key
    text = (th.get("abbr") or _text(th)).lower()
    for fragment, key in _HEADER_TEXT_KEYS:
        if fragment in text:
            return key
    return None


def _rows(table: Tag) -> list[tuple[Tag, dict[str, Tag], dict[str, str]]]:
    """Split a grid into data rows keyed by column.

    Returns (row, known columns, unknown columns as label -> text).
    """
    result: list[tuple[Tag, dict[str, Tag], dict[str, str]]] = []
    keys: list[str | None] = []
    labels: list[str] = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        ths = row.find_all("th", recursive=False)
        if ths:
            keys = [_header_key(th) for th in ths]
            labels = [str(th.get("abbr") or _text(th)) for th in ths]
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) <= 1:
            continue
        known: dict[str, Tag] = {}
        extra: dict[str, str] = {}
        for index, cell in enumerate(cells):
            key = keys[index] if index < len(keys) else None
            if key and key not in known:
                known[key] = cell
            elif (
                index < len(labels)
                and labels[index]
                and key != "cover"
                and (value := _cell_text(cell))
            ):
                extra[labels[index]] = value
        result.append((row, known, extra))
    return result


def _col(known: dict[str, Tag], key: str) -> str | None:
    cell = known.get(key)
    if cell is None:
        return None
    return _cell_text(cell) or None


# --- forms ---------------------------------------------------------------------


def main_form(soup: BeautifulSoup) -> Tag:
    """Return the ASP.NET page form."""
    form = soup.find("form")
    if form is None:
        raise ParseError("No form on page")
    return form


def form_action(form: Tag, page_url: str) -> str:
    """Absolute form action URL."""
    return urljoin(page_url, str(form.get("action") or page_url))


def form_fields(form: Tag, submit_name: str | None = None) -> list[tuple[str, str]]:
    """Collect the values a browser would submit for this form."""
    fields: list[tuple[str, str]] = []
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled"):
            continue
        if el.name == "select":
            selected = el.find_all("option", selected=True) or el.find_all("option")[:1]
            fields.extend((name, str(opt.get("value", _text(opt)))) for opt in selected)
            continue
        if el.name == "textarea":
            fields.append((name, el.get_text()))
            continue
        kind = str(el.get("type", "text")).lower()
        if kind in ("checkbox", "radio"):
            if el.has_attr("checked"):
                fields.append((name, str(el.get("value") or "on")))
        elif kind in ("submit", "image", "button", "reset"):
            if submit_name is not None and name == submit_name:
                fields.append((name, str(el.get("value", ""))))
        elif kind == "file":
            continue
        else:
            fields.append((name, str(el.get("value", ""))))
    return fields


def _set_field(fields: list[tuple[str, str]], name: str, value: str) -> None:
    fields[:] = [(k, v) for k, v in fields if k != name]
    fields.append((name, value))


def login_fields(
    soup: BeautifulSoup, username: str, password: str
) -> list[tuple[str, str]] | None:
    """Fill the DNN login form; None when the page has no login form."""
    user = soup.select_one("input[name$=txtUsername]")
    pwd = soup.select_one("input[name$=txtPassword]")
    button = soup.select_one("input[name$=cmdLogin]")
    if user is None or pwd is None or button is None:
        return None
    fields = form_fields(main_form(soup), str(button["name"]))
    _set_field(fields, str(user["name"]), username)
    _set_field(fields, str(pwd["name"]), password)
    return fields


def login_error(soup: BeautifulSoup) -> str | None:
    """Error message shown after a failed login."""
    for el in soup.select(".dnnFormValidationSummary, .dnnFormMessage.dnnFormError"):
        if not _is_hidden(el) and (text := _text(el)):
            return text
    return None


def is_account_page(soup: BeautifulSoup) -> bool:
    """True when the logged-in account view is present."""
    return soup.select_one(RESERVATIONS_TABLE) is not None


def site_name(soup: BeautifulSoup) -> str | None:
    """Portal name from '<Portal> > <Page>' titles."""
    title = _text(soup.find("title"))
    name = title.split(">")[0].strip()
    return name or None


def logoff_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """URL of the logout link."""
    link = soup.select_one("a[href*='ctl/Logoff']")
    return urljoin(page_url, str(link["href"])) if link else None


# --- account -------------------------------------------------------------------


def parse_account(soup: BeautifulSoup) -> AccountData:
    """Parse the logged-in account page."""
    if not is_account_page(soup):
        raise ParseError("Not an account page")
    data = AccountData()
    for selector in (LOANS_TABLE, REMOTE_LOANS_TABLE):
        if (table := soup.select_one(selector)) and not _table_is_empty(table):
            data.loans.extend(_parse_loans(table))
    if (table := soup.select_one(RESERVATIONS_TABLE)) and not _table_is_empty(table):
        data.reservations.extend(_parse_reservations(table, ready=False))
    for selector in (READY_TABLE, REMOTE_READY_TABLE):
        if (table := soup.select_one(selector)) and not _table_is_empty(table):
            data.reservations.extend(_parse_reservations(table, ready=True))
    if (table := soup.select_one(FEES_TABLE)) and not _table_is_empty(table):
        data.fees.extend(_parse_fees(table))

    data.fees_open = parse_money(_text(soup.select_one("[id$=ucFeesView_lblFeeTotalData]")))
    data.fees_paid = parse_money(_text(soup.select_one("[id$=ucFeesView_lblDepositData]")))
    data.balance = parse_money(_text(soup.select_one("[id$=ucFeesView_lblTotalSaldoData]")))
    data.valid_until = parse_date(
        _text(soup.select_one("[id$=ucPatronAccountView_LblMembershipValidUntilData]"))
    )
    warnings = [
        _text(el)
        for el in soup.select(".dnnFormWarning, [id$=patronAccountExtensionMessage]")
        if not _is_hidden(el) and _text(el)
    ]
    data.warning = " ".join(warnings) or None
    return data


def _parse_loans(table: Tag) -> list[Loan]:
    loans: list[Loan] = []
    for row, known, extra in _rows(table):
        copy_input = row.select_one("input[name$=CopyIdChx]")
        checkbox = row.select_one("input[name*=chkSelect]")
        title_cell = known.get("title")
        copy_id = str(copy_input.get("value", "")).strip() if copy_input else ""
        loan = Loan(
            title=_col(known, "title") or "",
            copy_id=copy_id or None,
            media_id=_media_id(title_cell) or _media_id(row),
            author=_col(known, "author"),
            media_type=_col(known, "media_type"),
            branch=_col(known, "branch"),
            due_date=parse_date(_col(known, "due_date")),
            select_name=str(checkbox["name"]) if checkbox else None,
            extra=extra,
        )
        if checkbox is not None and checkbox.has_attr("disabled"):
            loan.renewable = False
        loans.append(loan)
    return loans


def _parse_reservations(table: Tag, ready: bool) -> list[Reservation]:
    items: list[Reservation] = []
    for row, known, extra in _rows(table):
        deadline_text = _col(known, "due_date")
        if deadline_text:
            deadline_text = deadline_text.replace("Aktuelle Frist:", "")
        items.append(
            Reservation(
                title=_col(known, "title") or "",
                ready=ready,
                media_id=_media_id(known.get("title")) or _media_id(row),
                author=_col(known, "author"),
                media_type=_col(known, "media_type"),
                branch=_col(known, "branch"),
                pickup_deadline=parse_date(deadline_text) if ready else None,
                extra=extra,
            )
        )
    return items


def _parse_fees(table: Tag) -> list[Fee]:
    return [
        Fee(
            title=_col(known, "title") or "",
            author=_col(known, "author"),
            media_type=_col(known, "media_type"),
            reason=_col(known, "reason"),
            amount=parse_money(_col(known, "amount")),
        )
        for _row, known, _extra in _rows(table)
    ]


# --- renewal ---------------------------------------------------------------------


def extension_check(soup: BeautifulSoup, page_url: str, username: str) -> ExtensionCheck:
    """Parameters for querying which loans can be renewed."""
    url = urljoin(
        page_url,
        "/DesktopModules/OCLC.OPEN.PL.DNN.PatronAccountModule/"
        "PatronAccountService.asmx/IsCatalogueCopyExtendable",
    )
    portal_id = 1
    for script in soup.find_all("script"):
        if (text := script.string or "") and (match := _EXTENSIONS_RE.search(text)):
            url = urljoin(page_url, match.group(1))
            portal_id = int(match.group(2))
            break
    culture = soup.select_one("input[name$=Culture]")
    rnd_id = soup.select_one("input[type=hidden][name$=PatronRndId]")
    return ExtensionCheck(
        url=url,
        portal_id=portal_id,
        culture=str(culture.get("value", "de-DE")) if culture else "de-DE",
        user_name=str(rnd_id.get("value")) if rnd_id and rnd_id.get("value") else username,
        batch=open_version(soup) >= (7, 1),
    )


def open_version(soup: BeautifulSoup) -> tuple[int, ...]:
    """OPEN version from the open.js cache-busting query, e.g. (11, 1, 0, 10)."""
    script = soup.select_one("script[src*='open.js']")
    if script is None:
        return (0,)
    query = str(script["src"]).rsplit("?", 1)[-1]
    try:
        return tuple(int(part) for part in query.split("."))
    except ValueError:
        return (0,)


EXTENSION_RESOURCE_FILE = (
    "~/DesktopModules/OCLC.OPEN.PL.DNN.PatronAccountModule/App_LocalResources/"
    "PatronAccountModule.resx"
)


def extension_payload(check: ExtensionCheck, copy_ids: list[str]) -> dict[str, Any]:
    """JSON body for the batch IsCatalogueCopyExtendable call (OPEN >= 7.1)."""
    return {
        "portalId": check.portal_id,
        "copyIds": ",".join(copy_ids),
        "userName": check.user_name,
        "localResourceFile": EXTENSION_RESOURCE_FILE,
        "culture": check.culture,
    }


def single_extension_payload(check: ExtensionCheck, copy_id: str) -> dict[str, Any]:
    """JSON body for the single-item IsCatalogueCopyExtendable call."""
    return {
        "portalId": check.portal_id,
        "copyId": copy_id,
        "userName": check.user_name,
        "localResourceFile": EXTENSION_RESOURCE_FILE,
        "culture": check.culture,
    }


def apply_extension_results(loans: list[Loan], response: Any) -> None:
    """Merge the web service response ({"d": [...]} or {"d": {...}}) into loans."""
    entries = response.get("d") if isinstance(response, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return
    by_copy = {loan.copy_id: loan for loan in loans if loan.copy_id}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        loan = by_copy.get(str(entry.get("CopyId", "")))
        if loan is None:
            continue
        loan.renewable = bool(entry.get("IsExtendable", False))
        message = entry.get("StatusMessages")
        if not loan.renewable and message and message != "null":
            loan.renew_message = _text(soup_of(str(message)))


def renew_fields(soup: BeautifulSoup, copy_ids: list[str]) -> list[tuple[str, str]]:
    """Form data that selects the given copies and presses 'Verlängern'."""
    button = soup.select_one("[name$=BtnExtendMediumsBottom]")
    if button is None:
        raise ParseError("No renew button on account page")
    fields = form_fields(main_form(soup), str(button["name"]))
    table = soup.select_one(LOANS_TABLE)
    wanted = set(copy_ids)
    found = 0
    if table is not None:
        for row in table.find_all("tr"):
            copy_input = row.select_one("input[name$=CopyIdChx]")
            checkbox = row.select_one("input[name*=chkSelect]")
            if copy_input is None or checkbox is None:
                continue
            if str(copy_input.get("value", "")).strip() in wanted:
                _set_field(fields, str(checkbox["name"]), "on")
                found += 1
    if not found:
        raise ParseError("None of the requested items were found")
    return fields


def parse_renew_preview(soup: BeautifulSoup) -> RenewPreview:
    """Parse the response to the first renewal step."""
    items: list[dict[str, str]] = []
    if table := soup.select_one(EXTENSION_TABLE):
        headers: list[str] = []
        for row in table.find_all("tr"):
            if ths := row.find_all("th", recursive=False):
                headers = [str(th.get("abbr") or _text(th)) for th in ths]
                continue
            cells = row.find_all("td", recursive=False)
            if len(cells) > 1:
                items.append(
                    {
                        (headers[i] if i < len(headers) and headers[i] else str(i)): value
                        for i, cell in enumerate(cells)
                        if (value := _cell_text(cell))
                    }
                )
    fee_el = soup.select_one(
        "[id$=loansExtensionPopup_ucLoansExtension_LblFeeTotalData], "
        "[id$=ucExtendCatalogueCopiesPopupView_DivFeeDisplay]"
    )
    return RenewPreview(
        items=items,
        fee_total=parse_money(_text(fee_el)),
        confirm_fields=_confirm_fields(soup),
        message=renew_message(soup),
    )


def _confirm_fields(soup: BeautifulSoup) -> list[tuple[str, str]] | None:
    form = main_form(soup)
    for script in soup.find_all("script"):
        text = script.string or ""
        if "DivExtendCatalogueCopiesPopupControl" in text and (
            match := _POSTBACK_RE.search(text)
        ):
            fields = form_fields(form)
            _set_field(fields, "__EVENTTARGET", match.group(1))
            _set_field(fields, "__EVENTARGUMENT", match.group(2))
            return fields
    button = soup.select_one("input[id$=loansExtensionPopup_btnDefault]")
    if button is not None:
        return form_fields(form, str(button["name"]))
    return None


def renew_message(soup: BeautifulSoup) -> str | None:
    """Result or error message after a renewal step."""
    texts = [_text(el) for el in soup.select(RENEW_MESSAGE)]
    return " ".join(t for t in texts if t) or None


def renew_succeeded(message: str | None) -> bool:
    """Whether the result message reports success."""
    return bool(message) and any(ok in message for ok in RENEW_SUCCESS_MESSAGES)
