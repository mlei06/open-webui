"""
title: Interface Toggles
description: Adds eight settings under Settings > Interface: Green Switches, Sidebar Hotkey Hints and Message Timestamps (on hover, always, or hidden), Stack Notifications, Enter to Confirm, Assistant Message Bubbles, Show Tool Call Output, and a switch for the Generate Message Pair shortcut. Published into /static/loader.js through the shared static-asset registry, so it coexists with Theme Designer Pro and any other plugin using it.
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://buymeacoffee.com/iamg30
version: 1.0.0
license: MIT
required_open_webui_version: 0.11.0
"""

import hashlib
import json as _json
import logging
import time

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

STORE_DIR_NAME = "interface_toggles"
DISABLED_CHECK_SECONDS = 1.0
STALE_MARKER_SECONDS = 10

log = logging.getLogger(__name__)


# ===========================================================================
# Shared static-asset registry
# --- KEEP BYTE-IDENTICAL IN EVERY PLUGIN THAT USES IT ----------------------
# ---------------------------------------------------------------------------
# app.html loads /static/loader.js and /static/custom.css on every page, and
# loader.js is the only hook running before the SvelteKit bundle hydrates. Two
# URLs, many plugins - so none may own either. Each publishes a fragment into
# one app.state registry and the route composes them PER REQUEST, so load order
# is irrelevant, a late plugin needs no cooperation, and a re-exec'd one
# replaces its own key. Per-process: each container serves what it has loaded.
#
# Fragments are inlined, never <script src> / @import - a second request would
# land after hydration, defeating the point.
#
# Contract:
#   * ASSET_REGISTRY_ATTR, ASSET_ROUTE_ATTR, the entry shape and the paths are
#     the interop surface. Everything else is implementation owned by whichever
#     plugin created the route - a stale copy silently serves everyone, hence
#     ASSET_IMPL_VERSION and byte-identity.
#   * `key` must be a module-level constant. Derive it from a build id or a
#     function id and a re-exec registers a SECOND entry - duplicated output,
#     not just a leaked closure.
#   * `order` breaks ties: lower composes first, so on custom.css it loses the
#     cascade and on loader.js it wraps innermost. Default 0. Use it instead of
#     encoding priority in the key, which would only work if every plugin
#     renamed at once.
#   * Producers run SYNCHRONOUSLY on the event loop, on every request, and
#     BEFORE the ETag is compared - so a 304 costs exactly what a 200 costs.
#     "Cheap" is per-call work, not payload size: memoise anything that
#     parses, formats or regexes and return a prebuilt string. No I/O, no
#     locks, no sleeps. Budget tens of microseconds, not milliseconds.
#   * To withdraw, return "" - there is no unregister. A disabled plugin still
#     gets function.disable_started (it fires before is_active flips), but a
#     DELETED one never sees its own deletion, so disable before deleting or
#     the fragment serves until that process restarts.
#   * Reach is the SPA only. A plugin serving its own HTML page loads neither
#     asset and must inject its own.
# ===========================================================================
LOADER_PATH = "/static/loader.js"
CUSTOM_CSS_PATH = "/static/custom.css"
SHARED_ASSET_TYPES = {
    LOADER_PATH: "application/javascript; charset=utf-8",
    CUSTOM_CSS_PATH: "text/css; charset=utf-8",
}
ASSET_REGISTRY_ATTR = "_owui_static_fragments"  # {path: {key: entry}}
ASSET_ROUTE_ATTR = "_owui_shared_asset"  # set to the path the route serves
ASSET_IMPL_ATTR = "_owui_shared_asset_impl"  # implementation version of the route
# Bump when this block changes behaviour: newer evicts older, so the fleet
# converges on one implementation instead of whichever plugin booted first.
ASSET_IMPL_VERSION = 4

# Producer failures are reported once per (path, key, exception type) - compose
# runs on every page load, so an unconditional warning would be a firehose.
_ASSET_WARNED: set = set()


