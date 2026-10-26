# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim-bookworm


FROM ${PYTHON_IMAGE} AS base
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

FROM base AS librecad
ARG LIBRECAD_VERSION=2.2.1.6
ARG LIBRECAD_SHA256=f2d86e6a40ec684b758c821eff3496a7d36e69f17519cde0b508a66d72c5754c
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/LibreCAD.AppImage \
      "https://github.com/LibreCAD/LibreCAD/releases/download/v${LIBRECAD_VERSION}/LibreCAD-v${LIBRECAD_VERSION}-x86_64.AppImage" \
 && echo "${LIBRECAD_SHA256}  /tmp/LibreCAD.AppImage" | sha256sum -c - \
 && chmod +x /tmp/LibreCAD.AppImage \
 && mkdir -p /opt/librecad \
 && cd /opt/librecad \
 && /tmp/LibreCAD.AppImage --appimage-extract >/dev/null \
 && rm /tmp/LibreCAD.AppImage


FROM base AS libredwg
ARG LIBREDWG_VERSION=0.13.4
ARG LIBREDWG_SHA256=7e153ea4dac4cbf3dc9c50b9ef7a5604e09cdd4c5520bcf8017877bbe1422cd5
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl pkg-config xz-utils \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/libredwg.tar.xz "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz" \
 && echo "${LIBREDWG_SHA256}  /tmp/libredwg.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/libredwg.tar.xz -C /tmp \
 && cd /tmp/libredwg-${LIBREDWG_VERSION} \
 && ./configure --prefix=/opt/libredwg --disable-bindings --disable-static --disable-docs \
 && make -j"$(nproc)" \
 && make install \
 && rm -rf /tmp/libredwg*


FROM base AS app
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
COPY catalog ./catalog
COPY profiles ./profiles
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir .


FROM base
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xauth poppler-utils \
      libgl1 libx11-6 libx11-xcb1 libxcb1 libxext6 libxrender1 libsm6 libice6 \
      libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libfreetype6 libglib2.0-0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY --from=librecad /opt/librecad /opt/librecad
COPY --from=libredwg /opt/libredwg /opt/libredwg
COPY --from=app /opt/venv /opt/venv
COPY docker/entrypoint.sh /usr/local/bin/unicad-entrypoint

RUN echo /opt/libredwg/lib > /etc/ld.so.conf.d/libredwg.conf && ldconfig \
 && useradd --create-home --uid 10001 unicad \
 && mkdir -p /data && chown unicad:unicad /data

ENV PATH=/opt/venv/bin:$PATH \
    LIBRECAD=/opt/librecad/squashfs-root/AppRun \
    LIBREDWG_BIN=/opt/libredwg/bin \
    DISPLAY=:99 \
    UNICAD_WORKSPACE=/data \
    PYTHONUNBUFFERED=1

USER unicad
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"

ENTRYPOINT ["unicad-entrypoint"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8765"]
