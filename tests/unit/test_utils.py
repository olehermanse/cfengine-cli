from cfengine_cli.utils import UserError


def test_user_error():
    try:
        raise UserError("foo")
    except UserError as e:
        assert str(e) == "Error: foo"
