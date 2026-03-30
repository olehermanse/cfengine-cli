#/usr/bin/env bash

set -e
# set -x

echo "These tests expect cfengine CLI to be installed globally or in venv"

echo "Looking for CFEngine CLI:"
cfengine --version

echo "Check that test files are in expected location:"
ls -al tests/shell/*.sh
ls -al tests/shell/00*.sh

rm -rf tmp
mkdir -p tmp

echo "Run shell tests:"
for file in tests/shell/*.sh; do
  bash $file
  echo "OK: $file"
done

echo "All shell tests successful!"
