# htmlcmp

Tool to compare (generated) HTML files visually and automatically using Selenium.

Provides various entry points to run:
- `compare-html` is a CLI tool to compare two directory structures containing HTML files
- `compare-html-server` starts a webserver and allows to inspect differences manually
- `html-render-diff` renders two HTML files and produces images
- `html-tidy` allows to run HTML tidy on a directory

Used for regression testing in https://github.com/opendocument-app/OpenDocument.core.

## Install via PyPI

```bash
pip install htmlcmp
```

## Download and run the docker image

```bash
docker pull ghcr.io/opendocument-app/odr_core_test
```

```bash
docker run -ti \
  -v $(pwd):/repo \
  -p 8000:8000 \
  ghcr.io/opendocument-app/odr_core_test \
  compare-html-server /repo/REFERENCE /repo/MONITORED --compare --driver firefox --port 8000
```

## Manually build the docker image

```bash
docker build --tag odr_core_test test/docker
```
