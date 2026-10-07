# Accounts and access

Who can sign in, what every user can see, and how the stack checks it.

## Accounts

Sign-up is closed (`ENABLE_SIGNUP=false`, also saved in Open WebUI by `bootstrap/accounts.py`, because saved settings
override the environment) and the default role is `user`. Only an admin creates accounts: **Admin Panel > Users > Add
User** (name, email, password, role `user`), or **CSV Import** on the same dialog with a file whose first row is a header
and whose other rows are `Name,Email,Password,Role` (role `admin`, `user` or `pending`; the template is at
`/static/user-import.csv`). There is no script in this repository for a users file: keep a private
`runtime/users.csv` out of git and import it through that dialog. The first account on a fresh volume is the one
`provision.py` creates from `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`: Open WebUI lets the first sign-up
through whatever `ENABLE_SIGNUP` says, and every later sign-up gets HTTP 403. `accounts.py --check` reports drift.

The user's identity inside models is the account's name, email and an id derived from the email local part, supplied
by the user context filter ([functions.md](functions.md#user-context-filter)); it is a query hint, never authorization.

## Visible to every user

Provisioning grants, and `bootstrap/access.py` audits (read-only, the last step of `provision.py`), access for
**all users** to:

| Object | Grant |
|---|---|
| Model presets and the base model | Public read (they appear in everyone's selector; a preset is unusable if its base model has no registered row with a read grant) |
| Workspace tools | Public read, **except** `visuals_toolkit_v4`, which is committed private (`access_grants: []`) and is reported as "not usable by all users" |
| Tool server connections | Public read in the connection config ([mcp.md](mcp.md)) |
| The SOPs knowledge base | Public read **and write**, so users can add and edit files; nothing ever deletes a user's addition |
| The review and send email action | Active, not global, attached to the models that list it (Lenny, Office Agent) |
| Skills | Public read ([skills.md](skills.md)) |
| The user context filter | Global (runs for every user) |
| The terminal connection | Public read once confined (`open_terminal.py --access all`, [terminal.md](terminal.md)) |

No object type is limited to admins: Open WebUI can grant all-user access to each. What stays private is what users
create: their own knowledge bases and files, which tools reach only with the signed-in user's own token. Each user has
their own terminal home, and the terminal refuses any path outside it (and the read-only `/shared`).
`tests/stack_e2e.py` proves visibility and privacy on a throwaway stack with a second, ordinary user.

Non-admin users see only models that have a registered row with a read grant; a connection or tool without access is
hidden silently. Function creation stays admin-only. QDTS is the deliberate exception to per-user data boundaries:
every granted user can read every loaded case and note ([mcp.md](mcp.md#qdts-cases)).

## Audit

`access.py` prints PASS or FAIL per object type and changes nothing: a FAIL means "run `provision.py` again" or
somebody edited a grant in the app. Administrative and security events (sign-ins, user and role changes, config,
plugin and model changes) are recorded by the audit event function ([functions.md](functions.md#audit-log)); it does
not record chat content.
