# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.14-bookworm
FROM ${PYTHON_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libgl1 \
        libx11-6 \
        libx11-xcb1 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-util1 \
        libxcb-xkb1 \
        libxkbcommon0 \
        libxkbcommon-x11-0 \
        libxcb1 \
        libxext6 \
        libxi6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv==0.11.28

WORKDIR /app
COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src
RUN uv sync --dev --frozen --no-editable

COPY packaging ./packaging
RUN uv run pyinstaller --noconfirm --clean packaging/hosts-manager-gui.spec

FROM scratch AS artifact
COPY --from=builder /app/dist/hosts-manager-gui /hosts-manager-gui
