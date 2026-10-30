#!/usr/bin/env bash
set -euo pipefail
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BENCH/../shared/env.sh"
SOURCE_IMAGE=ghcr.io/harveyai/lab-sandbox@sha256:0695466b5b1278b2b979b78de0f845b6e81b9d3cbd70ee6a6ec6a430a0599b23
if ! podman image inspect "$SOURCE_IMAGE" >/dev/null 2>&1; then
    podman pull "$SOURCE_IMAGE"
fi
podman tag "$SOURCE_IMAGE" toolspace-harvey:8133051
