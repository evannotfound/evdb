import pytest

from evdb import dns
from evdb.errors import ConfigError


def test_catalog_matches_pinned_traefik_and_lego():
    value = dns.catalog()

    assert value["traefik"] == "v3.7.8"
    assert value["lego"] == "v5.2.2"
    assert len(dns.providers()) == 217
    assert [item["code"] for item in dns.providers()] == sorted(
        item["code"] for item in dns.providers()
    )
    assert dns.provider("cloudflare")["name"] == "Cloudflare"
    assert tuple(dns.provider("cloudflare")["credentials"]) == (
        "CF_DNS_API_TOKEN",
        "CF_ZONE_API_TOKEN",
    )
    assert all(
        marker not in description.casefold()
        for item in dns.providers()
        for variables in (item["credentials"], item["additional"])
        for description in variables.values()
        for marker in ("alias", "deprecated")
    )


def test_provider_search_aliases_and_variables_are_bounded():
    assert dns.normalize("rfc2136") == "dnsupdate"
    assert [item["code"] for item in dns.search("Cloudflare")] == ["cloudflare"]
    dns.validate_variables("cloudflare", {"CF_DNS_API_TOKEN": "private"})

    with pytest.raises(ConfigError, match="unsupported DNS provider"):
        dns.normalize("not-a-provider")
    with pytest.raises(ConfigError, match="unsupported DNS variable"):
        dns.validate_variables("cloudflare", {"MADE_UP_TOKEN": "private"})
    with pytest.raises(ConfigError, match="unsupported DNS variable"):
        dns.validate_variables("cloudflare", {"CLOUDFLARE_DNS_API_TOKEN": "private"})
    with pytest.raises(ConfigError, match="unsupported DNS variable"):
        dns.validate_variables("cloudflare", {"CF_API_KEY": "private"})


def test_catalog_version_check_rejects_other_traefik_images():
    dns.require_versions("traefik:v3.7.8")

    with pytest.raises(ConfigError, match="catalog requires"):
        dns.require_versions("traefik:v3.7.9")
