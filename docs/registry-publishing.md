# Before publishing registry metadata

Run the read-only version check from the repository root:

```sh
python3 -m unittest discover -s scripts -p test_registry_preflight.py -v
python3 scripts/registry_preflight.py server.json
```

The official MCP Registry requires a unique version for every publication.
Published metadata is immutable, including descriptions. Editing `server.json`
without choosing a new version cannot update an existing registry record.
See the [registry versioning rules](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/versioning.mdx).

The check makes an unauthenticated GET for the exact server/version. It stops if
the version is already published, or if a network error, rate limit, malformed
response or unexpected record prevents verification. An exact 404 means that
version was not found; it does not prove publisher authorization or validate the
rest of the manifest.

Review the final capability description against the live tool catalog and
approved source, then change the version in `server.json` in a PR. When that
merges, the **Publish to MCP Registry** workflow runs this check and publishes.
Run the workflow manually to publish the current version again, for example
after a failed publish.
