---
name: PATH mailroom
description: How to answer questions about packages, checked-in mail and custody with the read-only PATH tools. Load it before any PATH lookup (my packages, who collected, what is waiting).
---

# PATH mailroom (read only)

PATH records packages and checked-in mail with recipients, check-in, custody and activity. Tools (prefixed `path_`): `path_lookup_employees`, `path_search_records`, `path_count_records`, `path_get_record_status`, `path_get_record_details`, `path_get_record_history`, `path_get_records`, `path_get_activity`, `path_get_filter_values`. PATH is read-only: never promise to check in, collect, reassign, edit, delete or send anything.

## Planning

1. **Identity first.** For "my packages", take the id/itcode from `<user_context>` and validate it with `path_lookup_employees`; then filter by that exact recipient. Never use "me", a UUID or "everyone" as a fallback. If a name is ambiguous, clarify with the user.
2. **Prefer counts and status.** `path_count_records` for "how many"; `path_get_record_status` for one record; `path_search_records` for lists. Discover valid filter values with `path_get_filter_values` instead of guessing.
3. **Retain** returned cursors, totals and failures, and continue pages only when needed.
4. **Dates** use America/New_York with daylight-saving-aware RFC 3339 offsets, explicit date fields and half-open intervals; get the current date from the time tools.
5. Use `path_get_record_details` and `path_get_record_history` only for the specific record the user asks about.

## What the records mean

- A recipient-name search matches current recipient aliases and receiver labels, including unassigned labels: label fuzzy matches separately from exact ownership. Do not infer teams.
- WAITING means recorded as uncollected, not verified shelf presence.
- Keep these apart: current recipient, check-in actor, physical collector, reassignment, and the administrator marker. Say "Marked as picked up by <admin> on <date>" for an admin mark: it proves neither physical collection by that admin nor that anyone was notified.
- Checked-in MAIL has no waiting/pickup state and no guaranteed current location.
- Counts are stock; activity is action flow; physical pickups are separate from admin marks.
- Missing coverage is unknown, not zero.

## Never expose

Photos, raw labels or OCR text, addresses, private notes, credentials or raw audit payloads. Record routes are authenticated links: give them as returned. Explain material errors and a useful next step without blind retries or invented results.
