"""
title: Collapsed Sidebar Pinned Models
description: Shows your pinned models as icons in the collapsed chat sidebar, so a new chat with a favorite model is one click away without opening the sidebar. Published into /static/loader.js through the shared static-asset registry, so it coexists with Theme Designer Pro and any other plugin using it.
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
from typing import Any

STORE_DIR_NAME = "collapsed_sidebar_pinned_models"
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


CSP_CSS = r"""
#owui-csp{display:flex;flex-direction:column;align-items:center;gap:.25rem;margin-top:.25rem;padding-top:.25rem;border-top:1px solid var(--color-gray-100,#f0f0f0);max-height:calc(100vh - 17rem);overflow-y:auto;overflow-x:hidden;scrollbar-width:none}
#owui-csp::-webkit-scrollbar{display:none}
html.dark #owui-csp{border-top-color:var(--color-gray-850,#262626)}
.csp-link{display:flex;flex:none;border-radius:.75rem;cursor:pointer;transition:background-color .15s}
.csp-link:hover{background:var(--color-gray-100,#f3f4f6)}
html.dark .csp-link:hover{background:var(--color-gray-850,#262626)}
.csp-link:focus-visible{outline:2px solid #3b82f6;outline-offset:-2px}
.csp-tile{display:flex;align-items:center;justify-content:center;width:2.25rem;height:2.25rem}
.csp-tile img{width:1.5rem;height:1.5rem;border-radius:9999px;object-fit:cover}
"""

LOADER_SCRIPT = r"""
(function () {
  'use strict';
  if (window.__owuiCollapsedPins) return;
  window.__owuiCollapsedPins = true;

  var CSS = __CSS__;
  var MODELS_TTL = 300000;
  var REFRESH_MS = 60000;
  var models = null;
  var modelsAt = 0;
  var pins = null;
  var loading = null;
  var lastRail = null;
  var scheduled = false;

  function token() {
    try { return localStorage.getItem('token') || ''; } catch (e) { return ''; }
  }

  function get(url) {
    return fetch(url, { headers: { Authorization: 'Bearer ' + token() }, cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; });
  }

  function emptyMeansDefaults(version) {
    var parts = String(version || '').replace(/^v/, '').split('.').map(function (n) { return parseInt(n, 10) || 0; });
    return parts[0] === 0 && parts[1] === 11 && parts[2] === 0;
  }

  function refresh() {
    if (loading || !token()) return loading;
    var needModels = !models || Date.now() - modelsAt > MODELS_TTL;
    loading = Promise.all([
      get('/api/v1/users/user/settings'),
      get('/api/config'),
      needModels ? get('/api/models') : Promise.resolve(null)
    ]).then(function (results) {
      var ui = (results[0] && results[0].ui) || {};
      var defaults = String((results[1] && results[1].default_pinned_models) || '').split(',').filter(Boolean);
      var saved = Array.isArray(ui.pinnedModels) ? ui.pinnedModels : null;
      pins = saved && !(saved.length === 0 && emptyMeansDefaults(results[1] && results[1].version)) ? saved : defaults;
      if (needModels && results[2]) {
        models = Array.isArray(results[2].data) ? results[2].data : Array.isArray(results[2]) ? results[2] : [];
        modelsAt = Date.now();
      }
      loading = null;
      render();
    });
    return loading;
  }

  function visible() {
    if (!pins || !models) return [];
    var byId = {};
    models.forEach(function (m) { if (m && m.id) byId[m.id] = m; });
    return pins
      .map(function (id) { return byId[id]; })
      .filter(function (m) { return m && !(m.info && m.info.meta && m.info.meta.hidden); });
  }

  function rail() {
    var el = document.getElementById('sidebar');
    if (!el || !el.firstElementChild || el.firstElementChild.tagName !== 'BUTTON') return null;
    return el;
  }

  function ensureStyle() {
    if (document.getElementById('owui-csp-style')) return;
    var style = document.createElement('style');
    style.id = 'owui-csp-style';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function navigate(el, href) {
    var helper = document.createElement('a');
    helper.href = href;
    helper.hidden = true;
    el.appendChild(helper);
    helper.click();
    helper.parentNode.removeChild(helper);
  }

  var tip = null;
  var tipFor = null;

  function hideTip() {
    if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
    tip = null;
    tipFor = null;
  }

  function showTip(anchor, text) {
    hideTip();
    if (!text || !anchor.isConnected) return;
    var root = document.createElement('div');
    root.setAttribute('data-tippy-root', '');
    root.style.cssText = 'z-index:9999;visibility:visible;position:absolute;left:0;top:0;margin:0;pointer-events:none';
    var box = document.createElement('div');
    box.className = 'tippy-box';
    box.setAttribute('data-state', 'hidden');
    box.setAttribute('data-theme', 'dark');
    box.setAttribute('data-animation', 'fade');
    box.setAttribute('data-placement', 'right');
    box.setAttribute('role', 'tooltip');
    box.style.maxWidth = '350px';
    box.style.transitionDuration = '300ms';
    var content = document.createElement('div');
    content.className = 'tippy-content';
    content.setAttribute('data-state', 'visible');
    content.textContent = text;
    box.appendChild(content);
    root.appendChild(box);
    document.body.appendChild(root);
    var rect = anchor.getBoundingClientRect();
    var sx = window.scrollX || 0;
    var sy = window.scrollY || 0;
    var y = rect.top + rect.height / 2 - root.offsetHeight / 2 + sy;
    root.style.transform = 'translate(' + Math.round(rect.right + 4 + sx) + 'px, ' + Math.round(y) + 'px)';
    requestAnimationFrame(function () { box.setAttribute('data-state', 'visible'); });
    tip = root;
    tipFor = anchor;
  }

  function link(el, model) {
    var href = '/?model=' + encodeURIComponent(model.id);
    var name = model.name || model.id;
    var a = document.createElement('a');
    a.className = 'csp-link';
    a.href = href;
    a.draggable = false;
    a.setAttribute('aria-label', name);
    a.dataset.id = model.id;
    var tile = document.createElement('span');
    tile.className = 'csp-tile';
    var img = document.createElement('img');
    img.alt = '';
    img.src = '/api/v1/models/model/profile/image?id=' + encodeURIComponent(model.id);
    img.addEventListener('error', function () {
      if (img.dataset.fallback) return;
      img.dataset.fallback = '1';
      img.src = '/static/favicon.png';
    });
    tile.appendChild(img);
    a.appendChild(tile);
    a.addEventListener('mouseenter', function () { showTip(a, name); });
    a.addEventListener('mouseleave', hideTip);
    a.addEventListener('focus', function () { showTip(a, name); });
    a.addEventListener('blur', hideTip);
    a.addEventListener('click', function (evt) {
      evt.stopImmediatePropagation();
      evt.preventDefault();
      hideTip();
      if (evt.ctrlKey || evt.metaKey || evt.shiftKey || evt.button === 1) {
        window.open(href, '_blank', 'noopener');
        return;
      }
      navigate(el, href);
    });
    return a;
  }

  function render() {
    var el = rail();
    if (!el) return;
    var host = el.firstElementChild.lastElementChild;
    if (!host) return;
    var list = visible();
    var block = document.getElementById('owui-csp');
    if (!list.length) {
      if (block) block.parentNode.removeChild(block);
      return;
    }
    ensureStyle();
    var key = list.map(function (m) { return m.id + '\u0000' + (m.name || ''); }).join('\u0001');
    if (!block) {
      block = document.createElement('div');
      block.id = 'owui-csp';
      block.setAttribute('role', 'group');
      block.setAttribute('aria-label', 'Pinned models');
    }
    if (block.dataset.key !== key) {
      block.dataset.key = key;
      block.replaceChildren.apply(block, list.map(function (m) { return link(el, m); }));
    }
    if (block.parentNode !== host || host.lastElementChild !== block) host.appendChild(block);
  }

  function tick() {
    scheduled = false;
    if (tipFor && !tipFor.isConnected) hideTip();
    var el = rail();
    if (el !== lastRail) {
      lastRail = el;
      if (el) refresh();
    }
    if (el) render();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(tick);
  }

  function refreshIfShown() {
    if (!document.hidden && rail()) refresh();
  }

  function start() {
    new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('focus', refreshIfShown);
    document.addEventListener('visibilitychange', refreshIfShown);
    setInterval(refreshIfShown, REFRESH_MS);
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
    LOADER_BLOCK_START = "// owui-collapsed-sidebar-pinned-models:start"
    LOADER_BLOCK_END = "// owui-collapsed-sidebar-pinned-models:end"
    ASSET_KEY = "collapsed-sidebar-pinned-models"
    ASSET_ORDER = 0

    _function_disabled = False
    _disabled_cache = None
    _disabled_at = 0.0
    _marker_ok = True
    _fragment_cache = None

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
                "[Collapsed Sidebar Pinned Models] Could not record the on/off state for other workers"
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
        cache_key = (Event._is_disabled(),)
        cached = Event._fragment_cache
        if cached and cached[0] == cache_key:
            return cached[1]
        if cache_key[0]:
            fragment = ""
        else:
            js = LOADER_SCRIPT.strip().replace("__CSS__", _json.dumps(CSP_CSS.strip()))
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
            log.exception(
                "[Collapsed Sidebar Pinned Models] Could not publish the loader fragment"
            )
