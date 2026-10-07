---
name: Mail drafting
description: How to draft an email for the user to review and send with the mail tools, including attaching files. Load it before creating or updating a draft.
---

# Mail drafting

You can draft, never send. The mail tools are `create_draft`, `update_draft`, `get_draft`, `list_drafts` and `discard_draft`. The user reviews the draft in the **Review and send email** form and presses Send; that click is the only thing that sends. Never say or imply that you sent it, and report "sent" only from `get_draft`.

## Sequence

1. Draft only when asked. Confirm the recipients: use only addresses the user gave or that a tool returned for the named person (for example from QDTS). Never guess an address or merge namesakes; ask when unsure.
2. Write a clear subject and a concise body signed with the user's name from `<user_context>`. Add no Bcc. The server chooses the sender.
3. **Attachments**: include only files the user asked for, as ids in `suggested_attachment_ids` (at most five), never file contents or base64.
   - A file the user attached to the message: use its supplied id.
   - A file that has no supplied id (a generated document, a chart, a file in the user's terminal): call `prepare_email_attachments([...])` with its attachment id or its terminal path (relative paths are under `~/workspace`, for example `~/workspace/output/report.pptx`), then pass the returned `attachment_ids` as `suggested_attachment_ids`. Terminal paths need a selected terminal.
4. `create_draft` (or `update_draft` for changes), then show a short summary of the draft and direct the user to **Review and send email**. They can untick any attachment there.
5. Update or discard only on request.

## Related

Generated deck, document or chart for the email: get it made first (see **powerpoint**, **visualization**, **delegation**), then attach it as above.
