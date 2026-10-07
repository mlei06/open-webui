---
name: Terminal workspace
description: How to use the user's Open Terminal workspace for files: where things live, how to share downloads, and the rules for running commands. Load it when a task involves terminal files, projects, or delivering a file the user did not get from a generator.
---

# The user's terminal workspace

When a terminal is selected, read `~/workspace/README.md` before workspace tasks. Layout under `~/workspace`: `inbox` for files the user supplies, `projects` for code, `assets` for reusable material, `output` for deliverables. Relative paths mean `~/workspace`; `~/folder` is the home itself. The user's agents can read the read-only `/shared` area but can only write inside their own home.

## Working rules

- Inspect what is there and preserve originals. Never guess paths or file contents: list the folder first.
- Chat attachments and terminal files are separate. Use `import_attachment` to put an attached file into the workspace, and `publish_workspace_file(path)` to give the user a download link for any terminal file. Ask for a missing file rather than guessing.
- Generators save to `~/workspace/output` by default and never overwrite (a taken name gets a short suffix, and the result says where it went). Use their `save_to` parameter to choose another folder instead of copying with the shell.
- Give the user the download link from a tool result (`terminal_download_url` or `download_url`), one per file, exactly as returned. Keep `workspace_path` for reuse by tools such as slide generation.
- Use project-specific dependencies. For a web app, retain the process ID and show the native port preview; localhost is not a public link.
- Verify an output (open it, check its size or content) before claiming success.
- Do not use the shell to get around read-only service rules, the mail-send restriction or data-handling rules, and never use it to copy files that a tool can deliver.
