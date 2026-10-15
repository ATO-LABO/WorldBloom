#!/usr/bin/env bash
set -euo pipefail
version=8.30.1
archive="${RUNNER_TEMP}/gitleaks_${version}_linux_x64.tar.gz"
curl --fail --silent --show-error --location \
  "https://github.com/gitleaks/gitleaks/releases/download/v${version}/gitleaks_${version}_linux_x64.tar.gz" \
  --output "$archive"
printf '%s  %s\n' '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' "$archive" | sha256sum --check --status
mkdir -p "${RUNNER_TEMP}/worldbloom-gitleaks"
tar -xzf "$archive" -C "${RUNNER_TEMP}/worldbloom-gitleaks" gitleaks
printf '%s\n' "${RUNNER_TEMP}/worldbloom-gitleaks" >> "$GITHUB_PATH"
