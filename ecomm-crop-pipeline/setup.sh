#!/usr/bin/env bash
# Create a local venv and install pipeline dependencies.
# Usage: ./setup.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo
echo "Done. Activate with: source $HERE/.venv/bin/activate"
echo "Run pipeline with:   $HERE/.venv/bin/python -m crop_pipeline.cli --help"
