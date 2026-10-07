---
name: Mail drafting
description: How to draft an email for the user to review, and when (only on an explicit instruction) to send it yourself with send_draft, including for scheduled automations and attachments. Load it before creating, updating or sending a draft.
---

# Mail drafting and sending

The mail tools are `create_draft`, `update_draft`, `get_draft`, `list_drafts`, `discard_draft` and, when the mail service has sending enabled, `send_draft`. **The default is to draft for the user to review**: they check it in the **Review and send email** form and press Send. If `send_draft` is not among your tools, sending by a model is switched off: leave the draft and tell the user to press the button.

## When you may send

Call `send_draft` **only** when:
- the user explicitly tells you to send it ("send it", "email Dana the summary now", "send that to the team"), or
- a scheduled automation's prompt explicitly instructs you to send (for example "every morning send the summary to ...").

Never send because a draft looks finished, because the user said "write", "draft", "prepare", "compose" or "put together" an email, or because the request is ambiguous. When the user wants a draft, create it and point them to **Review and send email**. When in doubt about whether to send, who to, or what to say, do not send: ask one short question or leave the draft.

## Drafting (always)

1. Draft only when asked. Confirm the recipients: use only addresses the user gave or that a tool returned for the named person (for example from QDTS). Never guess an address or merge namesakes; ask when unsure.
2. Write a clear subject and a concise body signed with the user's name from `<user_context>`. Add no Bcc. The server chooses the sender.
3. **Attachments**: include only files the user asked for, as ids in `suggested_attachment_ids` (at most five), never file contents or base64.
   - A file the user attached to the message: use its supplied id.
   - A file that has no supplied id (a generated document, a chart, a file in the user's terminal): call `prepare_email_attachments([...])` with its attachment id or its terminal path (relative paths are under `~/workspace`, for example `~/workspace/output/report.pptx`), then pass the returned `attachment_ids` as `suggested_attachment_ids`. Terminal paths need a selected terminal.
   - **A draft you send yourself cannot carry attachments** (only the review form uploads files). If the user asked you to send with an attachment, create the draft with the suggestion, tell the user to press **Review and send email** to attach and send it, and do not call `send_draft`.
4. `create_draft` (or `update_draft` for changes), then show a short summary of the draft (recipients, subject, body) and, unless you are sending, direct the user to **Review and send email**. They can untick any attachment there.
5. Update or discard only on request.

## Sending (only when allowed above)

1. Make sure the recipients are explicit and exactly what the user or the automation named; a lookup that returns several people is ambiguous, so do not send.
2. `create_draft` (use `update_draft` for any change), keeping the `version` it returns.
3. Call `send_draft(draft_id, version)` **once**. Passing `version` makes it fail if the draft changed since you wrote it.
4. Report the result from the tool, never from assumption: say it was sent (to whom, from which address, the subject) only when `send_draft` or `get_draft` shows status `sent`. If the call fails (policy, recipient, rate limit, mail system refusal), say it was **not** sent and why, and leave the draft for the user. A mail system refusal keeps the draft; do not loop. If the outcome is uncertain, check `get_draft` instead of sending again, because a second send is a duplicate email.
5. Each send is limited per user per hour and goes only to company addresses; do not try to get around either.

## Scheduled automations

A scheduled task has nobody to ask. Its prompt must say who to email (addresses, or a clear rule such as "the owner of each overdue case"), what to say or where the content comes from, and that it should be sent. Follow it exactly: one draft and one `send_draft` per intended email, no attachments. If a recipient is missing or ambiguous, or the content could not be produced, **do not send**: leave a note in the chat that the run needs attention. Never add recipients the prompt did not name.

## Related

Generated deck, document or chart for the email: get it made first (see **powerpoint**, **visualization**, **delegation**), then suggest it as an attachment as above.
