"""The Safari bridge: osascript in, page state out.

Two rules shape this module.

JavaScript and URLs are handed to `osascript` as **argv items**, never
interpolated into the script text. AppleScript string escaping is its own small
hell, and a page title with a quote in it should not be able to break a tool —
or inject AppleScript.

Tabs are addressed by index or URL substring and re-resolved on every call.
Safari exposes a `pid` per tab, but same-origin tabs share one WebContent
process (two Gmail tabs both report the same pid), so it cannot identify a tab.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit, quote

log = logging.getLogger(__name__)

_SEP = "|:|"
_ERR = "__SMCP_ERR__"

# Safari's AppleScript URL setter silently refuses some literal characters —
# parentheses are the one that bites in practice, on product URLs like
# /bambu-lab-p1s-combo-(with-ams)-3d-printer. Everything RFC 3986 allows is
# left alone except those; `%` stays safe so existing encodings survive.
_URL_SAFE = ":/?#[]@!$&'*+,;=%-._~"


class SafariError(RuntimeError):
    """Safari could not do the thing. The message is safe to show the caller."""


@dataclass(frozen=True)
class Tab:
    window: int
    index: int
    url: str
    title: str

    def label(self) -> str:
        return f"[w{self.window}:t{self.index}] {self.title[:60]} — {self.url[:80]}"


_EVAL = """
on run argv
  set jsCode to item 1 of argv
  set w to (item 2 of argv) as integer
  set t to (item 3 of argv) as integer
  tell application "Safari"
    try
      set r to (do JavaScript jsCode in tab t of window w)
    on error errMsg
      return "__SMCP_ERR__" & errMsg
    end try
  end tell
  try
    return r as text
  on error
    return ""
  end try
end run
"""

_LIST = """
on run argv
  set out to ""
  tell application "Safari"
    set wi to 0
    repeat with w in windows
      set wi to wi + 1
      set ti to 0
      repeat with tb in tabs of w
        set ti to ti + 1
        set ur to ""
        set nm to ""
        try
          set ur to (URL of tb) as text
        end try
        try
          set nm to (name of tb) as text
        end try
        set out to out & wi & "|:|" & ti & "|:|" & ur & "|:|" & nm & linefeed
      end repeat
    end repeat
  end tell
  return out
end run
"""

_SET_URL = """
on run argv
  set theURL to item 1 of argv
  set w to (item 2 of argv) as integer
  set t to (item 3 of argv) as integer
  tell application "Safari"
    set URL of tab t of window w to theURL
  end tell
  return "ok"
end run
"""

_OPEN = """
on run argv
  set theURL to item 1 of argv
  tell application "Safari"
    if (count of windows) is 0 then
      make new document with properties {URL: theURL}
      return "1|:|1"
    end if
    tell window 1
      set newTab to make new tab with properties {URL: theURL}
      set current tab to newTab
      set ti to 0
      repeat with tb in tabs
        set ti to ti + 1
        if tb is newTab then return "1|:|" & ti
      end repeat
    end tell
  end tell
  return "1|:|0"
