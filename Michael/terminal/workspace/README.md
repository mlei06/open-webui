# Your terminal workspace

Use `~/workspace` for this user's work. Read this guide before starting a task.

- `inbox/`: upload source files through Open WebUI's terminal file browser.
- `assets/`: reusable images, fonts and templates you own.
- `projects/`: one directory per development project.
- `output/`: finished documents, images and videos to display/download.
- `scratch/`: temporary intermediates; remove only your own task files.
- `scripts/`: starter document and media helpers.

Files under your home persist across container recreation. Running processes do not.
The terminal file browser is separate from chat attachments and Knowledge files;
do not assume an uploaded chat attachment is present on disk. Ask the user to upload
it here or use a supported attachment tool. Never read another user's home.

## Installed tools

Python, Node/npm, Git, FFmpeg/ffprobe, ImageMagick, LibreOffice and Pandoc are in the
pinned base image. Python includes python-pptx, python-docx, Pillow, pandas, openpyxl,
FastAPI and Uvicorn. `environment.json` records installed versions at setup time.
Use FFmpeg for video/audio transformations and Pillow/ImageMagick for raster edits.
This provides editing tools, not a generative image/video model.

Create project-specific environments rather than modifying system packages:

```sh
cd ~/workspace/projects/my-project
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install <needed-package>
.venv/bin/python -m pip freeze > requirements.lock.txt
# For a Node project: npm init -y, then npm install <needed-package>
# Keep package-lock.json and use npm ci to reproduce it.
```

The system-site-packages option reuses the pinned image's document libraries. Use a
plain venv and fully pinned requirements when a project needs independent versions.
Keep credentials out of project files and generated artifacts.

## Web app starter and preview

```sh
cd ~/workspace/projects/web-starter
python -m uvicorn app:app --host 127.0.0.1 --port 3000
```

Start with the terminal execution tool; retain its returned process ID. Open the
port 3000 preview in Open WebUI. Use another free port for another app; stop only
processes you started. Do not link to container localhost as if the user's browser
can reach it. Use the native port preview. No host-port publishing is needed.
Serve just the app's static directory, never your entire home/inbox. Preview servers
are for development and stop on restart; production deployment is a separate task.
For frontend frameworks, use relative asset/API paths and configure their base path
for the proxy if needed. Ports/resources are shared within this trusted-team container.

## Word and PowerPoint from files

```sh
python ~/workspace/scripts/create_documents.py --source ~/workspace/inbox --output ~/workspace/output/my-delivery
```

The helper makes a simple source packet from top-level `.txt`, `.md`, `.png`, `.jpg`
and `.jpeg` files. It does not interpret every format or invent summaries. It lists
included sources and fails on excessive source content rather than silently trimming.
Choose a new output directory for each delivery; existing outputs are not overwritten.
For polished business documents, use the existing Lenovo generators/templates when
they fit; for local-file processing or custom editing, use python-docx/python-pptx.
Read PDFs, spreadsheets or existing Office documents with suitable tools before
writing an audience-appropriate report; the packet helper is not that workflow.

Inspect outputs with LibreOffice or export to PDF for a visual check:

```sh
libreoffice -env:UserInstallation=file:///tmp/lo-workspace-review --headless --convert-to pdf --outdir ~/workspace/output/review ~/workspace/output/my-delivery/source-packet.pptx
```

Display completed files with the native terminal file display tool or file browser;
return the actual artifact link supplied by the tool, not an invented URL.

## Images and video

```sh
python ~/workspace/scripts/resize_image.py inbox/photo.jpg output/photo-small.png --max-size 1600
ffmpeg -n -i inbox/video.mp4 -ss 00:00:05 -t 00:00:10 -vf 'scale=1280:-2' -c:v libx264 -crf 23 -c:a aac output/clip.mp4
ffmpeg -n -i inbox/video.mp4 -frames:v 1 output/poster.png
ffprobe -v error -show_format -show_streams output/clip.mp4
```

Work on copies and preserve originals. The container has 2 CPUs and 2 GiB RAM;
process short clips incrementally and resize large assets before document insertion.
