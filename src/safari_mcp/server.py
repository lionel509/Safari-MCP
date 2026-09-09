"""The MCP server. Eight tools over stdio, three of them read-only."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from mcp.server.mcpserver import MCPServer

from . import applescript as sa
from .config import ConfigError, Settings, load
from .guard import Blocked, check_denylist, review

# stdio carries the protocol, so every log line must go to stderr.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)
mcp = MCPServer("safari")

_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load()
    return _settings


def _js_str(value: object) -> str:
    """Embed a Python value as a JS literal. json.dumps is valid JS syntax."""
    return json.dumps(value)


async def _resolve(tab: str) -> sa.Tab:
    return await asyncio.to_thread(sa.resolve_tab, tab)


async def _describe(t: sa.Tab, selector: str, index: int) -> str:
    """A one-line description of what a write is about to touch, for the gate."""
    expr = (
        f"var els=document.querySelectorAll({_js_str(selector)});"
        f"var e=els[{int(index)}];"
        "if(!e) return 'NO MATCH';"
        "return e.tagName+' '+(e.getAttribute('type')||'')+' \"'+"
        "((e.innerText||e.textContent||e.value||'').trim().replace(/\\s+/g,' ').slice(0,180))+'\"';"
    )
    try:
        return str(await asyncio.to_thread(sa.eval_json, t, expr))
    except sa.SafariError:
        return f"selector {selector!r} index {index}"


# --------------------------------------------------------------------------
# reads — never gated
# --------------------------------------------------------------------------


@mcp.tool()
async def list_tabs() -> str:
    """List every open Safari tab, with the handle you use to address it.

    Call this first. Every other tool takes a `tab` argument, and this shows you
    what is addressable: a 1-based index within window 1, or any distinctive
    substring of a tab's URL or title.

    Returns one line per tab as `[w<window>:t<index>] <title> — <url>`.
    """
    try:
        tabs = await asyncio.to_thread(sa.list_tabs)
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    if not tabs:
        return "Safari has no open tabs."
    return "\n".join(t.label() for t in tabs)


@mcp.tool()
async def read_page(tab: str, selector: str = "", max_chars: int = 8000) -> str:
    """Read the visible text of a tab, or of one element inside it.

    This is the workhorse — prefer it over screenshots. It returns rendered
    text (`innerText`), so it reflects what a person would actually see rather
    than raw markup.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        selector: Optional CSS selector. Omit it to read the whole page.
        max_chars: Truncate beyond this many characters. Default 8000.
    """
    try:
        t = await _resolve(tab)
        if selector:
            expr = (
                f"var e=document.querySelector({_js_str(selector)});"
                "return e?(e.innerText||e.textContent||''):null;"
            )
        else:
            expr = "return document.body?document.body.innerText:'';"
        text = await asyncio.to_thread(sa.eval_json, t, expr)
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc

    if text is None:
        raise RuntimeError(f"nothing on the page matches {selector!r}")
    text = str(text)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n… [truncated, {len(text)} chars total]"
    return text


@mcp.tool()
async def find_elements(tab: str, selector: str, limit: int = 20) -> str:
    """Inspect the elements a CSS selector matches, before acting on them.

    Use this to check that a selector hits what you think it does, and to read
    the index you will pass to `click` or `fill`. Returns JSON with each match's
    tag, id, class, visible text, href, name, type and current value.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        selector: The CSS selector to inspect.
        limit: Most elements to describe. Default 20.
    """
    expr = (
        f"var els=document.querySelectorAll({_js_str(selector)});var out=[];"
        f"for(var i=0;i<els.length&&i<{int(limit)};i++){{var e=els[i];out.push({{"
        "i:i,tag:e.tagName,"
        "id:e.id||null,"
        "cls:(e.className&&String(e.className).slice(0,80))||null,"
        "text:((e.innerText||e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,160))||null,"
        "href:e.getAttribute?e.getAttribute('href'):null,"
        "name:e.getAttribute?e.getAttribute('name'):null,"
        "type:e.getAttribute?e.getAttribute('type'):null,"
        "value:(e.value!==undefined&&e.value!==null)?String(e.value).slice(0,120):null"
        "}});}"
        "return {matched:els.length,shown:out.length,elements:out};"
    )
    try:
        t = await _resolve(tab)
        data = await asyncio.to_thread(sa.eval_json, t, expr)
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    return json.dumps(data, indent=2)


# --------------------------------------------------------------------------
# writes — denylist, then the local gate
# --------------------------------------------------------------------------


@mcp.tool()
async def open_tab(url: str) -> str:
    """Open a URL in a new Safari tab and wait for it to finish loading.

    Prefer this over `navigate` when starting a task: it leaves whatever the
    person was already reading untouched. Returns the new tab's handle and its
    final URL, which may differ from the one you asked for if the site
    redirected.

    Args:
        url: The URL to open. Parentheses and spaces are encoded for you.
    """
    cfg = settings()
    try:
        check_denylist("open", url, cfg)
        t = await asyncio.to_thread(sa.open_tab, url, cfg.load_timeout)
    except Blocked as exc:
        raise RuntimeError(str(exc)) from exc
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    return f"opened {t.label()}"


@mcp.tool()
async def navigate(tab: str, url: str) -> str:
    """Point an existing tab at a URL and wait for the load to complete.

    Returns the URL actually landed on, so you can see redirects. Parentheses,
    spaces and other characters Safari's URL setter silently refuses are
    percent-encoded for you.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        url: Where to send it.
    """
    cfg = settings()
    try:
        t = await _resolve(tab)
        check_denylist("navigate away from", t.url, cfg)
        check_denylist("navigate to", url, cfg)
        final = await asyncio.to_thread(sa.navigate, t, url, cfg.load_timeout)
    except Blocked as exc:
        raise RuntimeError(str(exc)) from exc
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    return f"loaded {final}"


@mcp.tool()
async def click(tab: str, selector: str, index: int = 0) -> str:
    """Click an element. Check it with `find_elements` first.

    Passes through the denylist and then the local safety gate, either of which
    may refuse — the refusal explains itself and is worth relaying verbatim
    rather than retrying.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        selector: CSS selector for the element.
        index: Which match to click when the selector hits several. Default 0.
    """
    cfg = settings()
    try:
        t = await _resolve(tab)
        check_denylist("click on", t.url, cfg)
        detail = await _describe(t, selector, index)
        if detail == "NO MATCH":
            raise RuntimeError(f"nothing matches {selector!r} at index {index}")
        await review("click", url=t.url, title=t.title, detail=detail, settings=cfg)
        expr = (
            f"var els=document.querySelectorAll({_js_str(selector)});"
            f"var e=els[{int(index)}];if(!e) return 'NO MATCH';"
            "e.click();return 'clicked';"
        )
        await asyncio.to_thread(sa.eval_json, t, expr)
    except Blocked as exc:
        raise RuntimeError(str(exc)) from exc
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    return f"clicked {detail}"


@mcp.tool()
async def fill(tab: str, selector: str, value: str, index: int = 0) -> str:
    """Type a value into an input, textarea, or contenteditable element.

    Sets the value through the element's native setter and then dispatches
    `input` and `change` with `bubbles: true`. That matters: Google Forms,
    React and other frameworks listen for those events and ignore a bare
    assignment to `.value`, so a naive fill looks correct on screen and submits
    empty.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        selector: CSS selector for the field.
        value: The text to enter.
        index: Which match to fill when the selector hits several. Default 0.
    """
    cfg = settings()
    try:
        t = await _resolve(tab)
        check_denylist("type into", t.url, cfg)
        detail = await _describe(t, selector, index)
        if detail == "NO MATCH":
            raise RuntimeError(f"nothing matches {selector!r} at index {index}")
        await review(
            f"fill with {value[:80]!r}",
            url=t.url,
            title=t.title,
            detail=detail,
            settings=cfg,
        )
        expr = (
            f"var els=document.querySelectorAll({_js_str(selector)});"
            f"var e=els[{int(index)}];if(!e) return {{ok:false}};"
            f"var v={_js_str(value)};e.focus();"
            "if(e.isContentEditable){e.textContent=v;}"
            "else{var proto=(e.tagName==='TEXTAREA')?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
            "var d=Object.getOwnPropertyDescriptor(proto,'value');"
            "if(d&&d.set){d.set.call(e,v);}else{e.value=v;}}"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "e.dispatchEvent(new Event('change',{bubbles:true}));"
            "e.blur();"
            "return {ok:true,now:String(e.isContentEditable?e.textContent:e.value).slice(0,120)};"
        )
        result = await asyncio.to_thread(sa.eval_json, t, expr)
    except Blocked as exc:
        raise RuntimeError(str(exc)) from exc
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    if not result or not result.get("ok"):
        raise RuntimeError(f"could not fill {selector!r} at index {index}")
    return f"filled {detail} — now {result.get('now')!r}"


@mcp.tool()
async def run_js(tab: str, code: str) -> str:
    """Run arbitrary JavaScript in a tab. The escape hatch; prefer the others.

    Your code runs as a function body, so `return` the value you want back. The
    result is JSON-encoded, so return plain data rather than DOM nodes.

    Args:
        tab: Tab index within window 1, or a substring of its URL or title.
        code: JavaScript to execute. Use `return` to produce a result.
    """
    cfg = settings()
    try:
        t = await _resolve(tab)
        check_denylist("run JavaScript on", t.url, cfg)
        await review(
            "run arbitrary JavaScript",
            url=t.url,
            title=t.title,
            detail=code[:400],
            settings=cfg,
        )
        data = await asyncio.to_thread(sa.eval_json, t, code)
    except Blocked as exc:
        raise RuntimeError(str(exc)) from exc
    except sa.SafariError as exc:
        raise RuntimeError(str(exc)) from exc
    return json.dumps(data, indent=2) if data is not None else "(no value returned)"


def main() -> None:
    try:
        settings()
    except ConfigError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
