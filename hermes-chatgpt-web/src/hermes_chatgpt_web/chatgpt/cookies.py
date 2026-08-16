import json
from pathlib import Path
from typing import Any

from .config import COOKIE_DOMAINS, HOST_ONLY_COOKIES, SECURE_COOKIES


def parse_cookie_line(raw: str) -> list[dict[str, str]]:
    """Parse a raw cookie header string 'k1=v1; k2=v2' into a list of name/value dicts."""
    out = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out.append({"name": k.strip(), "value": v.strip()})
    return out


def parse_cookie_dict(raw: str) -> dict[str, str]:
    """Parse a raw cookie header string into a dict of {name: value}."""
    out = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_cookie_file(file_path: str | Path) -> list[dict[str, str]] | list[list[str]]:
    """Load and parse cookies from a json file (supports list of dicts, list of pairs, or {cookies: ...})."""
    path = Path(file_path)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "cookies" in data:
            raw_c = data["cookies"]
            if isinstance(raw_c, str):
                return parse_cookie_line(raw_c)
            elif isinstance(raw_c, list):
                return raw_c
    except Exception:
        pass
    return []


async def inject_chatgpt_cookies(context: Any, cookie_pairs: list) -> int:
    """
    Accepts cookie_pairs as list of (name, value) tuples/lists or list of dicts.
    Adds them across chatgpt/openai domains into the Playwright async browser context.
    Returns the number of cookie assignments made.
    """
    if not context:
        return 0

    cookies_to_add = []
    for item in cookie_pairs:
        if isinstance(item, dict):
            name = item.get("name")
            value = item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            name, value = item[0], item[1]
        else:
            continue

        if not name or value is None:
            continue

        secure = name in SECURE_COOKIES or "__Secure" in name or "_dd_s" in name
        dms = ["chatgpt.com"] if name in HOST_ONLY_COOKIES else COOKIE_DOMAINS
        for dm in dms:
            cookies_to_add.append(
                {
                    "name": name,
                    "value": str(value),
                    "domain": dm,
                    "path": "/",
                    "secure": secure,
                    "sameSite": "Lax",
                }
            )
    if cookies_to_add:
        try:
            await context.add_cookies(cookies_to_add)
            return len(cookies_to_add)
        except Exception:
            pass
    return 0
