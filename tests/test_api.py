"""Client tests."""

from __future__ import annotations

from yarl import URL

from custom_components.oclc_open.api import create_session


async def test_session_keeps_base64_cookie_unquoted() -> None:
    """COPAUTH (base64) must be sent verbatim, or OPEN logs the session off."""
    session = create_session()
    try:
        url = URL("https://opac.iserlohn.de/Mein-Konto")
        session.cookie_jar.update_cookies({"COPAUTH": "ab+c/d=="}, url)
        sent = session.cookie_jar.filter_cookies(url)
        assert sent["COPAUTH"].coded_value == "ab+c/d=="
    finally:
        await session.close()
