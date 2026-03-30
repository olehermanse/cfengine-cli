import os
from cfengine_cli.paths import cfengine_dir, bin


def test_cfengine_dir():
    a = os.path.abspath(os.path.expanduser(cfengine_dir()))
    b = os.path.abspath(os.path.expanduser("~/.cfengine"))

    assert a == b

    a = os.path.abspath(os.path.expanduser(cfengine_dir("subdir")))
    b = os.path.abspath(os.path.expanduser("~/.cfengine/subdir"))

    assert a == b


def test_bin():
    assert bin("cf-agent") == "/var/cfengine/bin/cf-agent"
    assert bin("cf-hub") == "/var/cfengine/bin/cf-hub"
