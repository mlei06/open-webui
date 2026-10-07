# Existing Terminal, with reproducible local Plotly static export dependencies.
ARG TERMINAL_BASE_IMAGE=ghcr.io/open-webui/open-terminal:0.14.0@sha256:81a5394b3cd4ae32adb600f2135f09ee124de37f26b0a780e2f5692472c0fc5c
FROM ${TERMINAL_BASE_IMAGE}
USER root
ARG CHROMIUM_VERSION=154.0.8037.92-1~deb13u1
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium=${CHROMIUM_VERSION} fonts-liberation=1:2.1.5-3 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir plotly==7.0.0 kaleido==1.2.0 choreographer==1.4.0
ENV BROWSER_PATH=/usr/bin/chromium
# Confine the multi-user file API to the caller's home plus the read-only /shared area. The launcher
# is first on PATH, so the base entrypoint's `exec open-terminal` starts it; it refuses to start if
# the patch cannot apply. See docs/terminal.md.
COPY Michael/terminal/confine/michael_confine.py /opt/michael/michael_confine.py
COPY --chmod=0755 Michael/terminal/confine/open-terminal /opt/michael/bin/open-terminal
ENV PATH=/opt/michael/bin:${PATH}
USER user
