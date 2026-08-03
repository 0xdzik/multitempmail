"""Unit tests for tempmail.utils: OTP/link extraction and helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from tempmail.utils import (
    extract_link,
    extract_links,
    extract_otp,
    extract_otps,
    parse_dt,
    random_local_part,
    substring_match,
)


class TestExtractOtp:
    def test_six_digits(self):
        assert extract_otp("Your verification code is 123456.") == "123456"

    def test_split_normalized(self):
        assert extract_otp("Use code 123-456 to sign in") == "123456"
        assert extract_otp("Use code 123 456 to sign in") == "123456"

    def test_alphanumeric(self):
        assert extract_otp("Code: ABC123") == "ABC123"

    def test_prefers_first_pattern(self):
        assert extract_otp("123456 then ABC123") == "123456"

    def test_no_otp(self):
        assert extract_otp("no code here, only 12") is None
        assert extract_otp(None) is None
        assert extract_otp("") is None

    def test_custom_pattern_overrides(self):
        assert extract_otp("code 9876", pattern=r"\d{4}") == "9876"
        assert extract_otp("123456", pattern=r"abcdef") is None

    def test_extract_otps_all(self):
        codes = extract_otps("a 111111 b 222-222 c 333333")
        assert codes == ["111111", "333333", "222222"]

    def test_extract_otps_deduplicates(self):
        assert extract_otps("x 111111 y 111111") == ["111111"]

    def test_split_does_not_match_longer_runs(self):
        assert extract_otp("1234567") is None


class TestExtractLinks:
    def test_plain_text(self):
        text = "Check https://example.com/verify?t=1 and http://sub.example.org/x."
        assert extract_links(text) == ["https://example.com/verify?t=1", "http://sub.example.org/x"]

    def test_no_links(self):
        assert extract_links("just words") == []
        assert extract_links(None) == []

    def test_keyword_filter(self):
        text = "https://a.com/foo and https://b.com/verify"
        assert extract_link(text, keyword="verify") == "https://b.com/verify"

    def test_domain_filter(self):
        text = "https://a.com/x https://sub.b.com/y https://b.com/z"
        assert extract_link(text, domain="b.com") == "https://sub.b.com/y"
        assert extract_link(text, domain="other.org") is None

    def test_www_domain(self):
        assert extract_link("go to https://www.b.com/x", domain="b.com") == "https://www.b.com/x"


class TestRandomLocalPart:
    def test_shape(self):
        part = random_local_part()
        assert re.fullmatch(r"[a-z0-9]{8}", part)

    def test_prefix(self):
        assert random_local_part(prefix="redfox").startswith("redfox")

    def test_pattern_and_max_length(self):
        pattern = "^[a-z0-9._-]+$"
        part = random_local_part(pattern=pattern, max_length=20)
        assert re.fullmatch(pattern, part)
        assert len(part) <= 20

    def test_long_prefix_shrinks(self):
        part = random_local_part(prefix="x" * 19, max_length=20)
        assert len(part) <= 20


class TestParseDt:
    def test_iso(self):
        dt = parse_dt("2024-01-02T03:04:05Z")
        assert dt == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_unix_seconds(self):
        assert parse_dt(1704164645) == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_unix_millis(self):
        assert parse_dt(1704164645000) == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_garbage(self):
        assert parse_dt(None) is None
        assert parse_dt("not a date") is None


class TestSubstringMatch:
    def test_basic(self):
        assert substring_match("Hello World", "hello")
        assert not substring_match("Hello World", "nope")
        assert substring_match(None, None)
        assert substring_match("x", None)
        assert not substring_match(None, "x")
