from __future__ import annotations

import argparse
import io
import json
import tarfile
import tomllib
import urllib.request
from pathlib import Path

TRAEFIK_VERSION = "v3.7.8"
LEGO_VERSION = "v5.2.2"
ARCHIVE = f"https://github.com/go-acme/lego/archive/refs/tags/{LEGO_VERSION}.tar.gz"
ALIASES = {
    "acme-dns": "acmedns",
    "domainnameshop": "domeneshop",
    "fastdns": "edgedns",
    "linodev4": "linode",
    "rfc2136": "dnsupdate",
    "webnames": "webnamesru",
}


def generate() -> dict:
    request = urllib.request.Request(ARCHIVE, headers={"User-Agent": "evdb-dns-catalog"})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive = response.read()

    providers = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        for member in source.getmembers():
            parts = Path(member.name).parts
            if (
                not member.isfile()
                or len(parts) != 5
                or parts[1:3] != ("providers", "dns")
                or not parts[-1].endswith(".toml")
            ):
                continue
            opened = source.extractfile(member)
            if opened is None:
                continue
            data = tomllib.loads(opened.read().decode())
            code = data.get("Code")
            name = data.get("Name")
            if not isinstance(code, str) or not isinstance(name, str):
                continue
            configuration = data.get("Configuration", {})
            providers.append(
                {
                    "code": code,
                    "name": name,
                    "description": str(data.get("Description", "")).strip(),
                    "url": str(data.get("URL", "")).strip(),
                    "help": f"https://go-acme.github.io/lego/dns/{code}/",
                    "credentials": dict(sorted(configuration.get("Credentials", {}).items())),
                    "additional": dict(sorted(configuration.get("Additional", {}).items())),
                }
            )
    providers.sort(key=lambda item: item["code"])
    if not providers:
        raise RuntimeError("lego archive contained no DNS provider metadata")
    return {
        "traefik": TRAEFIK_VERSION,
        "lego": LEGO_VERSION,
        "aliases": ALIASES,
        "providers": providers,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Update evdb's pinned lego DNS provider catalog")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "src/evdb/dns_providers.json",
    )
    args = parser.parse_args()
    text = json.dumps(generate(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    args.output.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
