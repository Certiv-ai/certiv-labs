# AGENTS.md

## Repository purpose

Certiv Labs is the incubation repository for small, standalone tools the Certiv
team may publish for the broader engineering and AI-agent community.

## Working rules

- Keep every tool useful without a Certiv account.
- Preserve the privacy property: tools must not transmit source code or
  telemetry unless their README explicitly documents and justifies it.
- Keep commands dependency-light. Prefer the Go standard library when it fits.
- Give every new command a focused README and tests.
- Do not publish, push, tag, or change licensing without explicit approval.
- Remove private issue numbers, customer names, internal hostnames, and
  proprietary implementation details from public-facing copy.

## Validation

Run these before committing:

```bash
gofmt -w ./cmd
go test ./...
go vet ./...
(cd tools/claude-pool && python3 -m unittest discover -s tests -v)
(cd tools/claude-pool && python3 scripts/build_release.py)
```

README links should be relative for files inside this repository. Public-facing
links should use canonical `https://certiv.ai/` or
`https://github.com/Certiv-ai/` URLs.
