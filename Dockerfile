FROM --platform=linux/amd64 ubuntu:24.04

ENV FIREFOX_VERSION="138.0.4"
ENV PHANTOMJS_VERSION="2.1.1"
ENV GECKODRIVER_VERSION="0.36.0"

ENV INSTALL="apt-get install -y --no-install-recommends"

RUN apt-get update

# install download and unpack utilities
RUN $INSTALL wget ca-certificates xz-utils bzip2 unzip

# firefox setup
RUN $INSTALL libgtk-3-0t64 libasound2t64 libx11-xcb1

RUN wget https://download-installer.cdn.mozilla.net/pub/firefox/releases/${FIREFOX_VERSION}/linux-x86_64/en-US/firefox-${FIREFOX_VERSION}.tar.xz && \
    tar -xf firefox-${FIREFOX_VERSION}.tar.xz && \
    mv firefox /usr/local/share && \
    ln -s /usr/local/share/firefox/firefox /usr/local/bin && \
    rm firefox-${FIREFOX_VERSION}.tar.xz

RUN firefox --version

# geckodriver setup
RUN wget https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz && \
    tar -xf geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz && \
    mv geckodriver /usr/local/bin && \
    rm geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz

RUN geckodriver --version

# chrome setup
RUN wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    $INSTALL ./google-chrome-stable_current_amd64.deb && \
    rm google-chrome-stable_current_amd64.deb

RUN google-chrome --version

# phantomjs setup
RUN $INSTALL build-essential chrpath libssl-dev libxft-dev \
    libfreetype6 libfreetype6-dev libfontconfig1 libfontconfig1-dev

RUN wget https://bitbucket.org/ariya/phantomjs/downloads/phantomjs-${PHANTOMJS_VERSION}-linux-x86_64.tar.bz2 && \
    tar -xf phantomjs-${PHANTOMJS_VERSION}-linux-x86_64.tar.bz2 && \
    mv phantomjs-${PHANTOMJS_VERSION}-linux-x86_64/bin/phantomjs /usr/local/bin && \
    rm -rf phantomjs-${PHANTOMJS_VERSION}-linux-x86_64 && \
    rm phantomjs-${PHANTOMJS_VERSION}-linux-x86_64.tar.bz2

# fix weird phantomjs issue
ENV OPENSSL_CONF=/etc/ssl

RUN phantomjs --version

# install python dependencies
COPY . /compare-html
RUN $INSTALL python3 python3-pip && \
    pip config set global.break-system-packages true && \
    pip install /compare-html
