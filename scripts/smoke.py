"""Log in to a real OPEN catalogue and print what the integration would see.

Usage:  uv run python scripts/smoke.py [--url https://opac.iserlohn.de] [--save]

Asks for card number and password interactively (or reads OCLC_USERNAME /
OCLC_PASSWORD). Never renews anything. --save writes the raw account page to
captures/ (git-ignored, contains personal data). --renew-preview COPY_ID presses
"Medien verlängern" for one copy and saves the confirmation dialog without
confirming it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.oclc_open import parser
from custom_components.oclc_open.api import OpenClient


def _redacted_cookie(header: str) -> str:
    name, _, rest = header.partition("=")
    attrs = rest.split(";", 1)[1] if ";" in rest else ""
    return f"{name}=<{len(rest.split(';', 1)[0])} chars>;{attrs}"


def _debug_trace(jar: aiohttp.CookieJar) -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()

    async def on_start(_session, _ctx, params) -> None:
        names = sorted({c.key for c in jar})
        print(f">> {params.method} {params.url}  jar={names}")

    async def on_redirect(_session, _ctx, params) -> None:
        print(f"   {params.response.status} -> {params.response.headers.get('Location')}")
        for header in params.response.headers.getall("Set-Cookie", []):
            print(f"   set-cookie {_redacted_cookie(header)}")

    async def on_end(_session, _ctx, params) -> None:
        print(f"<< {params.response.status} {params.response.url}")
        for header in params.response.headers.getall("Set-Cookie", []):
            print(f"   set-cookie {_redacted_cookie(header)}")

    trace.on_request_start.append(on_start)
    trace.on_request_redirect.append(on_redirect)
    trace.on_request_end.append(on_end)
    return trace


async def _dump_last_page(session: aiohttp.ClientSession, url: str) -> None:
    """Describe the page shown after the failed login (no personal data)."""
    async with session.get(url.rstrip("/") + "/Mein-Konto") as resp:
        soup = parser.soup_of(await resp.text())
    print("\n-- page after login --")
    print("title:", soup.title.get_text(strip=True) if soup.title else None)
    print("account page:", parser.is_account_page(soup))
    print("login form:", soup.select_one("input[name$=txtUsername]") is not None)
    for el in soup.select(".dnnFormMessage, h1, h2"):
        text = el.get_text(" ", strip=True)
        if text:
            print(f"  {el.name}.{'.'.join(el.get('class', []))}: {text[:150]}")


async def _renew_preview(client: OpenClient, copy_id: str) -> None:
    """Renewal step 1 only: open the confirmation dialog, never confirm it."""
    soup, url = await client._account_page()
    fields = parser.renew_fields(soup, [copy_id])
    action = parser.form_action(parser.main_form(soup), url)
    soup, _ = await client._post_form(action, fields)
    out = Path("captures")
    out.mkdir(exist_ok=True)
    (out / "renew_preview.html").write_text(str(soup), encoding="utf-8")
    preview = parser.parse_renew_preview(soup)
    print(f"\nSaved {out / 'renew_preview.html'} (NOT confirmed)")
    print(f"  message: {preview.message}")
    print(f"  fee total: {preview.fee_total}")
    print(f"  confirm button found: {preview.confirm_fields is not None}")
    for item in preview.items:
        print(f"  item: {item}")


async def main() -> None:
    """Run the smoke test."""
    args = argparse.ArgumentParser()
    args.add_argument("--url", default="https://opac.iserlohn.de")
    args.add_argument("--save", action="store_true")
    args.add_argument("--debug", action="store_true", help="print requests and cookie names")
    args.add_argument(
        "--renew-preview",
        metavar="COPY_ID",
        help="open the renewal confirmation for one copy without confirming it",
    )
    opts = args.parse_args()
    username = os.environ.get("OCLC_USERNAME") or input("Card number: ").strip()
    password = os.environ.get("OCLC_PASSWORD") or getpass("Password: ")

    jar = aiohttp.CookieJar(quote_cookie=False)
    trace = _debug_trace(jar) if opts.debug else None
    async with aiohttp.ClientSession(
        cookie_jar=jar, trace_configs=[trace] if trace else None
    ) as session:
        client = OpenClient(session, opts.url, username, password)
        try:
            data = await client.async_fetch()
        except Exception:
            if opts.debug:
                await _dump_last_page(session, opts.url)
            raise
        if opts.save:
            soup, _ = await client._account_page()
            out = Path("captures")
            out.mkdir(exist_ok=True)
            (out / "account.html").write_text(str(soup), encoding="utf-8")
            print(f"Saved {out / 'account.html'}")
        if opts.renew_preview:
            await _renew_preview(client, opts.renew_preview)
        await client.async_logout()

    print(f"\nLoans ({len(data.loans)}):")
    for loan in data.loans:
        print(
            f"  {loan.due_date}  {loan.title[:50]:50}  copy={loan.copy_id}"
            f"  renewable={loan.renewable}  {loan.renew_message or ''}"
        )
        if loan.extra:
            print(f"      extra: {loan.extra}")
    print(f"\nReservations ({len(data.reservations)}):")
    for res in data.reservations:
        state = f"ready until {res.pickup_deadline}" if res.ready else "pending"
        print(f"  {state:22}  {res.title[:50]}  {res.extra or ''}")
    print(f"\nFees owed: {data.amount_owed} EUR  (open {data.fees_open}, paid {data.fees_paid})")
    print(f"Card valid until: {data.valid_until}")
    print(f"Warning: {data.warning}")
    if data.loans and all(loan.copy_id is None for loan in data.loans):
        print("\n!! Loans found but no copy ids: renewal will not work, parser needs adjusting.")
    if data.loans and all(loan.due_date is None for loan in data.loans):
        print("\n!! Loans found but no due dates: parser needs adjusting.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except parser.ParseError as err:
        sys.exit(f"Parse error: {err}")