def asset_fragments(app: Any, path: str) -> dict:
    registry = getattr(app.state, ASSET_REGISTRY_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.__setattr__(ASSET_REGISTRY_ATTR, registry)
    bucket = registry.get(path)
    if not isinstance(bucket, dict):
        bucket = {}
        registry[path] = bucket
    return bucket


def asset_sort_key(item):
    """(order, key). Coerced defensively: a non-int order from a third-party
    plugin would raise inside sorted(), outside the per-fragment guard, and
    take down the whole asset."""
    key, entry = item
    try:
        order = int(entry.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return (order, key)


def asset_strip_block(content: str, start_marker: str, end_marker: str) -> str:
    """Remove every marker-wrapped block, leaving other content untouched."""
    while start_marker in content:
        start = content.find(start_marker)
        end = content.find(end_marker, start)
        if end == -1:
            # No end marker: the block was appended last, so drop to EOF.
            content = content[:start]
            break
        end += len(end_marker)
        if content[end : end + 1] == "\n":
            end += 1
        content = content[:start] + content[end:]
    return content


def asset_compose(app: Any, path: str) -> str:
    """Disk file plus every registered fragment, in (order, key) order."""
    import logging

    try:
        from open_webui.env import STATIC_DIR

        target = Path(STATIC_DIR) / path.rsplit("/", 1)[-1]
        body = (
            ""
            if (target.is_symlink() or not target.is_file())
            else target.read_text(encoding="utf-8")
        )
    except Exception:
        body = ""

    ordered = sorted(asset_fragments(app, path).items(), key=asset_sort_key)
    # Strip first: an older file-writing build may have left a block on disk.
    for _key, entry in ordered:
        body = asset_strip_block(body, entry["start"], entry["end"])
    body = body.rstrip()

    for key, entry in ordered:
        try:
            block = (entry["js"]() or "").strip()
        except Exception as exc:
            mark = (path, key, type(exc).__name__)
            if mark not in _ASSET_WARNED:
                if len(_ASSET_WARNED) > 256:
                    _ASSET_WARNED.clear()
                _ASSET_WARNED.add(mark)
                logging.getLogger("owui-shared-assets").warning(
                    "fragment %r failed for %s - it will be omitted",
                    key,
                    path,
                    exc_info=True,
                )
            continue
        if block:
            body = (body + "\n\n" if body else "") + block
    return body + "\n" if body else ""


def asset_register(
    app: Any, path: str, key: str, start: str, end: str, producer, order: int = 0
) -> None:
    """Publish a fragment and ensure the route exists. Idempotent, and safe
    from any plugin in any order."""
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    asset_fragments(app, path)[key] = {
        "start": start,
        "end": end,
        "js": producer,
        "order": order,
    }

    for existing in app.routes:
        if getattr(existing, ASSET_ROUTE_ATTR, None) != path:
            continue
        if getattr(existing, ASSET_IMPL_ATTR, 0) >= ASSET_IMPL_VERSION:
            return  # an equal or newer implementation already owns the route
        break  # ours is newer - fall through and replace it

    # Replaces a single-owner route from an older build, or an older impl of
    # this block. Fragments live on app.state, so nothing is lost.
    app.routes[:] = [r for r in app.routes if getattr(r, "path", "") != path]
    media_type = SHARED_ASSET_TYPES.get(path, "text/plain; charset=utf-8")

    async def serve_asset(request):
        content = asset_compose(app, path)
        etag = (
            '"owui-'
            # usedforsecurity=False: this is a cache validator, not a security
            # primitive, and a bare md5() raises ValueError on a FIPS host -
            # which would 500 the asset for every visitor.
            + hashlib.md5(
                (path + "\x00" + content).encode("utf-8"), usedforsecurity=False
            ).hexdigest()
            + '"'
        )
        # no-cache, NOT no-store: a response the browser may not store has no
        # validator, so If-None-Match is never sent and the 304 below is dead
        # code. no-cache still forbids reuse without revalidation, so a stale
        # body is impossible either way. Note a proxy may re-add no-store for
        # these paths, which puts the 304 back to sleep - that is deployment
        # policy, not this block's business.
        headers = {
            "Cache-Control": "no-cache, must-revalidate, private",
            "ETag": etag,
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        # Starlette only auto-appends charset for text/*, so JS would ship
        # undeclared and readers guessing latin-1 get mojibake.
        return Response(content, media_type=media_type, headers=headers)

    insert_at = len(app.routes)
    for position, existing in enumerate(app.routes):
        if isinstance(existing, Mount) and getattr(existing, "name", "") == "static":
            insert_at = position
            break
    shared = Route(path, serve_asset, methods=["GET"])
    setattr(shared, ASSET_ROUTE_ATTR, path)
    setattr(shared, ASSET_IMPL_ATTR, ASSET_IMPL_VERSION)
    app.routes.insert(insert_at, shared)


# =========================== end shared asset block ========================


UIT_CSS = r"""
html[data-uit-stack="off"] [data-sonner-toast][data-mounted="true"][data-expanded="false"][data-front="false"][data-removed="false"]{--y:translateY(calc(var(--lift) * var(--offset)));height:var(--initial-height)}
html[data-uit-stack="off"] [data-sonner-toast][data-removed="true"][data-front="false"][data-swipe-out="false"][data-expanded="false"]{--y:translateY(calc(var(--lift) * var(--offset) + var(--lift) * -100%));height:var(--initial-height);transition:transform 400ms,opacity 400ms,height 400ms,box-shadow 200ms}
html[data-uit-stack="off"] [data-sonner-toast][data-expanded="false"][data-front="false"][data-styled="true"]>*{opacity:1}
html[data-uit-bubbles="on"] #response-content-container:not(.hidden):has(> *){width:fit-content;max-width:90%;padding:.375rem 1rem;border-radius:1.5rem;background:var(--color-gray-50,#f9f9f9)}
html.dark[data-uit-bubbles="on"] #response-content-container:not(.hidden):has(> *){background:var(--color-gray-850,#262626)}
html[data-uit-bubbles="on"][data-osk="bars"] #response-content-container:has(> div:first-child > .osk-host[data-osk-kind="cursor"], .osk-host[data-osk-kind="dot"]){width:100%}
html[data-uit-green="on"] button[role="switch"][data-state="checked"]{background-color:var(--color-emerald-500,oklch(69.6% 0.17 162.48))!important}
html.dark[data-uit-green="on"] button[role="switch"][data-state="checked"]{background-color:var(--color-emerald-700,oklch(50.8% 0.118 165.612))!important}
html.dark[data-uit-green="on"] button[role="switch"][data-state="checked"]>span{background-color:var(--color-white,#fff)!important}
html[data-uit-output="off"] div[role="button"] + div > div.border.rounded-2xl.p-2\.5.space-y-2 > div:has(> div.w-full.max-w-none\!:not(.tool-call-body)),html[data-uit-output="off"] div[role="button"] + div > div.border.rounded-2xl.p-2\.5.space-y-2:not(:has(> div > div.tool-call-body, > div > div.px-1.space-y-0\.5)){display:none!important}
html[data-uit-hints="always"] #sidebar-new-chat-button > .md\:flex.text-xs,html[data-uit-hints="always"] #sidebar-search-button > .md\:flex.text-xs{opacity:1!important;visibility:visible!important}
html[data-uit-hints="hidden"] #sidebar-new-chat-button > .md\:flex.text-xs,html[data-uit-hints="hidden"] #sidebar-search-button > .md\:flex.text-xs{display:none!important}
html[data-uit-ts="always"] [id^="message-"] time[datetime],html[data-uit-ts="always"] [id^="responses-container-"] ~ * time[datetime]{opacity:1!important;visibility:visible!important;pointer-events:auto!important}
html[data-uit-ts="hidden"] [id^="message-"] :has(> time[datetime]),html[data-uit-ts="hidden"] [id^="message-"] time[datetime],html[data-uit-ts="hidden"] [id^="responses-container-"] ~ * :has(> time[datetime]),html[data-uit-ts="hidden"] [id^="responses-container-"] ~ * time[datetime]{display:none!important}
"""

SWITCH_ROOT = "focus-ring relative h-4 min-h-4 w-7 shrink-0 cursor-pointer rounded-full mx-[0.0625rem] transition-colors duration-150"
SWITCH_THUMB = "pointer-events-none absolute top-[0.125rem] block h-3 w-3 shrink-0 rounded-full transition-all duration-150 data-[state=checked]:left-[0.875rem] data-[state=checked]:bg-white data-[state=checked]:dark:bg-black data-[state=unchecked]:left-[0.125rem] data-[state=unchecked]:bg-white data-[state=unchecked]:dark:bg-gray-500"

LOADER_SCRIPT = r"""
(function () {
  'use strict';
  if (window.__owuiInterfaceToggles) return;
  window.__owuiInterfaceToggles = true;

  var CFG = __CONFIG__;
  var CSS = __CSS__;
  var KEY = 'owui-interface-toggles';
  var ON = ['bg-gray-900', 'dark:bg-white'];
  var OFF = ['bg-gray-300', 'dark:bg-gray-700'];
  var TIMES = ['hover', 'always', 'hidden'];
  var TIME_LABELS = { hover: 'On Hover', always: 'Always', hidden: 'Hidden' };
  var scheduled = false;

  function saved() {
    try {
      var value = JSON.parse(localStorage.getItem(KEY) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch (e) {
      return {};
    }
  }

  function get(name) {
    var value = saved()[name];
    if (name === 'timestamps' || name === 'hints') {
      if (TIMES.indexOf(value) !== -1) return value;
      if (name === 'hints' && typeof value === 'boolean') return value ? 'hover' : 'hidden';
      return CFG[name];
    }
    return typeof value === 'boolean' ? value : CFG[name];
  }

  function set(name, value) {
    var all = saved();
    all[name] = value;
    try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) {}
    apply();
    repaint();
  }

  function attr(name, value) {
    var root = document.documentElement;
    if (value) root.setAttribute(name, value);
    else root.removeAttribute(name);
  }

  function apply() {
    attr('data-uit-stack', get('stack') ? '' : 'off');
    attr('data-uit-bubbles', get('bubbles') ? 'on' : '');
    attr('data-uit-green', get('green') ? 'on' : '');
    attr('data-uit-output', get('output') ? '' : 'off');
    var hints = get('hints');
    attr('data-uit-hints', hints === 'hover' ? '' : hints);
    var times = get('timestamps');
    attr('data-uit-ts', times === 'hover' ? '' : times);
  }

  function ensureStyle() {
    if (document.getElementById('owui-uit-style')) return;
    var style = document.createElement('style');
    style.id = 'owui-uit-style';
    style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  var painters = [];

  function repaint() {
    painters = painters.filter(function (paint) { return paint(); });
  }

  function anchorBlock(labelId) {
    var label = document.querySelector('#tab-interface #' + labelId);
    if (!label) return null;
    var row = label.parentElement;
    var block = row && row.parentElement;
    if (!block || !block.parentElement) return null;
    return { label: label, row: row, block: block };
  }

  function makeBlock(id, anchor, labelText, note, control) {
    var block = document.createElement('div');
    block.id = id;
    var line = document.createElement('div');
    line.className = anchor.row.className || 'flex items-center justify-between gap-2.5';
    var name = document.createElement('div');
    name.id = id + '-label';
    name.className = anchor.label.className || 'min-w-0 text-xs text-gray-600 dark:text-gray-400';
    name.textContent = labelText;
    var holder = document.createElement('div');
    holder.className = (anchor.row.children[1] && anchor.row.children[1].className) || 'flex shrink-0 items-center justify-end gap-1.5';
    holder.appendChild(control);
    line.appendChild(name);
    line.appendChild(holder);
    var text = document.createElement('p');
    var sample = anchor.block.querySelector('p');
    text.className = sample ? sample.className : 'mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600';
    text.textContent = note;
    block.appendChild(line);
    block.appendChild(text);
    return block;
  }

  function makeSwitch(name, labelledBy) {
    var template = document.querySelector('#tab-interface button[role="switch"]');
    var sw = document.createElement('button');
    sw.type = 'button';
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-labelledby', labelledBy);
    var thumb = document.createElement('span');
    var rootBase = CFG.switchRoot;
    thumb.className = CFG.switchThumb;
    if (template) {
      rootBase = template.className.split(/\s+/).filter(function (c) { return c && ON.indexOf(c) === -1 && OFF.indexOf(c) === -1; }).join(' ');
      if (template.firstElementChild) thumb.className = template.firstElementChild.className;
    }
    sw.appendChild(thumb);
    function paint() {
      var on = get(name);
      sw.className = rootBase + ' ' + (on ? ON : OFF).join(' ');
      sw.setAttribute('aria-checked', String(on));
      sw.setAttribute('data-state', on ? 'checked' : 'unchecked');
      thumb.setAttribute('data-state', on ? 'checked' : 'unchecked');
      sw.title = on ? 'Enabled' : 'Disabled';
    }
    sw.addEventListener('click', function () { set(name, !get(name)); });
    paint();
    painters.push(function () {
      if (!sw.isConnected) return false;
      paint();
      return true;
    });
    return sw;
  }

  function makeCycle(name, labelledBy) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white';
    var mode = document.createElement('span');
    mode.id = labelledBy.replace(/-label$/, '-mode');
    button.setAttribute('aria-labelledby', labelledBy + ' ' + mode.id);
    button.appendChild(mode);
    function paint() {
      mode.textContent = TIME_LABELS[get(name)];
    }
    button.addEventListener('click', function () {
      set(name, TIMES[(TIMES.indexOf(get(name)) + 1) % TIMES.length]);
    });
    paint();
    painters.push(function () {
      if (!button.isConnected) return false;
      paint();
      return true;
    });
    return button;
  }

  function insertAfter(block, anchorEl) {
    anchorEl.parentElement.insertBefore(block, anchorEl.nextSibling);
  }

  function settingRows() {
    var contrastAnchor = anchorBlock('accessibility-mode-label') || anchorBlock('high-contrast-mode-label');
    if (contrastAnchor && !document.getElementById('uit-green')) {
      insertAfter(makeBlock('uit-green', contrastAnchor, 'Green Switches', 'Show switches that are turned on in green, as Open WebUI did before v0.11.0. Saved in this browser.', makeSwitch('green', 'uit-green-label')), contrastAnchor.block);
    }
    var titleAnchor = anchorBlock('use-chat-title-as-tab-title-label');
    if (titleAnchor && !document.getElementById('uit-hints')) {
      insertAfter(makeBlock('uit-hints', titleAnchor, 'Sidebar Hotkey Hints', 'When the keyboard shortcut shows beside New Chat and Search in the sidebar. Saved in this browser.', makeCycle('hints', 'uit-hints-label')), titleAnchor.block);
    }
    var bubbleAnchor = anchorBlock('chat-bubble-ui-label');
    if (bubbleAnchor && !document.getElementById('uit-bubbles')) {
      insertAfter(makeBlock('uit-bubbles', bubbleAnchor, 'Assistant Message Bubbles', 'Show model replies in bubbles, like your own messages with Chat Bubble UI. Saved in this browser.', makeSwitch('bubbles', 'uit-bubbles-label')), bubbleAnchor.block);
    }
    var bubbles = document.getElementById('uit-bubbles');
    if (bubbleAnchor && bubbles && !document.getElementById('uit-timestamps')) {
      insertAfter(makeBlock('uit-timestamps', bubbleAnchor, 'Message Timestamps', 'When the time shows next to each message. Saved in this browser.', makeCycle('timestamps', 'uit-timestamps-label')), bubbles);
    }
    var expandAnchor = anchorBlock('always-expand-label');
    if (expandAnchor && !document.getElementById('uit-output')) {
      insertAfter(makeBlock('uit-output', expandAnchor, 'Show Tool Call Output', 'Show the Output section, the raw result a tool returned, when you expand a tool call in a reply. Saved in this browser.', makeSwitch('output', 'uit-output-label')), expandAnchor.block);
    }
    var pasteAnchor = anchorBlock('paste-large-label');
    if (pasteAnchor && !document.getElementById('uit-pair')) {
      insertAfter(makeBlock('uit-pair', pasteAnchor, 'Generate Message Pair Shortcut', 'Ctrl+Shift+Enter adds your message and a placeholder reply without asking the model. Turn this off to stop that. Saved in this browser.', makeSwitch('pair', 'uit-pair-label')), pasteAnchor.block);
    }
    var copyAnchor = anchorBlock('copy-formatted-label');
    if (copyAnchor && !document.getElementById('uit-stack')) {
      insertAfter(makeBlock('uit-stack', copyAnchor, 'Stack Notifications', 'Pile notifications on top of each other until you point at them. Turn this off to see up to three at once. Saved in this browser.', makeSwitch('stack', 'uit-stack-label')), copyAnchor.block);
    }
    var stack = document.getElementById('uit-stack');
    if (copyAnchor && stack && !document.getElementById('uit-enter')) {
      insertAfter(makeBlock('uit-enter', copyAnchor, 'Enter to Confirm', 'When a confirmation dialog opens, start on its Confirm button so Enter confirms. Tab still reaches Cancel. Saved in this browser.', makeSwitch('enter', 'uit-enter-label')), stack);
    }
  }

  var seenDialogs = [];

  function confirmDialogs() {
    seenDialogs = seenDialogs.filter(function (dialog) { return dialog.isConnected; });
    var rows = document.querySelectorAll('.modal [role="dialog"] div.mt-5.flex.justify-between');
    for (var i = 0; i < rows.length; i++) {
      var dialog = rows[i].closest('[role="dialog"]');
      if (!dialog || seenDialogs.indexOf(dialog) !== -1) continue;
      seenDialogs.push(dialog);
      if (!get('enter')) continue;
      var buttons = rows[i].querySelectorAll('button');
      if (buttons.length < 2) continue;
      focusLater(dialog, buttons[buttons.length - 1]);
    }
  }

  function focusLater(dialog, button) {
    setTimeout(function () {
      if (!button.isConnected || button.disabled) return;
      var active = document.activeElement;
      if (active && active !== document.body && dialog.contains(active) && active.tagName !== 'BUTTON') return;
      button.focus({ preventScroll: true });
    }, 0);
  }

  function scan() {
    scheduled = false;
    settingRows();
    confirmDialogs();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(scan);
  }

  window.addEventListener('click', function (evt) {
    var target = evt.target;
    if (!target || target.id !== 'generate-message-pair-button' || get('pair')) return;
    evt.preventDefault();
    evt.stopImmediatePropagation();
  }, true);

  window.addEventListener('storage', function (evt) {
    if (evt.key !== KEY && evt.key !== null) return;
    apply();
    repaint();
  });

  ensureStyle();
  apply();

  function start() {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
    schedule();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
"""


class Event:
    class Valves(BaseModel):
        default_stack_notifications: bool = Field(
            default=True,
            description="Stack notifications for people who have not changed the setting themselves. Open WebUI stacks them by default.",
        )
        default_assistant_bubbles: bool = Field(
            default=False,
            description="Show model replies in bubbles for people who have not changed the setting themselves.",
        )
        default_hotkey_hints: Literal["hover", "always", "hidden"] = Field(
            default="hover",
            description="When the keyboard shortcut shows beside New Chat and Search in the sidebar for people who have not changed the setting themselves: 'hover' (Open WebUI's default), 'always', or 'hidden'.",
        )
        default_green_switches: bool = Field(
            default=False,
            description="Show switches that are turned on in green for people who have not changed the setting themselves, as Open WebUI did before v0.11.0.",
        )
        default_enter_to_confirm: bool = Field(
            default=False,
            description="Start confirmation dialogs on their Confirm button, so Enter confirms, for people who have not changed the setting themselves. Open WebUI starts them on Cancel.",
        )
        default_show_tool_call_output: bool = Field(
            default=True,
            description="Show the Output section of an expanded tool call for people who have not changed the setting themselves. Open WebUI shows it.",
        )
        default_message_pair_shortcut: bool = Field(
            default=True,
            description="Keep the Generate Message Pair shortcut (Ctrl+Shift+Enter) working for people who have not changed the setting themselves. Open WebUI has it on.",
        )
        default_timestamps: Literal["hover", "always", "hidden"] = Field(
            default="hover",
            description="When message timestamps show for people who have not changed the setting themselves: 'hover' (Open WebUI's default), 'always', or 'hidden'.",
        )

    LOADER_BLOCK_START = "// owui-interface-toggles:start"
    LOADER_BLOCK_END = "// owui-interface-toggles:end"
    ASSET_KEY = "interface-toggles"
    ASSET_ORDER = 0

    _function_disabled = False
    _disabled_cache = None
    _disabled_at = 0.0
    _marker_ok = True
    _fragment_cache = None

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _disabled_marker() -> Path:
        from open_webui.env import DATA_DIR

        return Path(DATA_DIR) / STORE_DIR_NAME / "disabled"

    @classmethod
    def _set_disabled(cls, disabled: bool) -> None:
        cls._function_disabled = disabled
        cls._disabled_at = time.time() if disabled else 0.0
        cls._disabled_cache = (time.monotonic(), disabled)
        cls._fragment_cache = None
        try:
            marker = cls._disabled_marker()
            if disabled:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
            cls._marker_ok = True
        except OSError:
            cls._marker_ok = False
            log.exception(
                "[Interface Toggles] Could not record the on/off state for other workers"
            )

    @classmethod
    def _is_disabled(cls) -> bool:
        now = time.monotonic()
        cached = cls._disabled_cache
        if cached and now - cached[0] < DISABLED_CHECK_SECONDS:
            return cached[1]
        if cls._marker_ok:
            try:
                disabled = cls._disabled_marker().exists()
            except OSError:
                disabled = cls._function_disabled
        else:
            disabled = cls._function_disabled
        cls._disabled_cache = (now, disabled)
        return disabled

    @classmethod
    def _disabled_age(cls) -> float:
        try:
            return time.time() - cls._disabled_marker().stat().st_mtime
        except OSError:
            return time.time() - cls._disabled_at if cls._disabled_at else 0.0

    def _loader_fragment(self) -> str:
        cache_key = (
            Event._is_disabled(),
            self.valves.default_stack_notifications,
            self.valves.default_assistant_bubbles,
            self.valves.default_timestamps,
            self.valves.default_hotkey_hints,
            self.valves.default_message_pair_shortcut,
            self.valves.default_green_switches,
            self.valves.default_show_tool_call_output,
            self.valves.default_enter_to_confirm,
        )
        cached = Event._fragment_cache
        if cached and cached[0] == cache_key:
            return cached[1]
        if cache_key[0]:
            fragment = ""
        else:
            config = _json.dumps(
                {
                    "stack": self.valves.default_stack_notifications,
                    "bubbles": self.valves.default_assistant_bubbles,
                    "timestamps": self.valves.default_timestamps,
                    "hints": self.valves.default_hotkey_hints,
                    "pair": self.valves.default_message_pair_shortcut,
                    "green": self.valves.default_green_switches,
                    "output": self.valves.default_show_tool_call_output,
                    "enter": self.valves.default_enter_to_confirm,
                    "switchRoot": SWITCH_ROOT,
                    "switchThumb": SWITCH_THUMB,
                }
            )
            js = (
                LOADER_SCRIPT.strip()
                .replace("__CONFIG__", config)
                .replace("__CSS__", _json.dumps(UIT_CSS.strip()))
            )
            fragment = f"{self.LOADER_BLOCK_START}\n{js}\n{self.LOADER_BLOCK_END}"
        Event._fragment_cache = (cache_key, fragment)
        return fragment

    def _publish(self, app) -> None:
        asset_register(
            app,
            LOADER_PATH,
            self.ASSET_KEY,
            self.LOADER_BLOCK_START,
            self.LOADER_BLOCK_END,
            self._loader_fragment,
            order=self.ASSET_ORDER,
        )

    async def event(
        self,
        event: dict,
        __event_name__: str = None,
        __id__: str = None,
        __app__=None,
        **kwargs,
    ) -> None:
        if __event_name__ in ("system.shutdown.started", "system.shutdown.completed"):
            return

        own_toggle = ((event or {}).get("subject") or {}).get("id") == __id__
        if __event_name__ == "function.disable_started" and own_toggle:
            Event._set_disabled(True)
            return

        if __event_name__ == "function.enable_started" and own_toggle:
            Event._set_disabled(False)
        elif Event._is_disabled():
            if Event._disabled_age() < STALE_MARKER_SECONDS:
                return
            Event._set_disabled(False)

        if __app__ is None:
            return
        try:
            self._publish(__app__)
        except Exception:
            log.exception("[Interface Toggles] Could not publish the loader fragment")
