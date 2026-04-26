from app.services.acme import find_matching_zone, normalize_zone, normalize_fqdn


def test_normalize_zone():
    assert normalize_zone("EXAMPLE.com") == "example.com."
    assert normalize_zone("foo.") == "foo."
    assert normalize_zone("") == ""


def test_normalize_fqdn():
    assert normalize_fqdn("A.B.c") == "a.b.c."


def test_find_matching_zone_longest_wins():
    z = find_matching_zone(
        "mail.example.com.",
        ["example.com.", "mail.example.com."],
    )
    assert z == "mail.example.com."


def test_find_matching_zone_parent():
    z = find_matching_zone(
        "sub.mail.example.com.",
        ["example.com."],
    )
    assert z == "example.com."


def test_find_matching_zone_none():
    assert find_matching_zone("other.tld.", ["example.com."]) is None
