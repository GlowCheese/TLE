#!/bin/bash

# Get to a predictable directory, the directory of this script
cd "$(dirname "$0")"

[ -e environment ] && . ./environment

git pull
poetry install
FONTCONFIG_FILE=$PWD/extra/fonts.conf poetry run python -m tle