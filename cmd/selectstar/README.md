# selectstar

Find `SELECT *` projections passed to common Go
[`sqlx`](https://github.com/jmoiron/sqlx) query methods.

- **Planned tool page:** `certiv.ai/tools/selectstar/`
- **Source:** this directory
- **Maturity:** Internal beta
- **Data handling:** local static analysis; no network requests or telemetry

## Why this exists

In a rolling deployment, a database migration can finish before every old
application instance stops serving. If that migration adds a column, a
`SELECT *` result changes immediately. An older Go destination struct may then
fail under strict `sqlx` scanning because it does not contain the new column.

Explicit columns keep the query result stable while old and new versions
overlap:

```go
// Risky during additive schema changes.
err := db.GetContext(ctx, &user, "SELECT * FROM users WHERE id = $1", id)

// Stable across an unrelated added column.
err := db.GetContext(
    ctx,
    &user,
    "SELECT id, email, created_at FROM users WHERE id = $1",
    id,
)
```

## Try it locally

From the repository root:

```bash
go run ./cmd/selectstar /path/to/go/project
```

The default is advisory: findings are printed, but the command exits zero.
Use `--fail` in CI:

```bash
go run ./cmd/selectstar --fail /path/to/go/project
```

When the first supported release is cut, this README will add a versioned
`go install` command. Until then, run it from a checkout so the instructions
remain truthful and reproducible.

## What it detects

The analyzer looks for a `SELECT *` string passed to common `sqlx` methods,
including `GetContext`, `SelectContext`, `QueryxContext`, `NamedQuery`, and
their non-context variants.

It follows:

- inline string literals;
- local and package-level string variables;
- `+` concatenation;
- `fmt.Sprintf` format strings;
- wildcard projections inside subqueries and CTEs.

It ignores:

- explicit projections such as `SELECT id, name`;
- `count(*)`;
- SQL stored as data but not passed to a recognized query method;
- hidden and underscore-prefixed directories, plus `vendor`, `node_modules`,
  `mocks`, and `testdata`;
- reviewed exceptions carrying an allow directive.

## Suppress a reviewed exception

Place a reason on the query, the query's preceding line, or the call:

```go
//selectstar:allow CTE explicitly defines its output columns
query := `
    WITH active AS (SELECT id, email FROM users WHERE active)
    SELECT * FROM active
`
```

Do not use a suppression merely to clear CI; record why the result shape cannot
drift.

## Exit codes

| Code | Meaning                                    |
| ---- | ------------------------------------------ |
| `0`  | No findings, or findings in advisory mode  |
| `1`  | Findings were present and `--fail` was set |
| `2`  | The tool could not read or parse an input  |

## Limitations

- The analyzer is AST-based and does not type-check receivers. A non-`sqlx`
  method with a recognized name that receives a literal `SELECT *` can be a
  false positive.
- It does not resolve strings returned by functions or built through arbitrary
  runtime logic.
- It analyzes Go source, not standalone `.sql` files.

These boundaries keep the command fast, dependency-free, and predictable.

## Testing

```bash
go test ./cmd/selectstar
go vet ./cmd/selectstar
```

See the repository [support](../../SUPPORT.md),
[security](../../SECURITY.md), and [publication](../../PUBLICATION_CHECKLIST.md)
policies before wider distribution.
