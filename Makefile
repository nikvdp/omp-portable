ifeq ($(shell uname -s),Darwin)
    export SDKROOT := $(shell xcrun --show-sdk-path)
    export LIBRARY_PATH := $(SDKROOT)/usr/lib
endif
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3

.PHONY: release test

# Local release prep: bump the release revision, commit it, and print the
# remaining (remote) steps. Never pushes.
release:
	@set -e; \
	test "$$(git symbolic-ref --short -q HEAD)" = main || { echo "release: not on main" >&2; exit 1; }; \
	git diff --quiet && git diff --cached --quiet || { echo "release: uncommitted changes; commit or stash first" >&2; exit 1; }; \
	rev=$$(sed -n 's/^RELEASE_REVISION = \([0-9][0-9]*\).*/\1/p' scripts/release.py); \
	test -n "$$rev" || { echo "release: RELEASE_REVISION not found in scripts/release.py" >&2; exit 1; }; \
	next=$$((rev + 1)); \
	tag=$$($(PYTHON) -c "import json; print('omp-' + json.load(open('config/upstream-lock.json'))['tag'] + '-r' + '$$next')"); \
	$(PYTHON) -c "import pathlib, re; p = pathlib.Path('scripts/release.py'); s = p.read_text(); s2 = re.sub(r'^RELEASE_REVISION = [0-9]+', 'RELEASE_REVISION = $$next', s, count=1, flags=re.M); assert s2 != s; p.write_text(s2)"; \
	msg=$$(mktemp); \
	printf 'Bump release revision to r%s for a rebuilt release\n\n- Increment RELEASE_REVISION in scripts/release.py\n\nThe existing r%s release is complete, so the release workflow would skip a\nrebuild. r%s publishes a fresh %s tag and never overwrites published releases.\n' "$$next" "$$rev" "$$next" "$$tag" > "$$msg"; \
	git commit scripts/release.py -F "$$msg"; \
	rm -f "$$msg"; \
	echo; echo "Release prep done (nothing was pushed)."; echo; \
	echo "Next:"; echo "  1. git push origin main"; \
	echo "     The push-triggered verify workflow runs all 8 builds as a pre-flight."; echo; \
	echo "  2. Trigger the release: GitHub Actions -> 'Release OMP Lite and Portable'"; \
	echo "     -> Run workflow with the publish checkbox checked (or wait for the"; \
	echo "     hourly cron at minute :49, which publishes automatically)."; \
	echo "     CI will build and publish $$tag."

test:
	$(PYTHON) -m unittest discover -s tests -v