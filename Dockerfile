FROM ubuntu:26.04

# Populated automatically by Docker Buildx (amd64 / arm64)
ARG TARGETARCH

ENV FIREFOX_VERSION="138.0.4"
ENV GECKODRIVER_VERSION="0.36.0"

ENV INSTALL="apt-get install -y --no-install-recommends"

# Every install refreshes the package index in its own layer: a cached index
# goes stale once the archive moves on, and its downloads then 404.
RUN apt-get update && \
    $INSTALL wget ca-certificates xz-utils bzip2 unzip \
      libgtk-3-0t64 libasound2t64 libx11-xcb1 python3 python3-pip && \
    pip config set global.break-system-packages true

RUN case "$TARGETARCH" in \
      amd64) FFARCH=linux-x86_64 ;; \
      arm64) FFARCH=linux-aarch64 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" && exit 1 ;; \
    esac && \
    wget https://download-installer.cdn.mozilla.net/pub/firefox/releases/${FIREFOX_VERSION}/${FFARCH}/en-US/firefox-${FIREFOX_VERSION}.tar.xz && \
    tar -xf firefox-${FIREFOX_VERSION}.tar.xz && \
    mv firefox /usr/local/share && \
    ln -s /usr/local/share/firefox/firefox /usr/local/bin && \
    rm firefox-${FIREFOX_VERSION}.tar.xz

RUN firefox --version

# geckodriver setup
RUN case "$TARGETARCH" in \
      amd64) GDARCH=linux64 ;; \
      arm64) GDARCH=linux-aarch64 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" && exit 1 ;; \
    esac && \
    wget https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-${GDARCH}.tar.gz && \
    tar -xf geckodriver-v${GECKODRIVER_VERSION}-${GDARCH}.tar.gz && \
    mv geckodriver /usr/local/bin && \
    rm geckodriver-v${GECKODRIVER_VERSION}-${GDARCH}.tar.gz

RUN geckodriver --version

# chrome setup (Google Chrome ships no ARM64 Linux build, so amd64 only;
# use --driver firefox, the default, on arm64)
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
      apt-get update && \
      $INSTALL ./google-chrome-stable_current_amd64.deb && \
      rm google-chrome-stable_current_amd64.deb && \
      google-chrome --version ; \
    else \
      echo "Skipping Google Chrome on $TARGETARCH (no ARM64 build; use --driver firefox)" ; \
    fi

# install python dependencies
COPY . /compare-html
RUN pip install /compare-html
