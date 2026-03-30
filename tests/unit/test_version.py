from cfengine_cli.version import cfengine_cli_version_string


def test_cfengine_cli_version_string():
    assert type(cfengine_cli_version_string()) is str
    assert cfengine_cli_version_string().startswith("CFEngine CLI ")
