"""Strip API keys from provider error text before it is logged.

httpx exceptions embed the full request URL, and the keyed providers put
their key in it (Finnhub `token=`, Polygon `apiKey=`, Alpha Vantage
`apikey=`, Marketaux `api_token=`, exchangerate-api in the path).
"""
import re

from ..config import settings

_URL_QUERY = re.compile(r"(https?://[^\s?#'\"<>]+)\?[^\s#'\"<>]*")
_KEY_PARAM = re.compile(
    r"(?i)\b(token|api_?key|api_token|access_token|secret)=[^&\s'\"<>]+"
)
# SQLAlchemy appends "[parameters: (...)]" to DB errors: bound values (costs,
# quantities). Strip it, even when the text is cut off before the closing "]".
_SQL_PARAMETERS = re.compile(
    r"\[parameters: .*?(?:\](?=\s*(?:\(Background|\Z))|\Z)", re.DOTALL
)
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@'\"<>]+@")
_REDACTED = "<redacted>"


def _configured_keys() -> list[str]:
    keys = (
        settings.polygon_api_key,
        settings.finnhub_api_key,
        settings.alpha_vantage_api_key,
        settings.marketaux_api_key,
        settings.exchangerate_api_key,
    )
    return [k for k in keys if k]


def redact_secrets(value) -> str:
    """`str(value)` with SQL `[parameters: ...]` and URL userinfo masked, and URL
    query strings, key-like params and configured provider keys replaced by
    <redacted>."""
    text = str(value)
    text = _SQL_PARAMETERS.sub(f"[parameters: {_REDACTED}]", text)
    text = _URL_USERINFO.sub(r"\1***@", text)
    text = _URL_QUERY.sub(rf"\1?{_REDACTED}", text)
    text = _KEY_PARAM.sub(rf"\1={_REDACTED}", text)
    for key in _configured_keys():
        text = text.replace(key, _REDACTED)
    return text
