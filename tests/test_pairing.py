import pytest

from gp_agent.pairing import CODE_TTL_S, MAX_ATTEMPTS, Pairing, PairingLocked, TokenStore


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def pairing(clock):
    return Pairing(clock=clock)


def wrong_code(code: str) -> str:
    return "000000" if code != "000000" else "111111"


class TestPairing:
    def test_code_is_six_digits(self, pairing):
        code = pairing.start()
        assert len(code) == 6 and code.isdigit()

    def test_correct_code_confirms_once(self, pairing):
        code = pairing.start()
        assert pairing.confirm(code)
        assert not pairing.confirm(code)

    def test_wrong_code_rejected(self, pairing):
        code = pairing.start()
        assert not pairing.confirm(wrong_code(code))
        assert pairing.confirm(code)

    def test_no_active_code(self, pairing):
        assert not pairing.confirm("123456")

    def test_code_expires(self, pairing, clock):
        code = pairing.start()
        clock.now += CODE_TTL_S + 1
        assert not pairing.confirm(code)

    def test_locks_after_max_attempts(self, pairing):
        code = pairing.start()
        for _ in range(MAX_ATTEMPTS):
            pairing.confirm(wrong_code(code))
        with pytest.raises(PairingLocked):
            pairing.confirm(code)

    def test_new_start_resets_attempts(self, pairing):
        pairing.start()
        for _ in range(MAX_ATTEMPTS):
            pairing.confirm("abcdef")
        code = pairing.start()
        assert pairing.confirm(code)

    @pytest.mark.parametrize("bad", [None, "", "12345", "1234567", "abcdef"])
    def test_malformed_code_rejected(self, pairing, bad):
        pairing.start()
        assert not pairing.confirm(bad)


class TestTokenStore:
    def test_missing_file_has_no_token(self, tmp_path):
        assert TokenStore(tmp_path / "token").load() is None

    def test_load_or_create_persists(self, tmp_path):
        path = tmp_path / "sub" / "token"
        token = TokenStore(path).load_or_create()
        assert len(token) >= 32
        assert TokenStore(path).load() == token
        assert TokenStore(path).load_or_create() == token

    def test_blank_file_is_regenerated(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("  \n")
        assert TokenStore(path).load() is None
        assert TokenStore(path).load_or_create().strip()
