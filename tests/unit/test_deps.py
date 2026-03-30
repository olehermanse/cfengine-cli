"""Just some very basic tests to ensure we can use functions from the
cfbs and cf-remote dependencies"""

from cf_remote.utils import canonify
from cfbs.utils import is_a_commit_hash


def test_cfbs_is_a_commit_hash():
    assert is_a_commit_hash("6072060c63c5f6bc74897bf4be2681d3e9d3bf32")
    assert not is_a_commit_hash("blah")


def test_cf_remote_canonify():
    assert canonify(" CF-REMOTE ") == "cf-remote"
