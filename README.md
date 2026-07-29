# Certiv Labs

Focused tools from the team building runtime assurance for AI agents.

> **Current status:** Public preview. The tools are available for evaluation,
> but this repository does not yet carry an open-source license or a supported
> versioned release.

## Start here

- **Machine-readable catalog:** [`catalog.json`](catalog.json)
- **About Certiv:** [certiv.ai](https://certiv.ai/)
- **Product overview:** [Runtime assurance for AI agents](https://certiv.ai/product/)

For now, share or cite this GitHub repository. Dedicated pages at
`certiv.ai/tools/` will become the stable, human- and agent-readable source for
maturity, support, and release information when the website catalog ships.

## Tools

| Tool                                                | What it catches                                                                                                                | Maturity      | Documentation                                |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------- | -------------------------------------------- |
| [`selectstar`](cmd/selectstar/)                     | `SELECT *` passed to Go `sqlx` query methods, a rollout risk when schemas change before old application instances stop serving | Internal beta | [README](cmd/selectstar/README.md)           |
| [`integrationtestnames`](cmd/integrationtestnames/) | Integration-tagged Go tests that CI silently skips because their names do not match the configured `-run` prefix               | Internal beta | [README](cmd/integrationtestnames/README.md) |

The [Certiv Python SDK on PyPI](https://pypi.org/project/certiv/) will be
cataloged alongside Labs releases, but it is a Certiv platform integration
rather than a standalone Labs utility.

## Try the tools locally

Requirements:

- Go 1.22 or later
- a local checkout of this repository

Run all checks:

```bash
go test ./...
go vet ./...
```

Run either tool directly:

```bash
go run ./cmd/selectstar --fail /path/to/go/project
go run ./cmd/integrationtestnames /path/to/go/project
```

Both tools are local static analyzers. They do not send source code, telemetry,
or usage data anywhere.

## What belongs in Labs

A Labs release should:

1. solve a real problem without requiring a Certiv account;
2. take less than ten minutes to try;
3. state its maturity, limitations, data handling, and support level;
4. include tests, CI, contribution guidance, and a security contact;
5. connect a specific engineering problem to the broader work Certiv does.

Small and useful beats broad and speculative.

## Repository state

This repository is intentionally not licensed for public reuse yet. Before the
first supported release, Certiv must approve a license, complete intellectual
property review, assign a maintainer, and replace the preview notices. The
complete gate is in [PUBLICATION_CHECKLIST.md](PUBLICATION_CHECKLIST.md).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the internal development workflow.
Please report suspected vulnerabilities using the private process in
[SECURITY.md](SECURITY.md); do not open a public issue for security-sensitive
findings.
