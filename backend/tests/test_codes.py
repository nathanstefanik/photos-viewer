from app.codes import ALPHABET, generate_code


def test_generate_code_length():
    assert len(generate_code(6)) == 6
    assert len(generate_code(8)) == 8


def test_generate_code_alphabet_only():
    code = generate_code(200)
    assert all(c in ALPHABET for c in code)


def test_alphabet_excludes_ambiguous_chars():
    # Unambiguous for verbal relay: no 0/O, 1/I
    for bad in "0O1I":
        assert bad not in ALPHABET


def test_generate_code_is_randomized():
    codes = {generate_code(6) for _ in range(50)}
    assert len(codes) > 40
