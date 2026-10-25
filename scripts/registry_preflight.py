#!/usr/bin/env python3
"""Read-only guard against publishing an already immutable MCP Registry version."""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def check_unpublished(manifest, opener=urllib.request.urlopen):
    name = manifest.get("name")
    version = manifest.get("version")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("server.json needs a nonempty name")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("server.json needs a nonempty version")
    url = (
        "https://registry.modelcontextprotocol.io/v0.1/servers/"
        + urllib.parse.quote(name, safe="")
        + "/versions/"
        + urllib.parse.quote(version, safe="")
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError(f"Registry verification returned HTTP {response.status}")
            # Do not interpret an HTML challenge or malformed payload as a publication.
            record = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return url
        raise RuntimeError(f"Registry verification failed: HTTP {error.code}") from error
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Registry verification failed: {error}") from error
    server = record.get("server") if isinstance(record, dict) else None
    if not isinstance(server, dict) or server.get("name") != name or server.get("version") != version:
        raise RuntimeError("Registry returned an unexpected server/version; publication stopped")
    raise ValueError(
        f"{name} {version} is already published. Registry versions and metadata are immutable. "
        "Choose a new version in server.json, review the metadata, and dispatch again. "
        f"Existing record: {url}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", default="server.json", type=Path)
    args = parser.parse_args()
    try:
        url = check_unpublished(json.loads(args.manifest.read_text()))
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Version is not published: {url}. This check does not publish or validate authorization.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
