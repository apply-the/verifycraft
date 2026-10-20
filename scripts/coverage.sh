#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "${1:-}" == "--html" ]]; then
    python3 -m pytest --cov --cov-branch --cov-report=html:htmlcov --cov-report=term
    echo "Coverage HTML report generated at htmlcov/index.html"
elif [[ "${1:-}" == "--summary" ]]; then
    python3 -m pytest --cov --cov-branch --cov-report=term
else
    python3 -m pytest --cov --cov-branch --cov-report=xml:coverage.xml --cov-report=term
    echo "Generated coverage.xml successfully."
fi
