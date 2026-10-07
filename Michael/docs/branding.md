# Lenovo branding and theme

`bootstrap/branding.py` applies the Lenovo look to Open WebUI: a dark purple gradient by default with a light variant,
Segoe UI with a self-hosted Archivo fallback, the logo on the sign-in page, loading splash and sidebar, Lenovo red only
on the logo and the selected-chat edge, and blue as the one pressable colour. It delivers the theme through the
**Theme Designer Pro** plugin and does what the designer's Save button does, without opening the designer. Same
conventions as the other bootstrap scripts (PASS/FAIL output, secrets never printed, re-runs change nothing, standard
library only). Preset icons are in [models.md](models.md#icons); PowerPoint and Word styling is in [tools.md](tools.md).

**Licence.** Open WebUI's `LICENSE` forbids replacing its branding for deployments with more than 50 users unless there
is an enterprise licence or written permission. Settle that before applying the theme to a shared stack. Set
`WEBUI_NAME=AI Assistant` (or similar) in `.env` so the header reads "[Lenovo logo] AI Assistant (Open WebUI)"; Open
WebUI always appends "(Open WebUI)".

## Files and assets

- `branding/tokens.json` holds the colour tokens and `branding/theme.css` the rules. Both are committed; neither contains
  a logo or a font.
- The logo and font are brand binaries and are **not committed**. They live in the ignored `runtime/brand/`. The logo is
  embedded in the CSS as a `data:` URI at upload time, so nothing loads from the network. The font is **not** embedded
  (see the incident below): the theme asks for an installed `Archivo` and otherwise falls back to the Segoe UI stack.
- `python3 Michael/bootstrap/branding.py --init-assets --font /path/to/archivo-latin.woff2` writes a **placeholder**
  logo (a red tile with the word "Lenovo", not the official artwork) and copies the font. Drop an official
  `lenovo-logo.svg` or `.png` into `runtime/brand/` to replace the placeholder (the office generators and
  `export_visual`'s deck images use the same logo file, [tools.md](tools.md)).
- **Never embed a large `data:` URI in the theme.** Theme Designer Pro's `loader.js` runs
  `css.replace(/[^{}]*body\s*::before\s*\{[^}]*\}/g, '')` over the whole theme on every repaint; that regex is quadratic in
  the longest brace-free run, so a 90 KB font as a `data:` URI (a 120 KB run) blocked the main thread for 3+ seconds per
  call and the app never got past the splash with no console error. `branding.py` now fails if any brace-free run
  exceeds 6,000 characters.

## Applying it

```sh
python3 Michael/bootstrap/branding.py --init-assets --font /path/to/archivo-latin.woff2
python3 Michael/bootstrap/branding.py            # install, enable, harden, upload, verify
python3 Michael/bootstrap/branding.py --check    # verify only
```

The plugin accepts an admin **session** token only, so besides the admin key the script needs
`OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD` in `.env`. The script installs the plugin from
`tools/theme_designer_pro.py` if missing (the file itself is not committed, see below), enables it, sets its valves to
Canvas FX off, Canvas API access off, community-theme catalogue off and URL import off, uploads the theme and verifies
what users are served. **Do not open the designer and press Save**: that replaces the theme with the designer's own
state; re-run `branding.py` to restore it. `provision.py` runs it as the last step; it is a NOTE when the plugin file or
the logo is absent.

## Theme Designer Pro

A third-party function (author `@G30`, version 1.8.1, MIT, requires Open WebUI 0.11.0 or newer) that is an **event
function** (`class Event`): an instance-wide theme designer that replaces the built-in dark, light, OLED and other modes
with custom themes for all users, registers an interactive UI at `/api/v1/theme-designer` and persists themes
server-side. The source (`tools/theme_designer_pro.py`, about 15,200 lines and 900 KB) is deliberately untracked and
listed in the clone's local git exclude (`.git/info/exclude`), so it is never committed. It runs with the same access
as every function ([functions.md](functions.md#security)) and adds its own route and a loader script served to every
user; it has not been audited here, so review it before updating and do not install it on a new stack without that
review. If you must disable it, use the enable toggle in Admin Panel > Functions, or the rollback below.

How it behaves as an event function (read from its source and checked by installing it on a throwaway stack):
at its first event (startup or enable) it registers its routes before the static mount and publishes its CSS and
loader fragments into a shared static-asset registry on `app.state`, so `/static/custom.css` and `/static/loader.js` are
composed per request; on its own `enable_started` it clears its disabled flag and tells open tabs to refetch; on its own
`disable_started` it withdraws its CSS fragment and broadcasts so the theme disappears without a reload (its route then
answers 503 but stays registered until restart); on valve changes it broadcasts to browsers and, with `REDIS_URL`, to
peer workers; it depends on shutdown events for cleanup, which did not reach functions in our tests (harmless as the
process is exiting). The pattern to copy for features that must exist from the first page load: register on the first
event, make registration idempotent, keep state on `app.state` and class attributes, and handle both startup and enable.

## Verify the render, and roll back a stuck UI

```sh
npm i --prefix Michael/runtime/pw playwright-core      # once; needs a Chromium (CHROME_PATH, or ~/.cache/ms-playwright)
python3 Michael/bootstrap/branding.py --verify-render   # login page and signed-in hard reload, dark and light
```

`--verify-render` (`tests/verify_render.mjs`) fails unless the login form (logged out) or the chat input (signed in)
becomes visible and the splash is removed within `TIMEOUT_MS` (20 s; it also fails if rendering takes more than half
that). Set `SHOTS=dir` to save screenshots. Run it against a throwaway stack after every theme change **before** applying
to the live stack, and again after.

If the UI is stuck on the splash, switch the theme off in one step (needs the admin credentials in `.env`), then
hard-reload the browser (Ctrl+Shift+R):

```sh
python3 Michael/bootstrap/branding.py --rollback
```

This toggles the plugin off through the admin API, so `/static/custom.css` serves 0 bytes; re-run `branding.py` to
re-apply.

## Contrast (WCAG AA), light and dark

```sh
python3 Michael/bootstrap/branding.py --verify-contrast                  # light and dark
python3 Michael/bootstrap/branding.py --verify-contrast --modes light    # one mode
python3 Michael/bootstrap/branding.py --verify-contrast --unseeded       # live stack: skip steps needing seeded chats
```

`--verify-contrast` (`tests/verify_contrast.mjs`, same Node + playwright-core + Chromium setup) drives a real headless
browser through the sign-in page, loading splash, an empty chat, a Markdown conversation (headings, links, tables,
blockquotes, inline code, code blocks), message action icons and tooltips, toasts, the model selector, input menus, the
sidebar in hover, selected and menu states, search, the user menu, settings and every tab, every admin and workspace
page, the create forms, and a phone-width layout. For every visible text node, input value, placeholder, icon and
form-control border it measures the real contrast ratio against what is painted behind it (the page is screenshotted
with all text transparent and every `svg` hidden and the pixels under each item are sampled, so gradients, translucent
panels and backdrop blur are measured as drawn). It exits 1 when any item is under 4.5:1 (text, placeholders) or 3:1
(large text, icons, form-control edges, disabled controls), printing each failing element with its selector, text,
ratio, colours, screen and state. Set `REPORT=file.json` to save failures, `SHOTS=dir` for screenshots, `ONLY=regex` to
audit matching screens, `DUMP=1` to list every measurement.

It needs fixtures to reach every screen: against a **throwaway** stack (own compose project, port and volume), sign up
an admin, point `MICHAEL_ENV_FILE` at a file with that admin's `OPEN_WEBUI_URL`, `OPEN_WEBUI_ADMIN_EMAIL` and
`OPEN_WEBUI_ADMIN_PASSWORD`, and run `tests/seed_contrast_fixtures.py` (synthetic user, chats, folder, model, prompt,
knowledge base, tool, note; it refuses port 3000). Against the live stack use `--unseeded`: it signs in read-only,
creates nothing and audits the screens that exist.

How the theme keeps AA: the light ramp's `gray-300` to `gray-600` are all text-grade (Open WebUI paints inactive tabs,
meta lines, hints and faint sidebar icons with `gray-300` and `gray-400`), the hue palette is darkened for the light
ground, form controls get an edge (`--lnv-edge`) at 3:1, and `theme.css` has a short "Legibility" block for what a ramp
cannot reach (text dimmed by `opacity-NN`, disabled controls, the phone sidebar over its scrim, code syntax colours,
toast text). `branding.py` checks the token pairs statically on every run and
`python3 Michael/tests/test_branding_contrast.py` repeats those checks (plus ramp order and the loader-safety limits)
without a browser. Run `--verify-contrast` against a throwaway stack after every change to `tokens.json` or `theme.css`,
before applying to the live stack.
