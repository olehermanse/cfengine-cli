#!/bin/bash

set -e
set -x

# Setup: create a temp directory for test files
tmpdir=$(mktemp -d)
trap "rm -rf $tmpdir" EXIT

write_formatted() {
	printf 'bundle agent main\n{\n  vars:\n    "v" string => "hello";\n}\n' > "$1"
}

write_unformatted() {
	printf 'bundle agent main { vars: "v" string => "hello"; }\n' > "$1"
}

# Case 1: format without --check on already-formatted file -> exit 0
write_formatted "$tmpdir/good.cf"
cfengine format "$tmpdir/good.cf"

# Case 2: format without --check on unformatted file -> exit 0 (reformats it)
write_unformatted "$tmpdir/bad.cf"
cfengine format "$tmpdir/bad.cf"
# Verify it was actually reformatted to the correct output
write_formatted "$tmpdir/expected.cf"
diff "$tmpdir/expected.cf" "$tmpdir/bad.cf"

# Case 3: --check on already-formatted file -> exit 0
write_formatted "$tmpdir/good2.cf"
cfengine format --check "$tmpdir/good2.cf"

# Case 4: --check on unformatted file -> exit 1
write_unformatted "$tmpdir/bad2.cf"
cp "$tmpdir/bad2.cf" "$tmpdir/bad2_orig.cf"
if cfengine format --check "$tmpdir/bad2.cf"; then
	echo "FAIL: expected exit code 1 for --check on unformatted file"
	exit 1
fi
# Verify the file was NOT modified
diff "$tmpdir/bad2_orig.cf" "$tmpdir/bad2.cf"
