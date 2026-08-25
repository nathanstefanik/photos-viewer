from app.validation import is_uuid


def test_valid_uuid():
    assert is_uuid("550e8400-e29b-41d4-a716-446655440000")


def test_valid_uuid_uppercase():
    assert is_uuid("550E8400-E29B-41D4-A716-446655440000")


def test_invalid_uuid():
    assert not is_uuid("not-a-uuid")
    assert not is_uuid("")
    assert not is_uuid(None)


def test_invalid_uuid_wrong_length():
    assert not is_uuid("550e8400-e29b-41d4-a716-44665544000")
