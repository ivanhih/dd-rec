"""Cookie parsing shared by login, recording, and manual credential fields."""
from __future__ import annotations

import re


def parse_cookie(raw: str | dict | None) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        pairs = raw.items()
    else:
        text = str(raw).strip()
        if "\r" in text or "\n" in text:
            raise ValueError("Cookie must not contain line breaks")
        known_names = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid", "buvid3", "buvid4"}
        padded_token = (";" not in text and text.endswith("=") and "=" not in text.rstrip("=")
                        and text.split("=", 1)[0] not in known_names)
        if "=" not in text or padded_token:
            pairs = [("SESSDATA", text)]
        else:
            pairs = [part.strip().split("=", 1) for part in text.split(";") if "=" in part]
    result = {}
    for name, value in pairs:
        name, value = str(name).strip(), str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", name) or any(c in value for c in "\r\n;"):
            raise ValueError("Invalid Cookie format")
        if value:
            result[name] = value
    return result


def cookie_header(raw: str | dict | None) -> str:
    return "; ".join(f"{name}={value}" for name, value in parse_cookie(raw).items())


def ffmpeg_headers(headers: dict) -> str:
    """Serialize HTTP headers only; internal proxy metadata is never forwarded."""
    return "".join(
        f"{name}: {value}\r\n"
        for name, value in headers.items()
        if not name.startswith("_") and name != "User-Agent"
        and "\r" not in str(value) and "\n" not in str(value)
    )
