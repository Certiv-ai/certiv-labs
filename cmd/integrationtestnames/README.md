# integrationtestnames: Go integration-test CI checker

Catch integration-tagged Go tests that a prefix-based CI filter would silently
skip, before a false-green build hides them.

- **Tool page:** [certiv.ai/tools/integrationtestnames/](https://certiv.ai/tools/integrationtestnames/)
- **Source:** this directory
- **Maturity:** Internal beta
- **Data handling:** local static analysis; no network requests or telemetry

## Why this exists

Some Go projects separate integration tests with both a build tag and a
name-based CI filter:

```bash
go test -tags=integration -run '^TestIntegration_' ./...
```

The build tag controls which files compile. The `-run` expression controls
which test functions execute. An integration-tagged function named
`TestDatabase` compiles successfully but never runs under that command. CI stays
green while the test quietly rots.

This tool reports the mismatch:

```text
storage/database_integration_test.go:14: TestDatabase must start with TestIntegration_ (integration-tagged test)
```

## Try it locally

From the repository root:

```bash
go run ./cmd/integrationtestnames /path/to/go/project
```

The default required prefix is `TestIntegration_`. Match a different CI
convention with:

```bash
go run ./cmd/integrationtestnames \
  --prefix TestE2E_ \
  /path/to/go/project
```

When the first supported release is cut, this README will add a versioned
`go install` command. Until then, run it from a checkout.

## Recommended CI pairing

Keep the prefix identical in the check and in `go test`:

```bash
go run ./cmd/integrationtestnames --prefix TestIntegration_ ./...
go test -tags=integration -run '^TestIntegration_' ./...
```

The command accepts a directory, a file-tree-style `./...` argument, or
multiple roots.

## What it checks

A finding requires all of the following:

1. the filename ends in `_test.go`;
2. the leading `//go:build` expression enables the file for the `integration`
   tag but not with no tags;
3. a package-level function has the Go test signature `func TestXxx(*testing.T)`;
4. the function name does not start with the configured prefix.

Benchmarks, examples, `TestMain`, methods, ordinary unit-test files, and
matching integration tests are ignored. Hidden and underscore-prefixed
directories, plus `vendor`, `node_modules`, `mocks`, and `testdata`, are not
walked.

## Exit codes

| Code | Meaning                                                             |
| ---- | ------------------------------------------------------------------- |
| `0`  | Every integration test matches the configured prefix                |
| `1`  | One or more integration tests would be skipped by the prefix filter |
| `2`  | Input, configuration, or parsing failed                             |

## Limitations

- The analyzer checks the `integration` build tag specifically.
- It models the no-tag and integration-only cases; it does not enumerate every
  possible custom build-tag combination.
- It validates naming, not whether the integration test can connect to its
  dependencies or pass.

## Testing

```bash
go test ./cmd/integrationtestnames
go vet ./cmd/integrationtestnames
```

See the repository [support](../../SUPPORT.md),
[security](../../SECURITY.md), and [publication](../../PUBLICATION_CHECKLIST.md)
policies before wider distribution.
