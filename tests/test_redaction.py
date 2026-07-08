from werktools.redaction import is_secret_key, mask_secret_text, redact_payload


class TestIsSecretKey:
    def test_classic_markers_match(self):
        for key in ("api_key", "apikey", "Authorization", "access_key", "password", "private_key", "secret", "token"):
            assert is_secret_key(key) is True

    def test_new_markers_match(self):
        for key in ("jwt", "bearer", "client_secret", "refresh_token", "credential", "user-credentials"):
            assert is_secret_key(key) is True

    def test_auth_requires_word_boundary(self):
        assert is_secret_key("auth") is True
        assert is_secret_key("basic_auth") is True
        assert is_secret_key("auth_header") is True
        assert is_secret_key("author") is False
        assert is_secret_key("authored_by") is False
        assert is_secret_key("authors") is False

    def test_plain_keys_do_not_match(self):
        for key in ("status", "name", "payload", "mission", "ok"):
            assert is_secret_key(key) is False


class TestRedactPayload:
    def test_redacts_nested_dicts(self):
        payload = {"token": "x", "nested": {"password": "y", "ok": 1}}
        result = redact_payload(payload)
        assert result["token"] == "[redacted]"
        assert result["nested"]["password"] == "[redacted]"
        assert result["nested"]["ok"] == 1

    def test_recurses_into_lists_and_tuples(self):
        payload = {"items": ({"api_key": "x"}, [{"jwt": "y"}])}
        result = redact_payload(payload)
        assert result["items"][0]["api_key"] == "[redacted]"
        assert result["items"][1][0]["jwt"] == "[redacted]"

    def test_author_value_is_preserved(self):
        assert redact_payload({"author": "jane"}) == {"author": "jane"}


class TestMaskSecretText:
    def test_masks_inline_value(self):
        masked = mask_secret_text("api_key: 12345\nname: alpha")
        assert "12345" not in masked
        assert "api_key: [redacted]" in masked
        assert "name: alpha" in masked

    def test_masks_yaml_block_scalar_continuation(self):
        text = "password: |\n  alpha\n  bravo\nok: fine"
        masked = mask_secret_text(text)
        assert "alpha" not in masked
        assert "bravo" not in masked
        assert "ok: fine" in masked

    def test_masks_empty_value_continuation(self):
        text = "secret:\n    charlie\nplain: keep"
        masked = mask_secret_text(text)
        assert "charlie" not in masked
        assert "plain: keep" in masked

    def test_plain_text_unchanged(self):
        text = "# Notes\n\njust regular prose"
        assert mask_secret_text(text) == text
