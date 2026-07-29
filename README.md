# Certiv Labs

Practical developer tools for safer software delivery. Run focused checks
locally or in CI—no Certiv account, network access, or telemetry required.

Built by the team behind [Certiv](https://certiv.ai/): **AI Agent Assurance for
Endpoints.**

> **Current status:** Public preview. The tools are available for evaluation,
> but this repository does not yet carry an open-source license or a supported
> versioned release.

## Start here

- **Browse the tools:** [certiv.ai/tools/](https://certiv.ai/tools/)
- **Machine-readable catalog:** [`catalog.json`](catalog.json)
- **About Certiv:** [certiv.ai](https://certiv.ai/)
- **Product overview:** [AI Agent Assurance for Endpoints](https://certiv.ai/product/)

The website catalog is the stable, human- and agent-readable source for
discovery. This repository remains the source of truth for code, setup,
limitations, and contribution guidance.

## Tools

| Tool                                                                                 | Problem it catches                                                                                                    | Maturity      | Documentation                                |
| ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- | ------------- | -------------------------------------------- |
| [`selectstar` — Go `sqlx` `SELECT *` linter](cmd/selectstar/)                        | `SELECT *` queries that can break strict scans when a schema changes before old application instances stop serving    | Internal beta | [README](cmd/selectstar/README.md)           |
| [`integrationtestnames` — Go integration-test CI checker](cmd/integrationtestnames/) | Integration-tagged tests that CI silently skips because their names do not match the configured `go test -run` prefix | Internal beta | [README](cmd/integrationtestnames/README.md) |

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

## Why Certiv publishes these

AI-assisted engineering makes small, automated safeguards more valuable. Labs
is where Certiv shares focused tools and implementation lessons that are useful
outside our product. Each release is problem-led, transparent about its limits,
and usable without a Certiv account.

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