end run
"""


def _osascript(script: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-", *args],
            input=script,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SafariError("Safari did not answer within 60s — is it beachballing?") from exc
    except FileNotFoundError as exc:  # pragma: no cover - macOS always has it
        raise SafariError("osascript is missing; this server only runs on macOS") from exc

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "-1743" in err or "Not authorized" in err:
            raise SafariError(
                "macOS has not granted this process Automation access to Safari — "
                "approve it under System Settings > Privacy & Security > Automation"
            )
        raise SafariError(f"AppleScript failed: {err or 'no detail'}")

    out = proc.stdout.rstrip("\n")
    if out.startswith(_ERR):
        detail = out[len(_ERR):].strip()
        if "Allow JavaScript from Apple Events" in detail:
            raise SafariError(
                "Safari is refusing JavaScript from Apple Events — enable it under "
                "Safari Settings > Developer > Allow JavaScript from Apple Events"
            )
        raise SafariError(f"Safari rejected the script: {detail}")
    return out


def list_tabs() -> list[Tab]:
    raw = _osascript(_LIST)
    tabs: list[Tab] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split(_SEP, 3)
        if len(parts) != 4:
            continue
        w, i, url, title = parts
        try:
            tabs.append(Tab(window=int(w), index=int(i), url=url, title=title))
        except ValueError:
            continue
    return tabs


def resolve_tab(tab: str | int) -> Tab:
    """Accept an index (1-based, within window 1) or a URL/title substring."""
    tabs = list_tabs()
    if not tabs:
        raise SafariError("Safari has no open tabs")

    if isinstance(tab, int) or (isinstance(tab, str) and tab.strip().lstrip("-").isdigit()):
        want = int(tab)
        for t in tabs:
            if t.window == 1 and t.index == want:
                return t
        raise SafariError(
            f"no tab {want} in window 1; open tabs are:\n"
            + "\n".join("  " + t.label() for t in tabs)
        )

    needle = str(tab).strip().lower()
    if not needle:
        raise SafariError("tab selector was empty")
    hits = [t for t in tabs if needle in t.url.lower() or needle in t.title.lower()]
    if not hits:
        raise SafariError(
            f"no open tab matches {tab!r}; open tabs are:\n"
            + "\n".join("  " + t.label() for t in tabs)
        )
    if len(hits) > 1:
        raise SafariError(
            f"{tab!r} matches {len(hits)} tabs — be more specific:\n"
            + "\n".join("  " + t.label() for t in hits)
        )
    return hits[0]


def eval_js(tab: Tab, js: str) -> str:
    return _osascript(_EVAL, js, str(tab.window), str(tab.index))


def eval_json(tab: Tab, expression: str) -> Any:
    """Run an expression and decode its JSON. Keeps AppleScript out of typing."""
    wrapped = (
        "(function(){try{return JSON.stringify((function(){" + expression + "})());}"
        "catch(e){return JSON.stringify({__error:String(e&&e.message||e)});}})()"
    )
    raw = eval_js(tab, wrapped)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SafariError(f"page returned something that was not JSON: {raw[:200]}") from exc
    if isinstance(value, dict) and "__error" in value:
        raise SafariError(f"JavaScript threw: {value['__error']}")
    return value


def encode_url(url: str) -> str:
    """Percent-encode the characters Safari's URL setter chokes on."""
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe=_URL_SAFE),
            quote(parts.query, safe=_URL_SAFE),
            quote(parts.fragment, safe=_URL_SAFE),
        )
    )


def wait_for_load(tab: Tab, timeout: float) -> str:
    """Poll readyState. Safari's AppleScript has no load event to hook."""
    deadline = time.monotonic() + timeout
    last = "unknown"
    while time.monotonic() < deadline:
        try:
            last = eval_js(tab, "document.readyState") or "unknown"
        except SafariError:
            last = "unreachable"  # mid-navigation the tab can briefly refuse
        if last == "complete":
            return eval_js(tab, "location.href")
        time.sleep(0.4)
    raise SafariError(
        f"page did not finish loading within {timeout:.0f}s (readyState={last})"
    )


def navigate(tab: Tab, url: str, timeout: float) -> str:
    encoded = encode_url(url)
    _osascript(_SET_URL, encoded, str(tab.window), str(tab.index))
    time.sleep(0.3)  # let Safari swap documents before the first poll
    return wait_for_load(tab, timeout)


def open_tab(url: str, timeout: float) -> Tab:
    encoded = encode_url(url)
    raw = _osascript(_OPEN, encoded)
    try:
        w, i = (int(x) for x in raw.split(_SEP, 1))
    except ValueError as exc:
        raise SafariError(f"could not read the new tab's position: {raw!r}") from exc
    tab = Tab(window=w, index=i, url=encoded, title="")
    time.sleep(0.3)
    final = wait_for_load(tab, timeout)
    return Tab(window=w, index=i, url=final, title=eval_js(tab, "document.title"))
