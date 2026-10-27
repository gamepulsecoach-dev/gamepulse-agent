import pytest

from gp_agent.security import is_allowed_origin, new_token, token_matches


class TestIsAllowedOrigin:
    @pytest.mark.parametrize("origin", ["https://gamepulsecoach.com", "https://www.gamepulsecoach.com"])
    def test_production_always_allowed(self, origin):
        assert is_allowed_origin(origin, allow_dev=False)

    @pytest.mark.parametrize(
        "origin",
        [
            "https://gamepulse-deploy-jjbjpgzd1-jose07ferreira-2613s-projects.vercel.app",
            "http://localhost:3000",
            "http://127.0.0.1:5500",
        ],
    )
    def test_dev_origins_only_when_enabled(self, origin):
        assert is_allowed_origin(origin, allow_dev=True)
        assert not is_allowed_origin(origin, allow_dev=False)

    @pytest.mark.parametrize(
        "origin",
        [
            None,
            "",
            "null",
            "http://gamepulsecoach.com",
            "https://gamepulsecoach.com.evil.com",
            "https://evilgamepulsecoach.com",
            "https://gamepulsecoach.com:8443",
            "https://other-app.vercel.app",
            "https://gamepulse-deploy-x.vercel.app.evil.com",
        ],
    )
    def test_rejects_others_even_in_dev(self, origin):
        assert not is_allowed_origin(origin, allow_dev=True)


class TestTokens:
    def test_new_token_is_long_and_unique(self):
        a, b = new_token(), new_token()
        assert a != b
        assert len(a) >= 40

    def test_matches_same_token(self):
        token = new_token()
        assert token_matches(token, token)

    @pytest.mark.parametrize("provided", [None, "", "wrong", "x" * 43])
    def test_rejects_wrong_or_missing(self, provided):
        assert not token_matches(new_token(), provided)

    def test_empty_expected_never_matches(self):
        assert not token_matches("", "")
