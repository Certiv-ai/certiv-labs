# Contributing

Certiv Labs is currently in internal hardening. Contributions are limited to
the Certiv team until the repository passes the publication checklist.

## Development workflow

1. Create a focused branch from `main`.
2. Keep changes scoped to one tool or one repository-wide concern.
3. Update the relevant README when behavior, flags, limitations, or output
   changes.
4. Run:

   ```bash
   gofmt -w ./cmd
   go test ./...
   go vet ./...
   ```

5. Include before-and-after examples for user-visible behavior.
6. Call out any new dependency, data flow, network access, or security
   implication in the pull request.

## Design expectations

- Prefer precise tools with low false-positive rates.
- Default to reporting findings; require an explicit flag for blocking behavior
  unless the command's purpose demands failure.
- Produce useful file and line locations.
- Support a documented suppression mechanism when a rule can have legitimate
  exceptions.
- Avoid Certiv-specific paths and configuration in reusable tools.

## Public contributions

Before public contributions are enabled, this file will be updated with the
approved license, contributor expectations, issue workflow, and maintainer
response targets.
