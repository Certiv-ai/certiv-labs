# Claude Pool

An experimental [Certiv Labs](https://github.com/Certiv-ai/certiv-labs)
utility.

- **Project home:** [Certiv Labs / Claude Pool](https://github.com/Certiv-ai/certiv-labs/tree/main/tools/claude-pool)
- **Maturity:** Experimental
- **License:** [Apache-2.0](https://github.com/Certiv-ai/certiv-labs/blob/main/LICENSE.md)
- **Data handling:** local account metadata and macOS Keychain credentials; no
  Certiv network service or telemetry

Claude Pool is a local macOS launcher for one person who owns multiple Claude
subscription accounts. It spreads new local Claude Code sessions across the
accounts with the most remaining capacity and can resume an interrupted
terminal session on another account after a usage-window limit.

It works with:

- Claude Code in a terminal
- local sessions in the Code tab of Claude Desktop

It does not add capacity, share credentials with other people, or route Remote,
Dispatch, Cowork, or cloud sessions.

> Claude Pool is an independent, unofficial project from Certiv Labs. It is not
> affiliated with, endorsed by, or supported by Anthropic. Claude and Claude
> Code are Anthropic products. Multi-account pooling is not an officially
> supported Claude Desktop feature, so a future update may require changes to
> this tool.

Users are responsible for complying with Anthropic's terms and the terms of
each subscription. This tool is intended only for accounts owned and operated
by the same person. It is not designed for credential sharing, automated
account creation, ban evasion, or bypassing safety controls.

## Requirements

- macOS
- Python 3.10 or newer (`python3 --version`)
- Claude Code installed and available as `claude`
- Two or more Claude subscription accounts owned by the same person

## Quick start

Download and extract the release, open Terminal in its folder, then run:

```bash
./install.sh
~/.local/bin/claude-pool setup --desktop personal work
```

Replace `personal` and `work` with any short local labels you like. For three
accounts, add a third label:

```bash
~/.local/bin/claude-pool setup --desktop personal work side-project
```

The installer adds `~/.local/bin` to future zsh sessions when needed. Using the
full path above means setup also works immediately in the current terminal.

For each label, Claude opens an OAuth page. Before authorizing, make sure the
browser is signed into the intended account. The generated token is captured
and saved in macOS Keychain; do not paste it at a `password data` prompt.

When setup finishes, fully quit and reopen Claude Desktop once. Verify both
integrations:

```bash
~/.local/bin/claude-pool doctor
~/.local/bin/claude-pool desktop status
```

## Daily use

In a terminal, use `claude-pool` wherever you would normally use `claude`:

```bash
claude-pool
claude-pool --continue
claude-pool -- --model opus
claude-pool --account personal
```

Claude Desktop uses the pool automatically for every newly launched **local**
Code session after Desktop has restarted. A running session stays on its
assigned account; new sessions prefer the account with the lowest recent
utilization.

Plain `claude` still uses Claude's normally logged-in account. If you want the
pooled launcher to become your normal terminal command after testing it, add
this to `~/.zshrc`:

```bash
alias claude='claude-pool'
```

The launcher resolves the underlying Claude executable before starting it, so
the alias does not recurse.

## Account management

```bash
claude-pool status
claude-pool add another
claude-pool disable work
claude-pool enable work
claude-pool remove another
claude-pool cooldown personal 5h
claude-pool clear personal
```

`claude-pool add <name> --skip-setup` stores a token that was already generated
with `claude setup-token`.

## Updates and removal

To upgrade, extract the newer release and run its installer again:

```bash
./install.sh
claude-pool desktop install
```

The installer replaces only its managed application copy. Account tokens,
configuration, usage state, and session assignments are preserved.

To remove the application and Desktop integration:

```bash
./uninstall.sh
```

The uninstaller deliberately preserves account tokens and data. Remove
individual credentials first with `claude-pool remove <name>` if desired.

## How routing works

Each account gets an official long-lived OAuth token from
`claude setup-token`. New sessions go to the healthy enabled account with the
lowest reported five-hour and seven-day utilization. Before native telemetry
is available, sequential launches rotate evenly and simultaneous sessions
spread across profiles.

Terminal sessions receive a stable Claude session ID. If Claude renders a
blocking usage-limit message, the launcher records its reset time, starts the
next available account, and resumes the same session. Claude cannot change
credentials inside a running process, so this handoff briefly restarts the
Claude process.

Desktop supplies a stable local host-session ID. Claude Pool records only the
ID-to-alias assignment, so reopening a session keeps the same account. Native
usage and rate-limit events are observed after being forwarded unchanged;
prompt and response content is not stored.

The Desktop integration uses the app's `CLAUDE_CODE_LOCAL_BINARY` local-worker
override. It does not patch Claude.app or the signed Claude Code worker. A user
LaunchAgent restores the override after login, and the supervisor finds the
newest signed worker when a session starts.

If Desktop routing cannot select an account, it falls back to Desktop's normal
logged-in account and records details in
`~/.local/state/claude-pool/desktop.log`.

## Security and local files

- OAuth tokens: macOS Keychain service
  `com.codex.claude-pool.oauth-token`
- Account registry: `~/.config/claude-pool/config.json`
- Runtime and Desktop session state:
  `~/.local/state/claude-pool/state.json`
- Installed application: `~/.local/share/claude-pool/app`
- Command link: `~/.local/bin/claude-pool`

`XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and `XDG_DATA_HOME` are honored. JSON files
are mode `0600` and contain aliases, timestamps, utilization, and process
leases—not tokens. Tokens are passed only through Claude's
`CLAUDE_CODE_OAUTH_TOKEN` environment variable. Conflicting API/provider
credentials are removed from the launched process.

## Limitations

Automatic terminal resume is disabled when:

- input or output is not an interactive terminal
- `--print` or `--no-session-persistence` is used
- the bare `--resume` picker is used without a known session ID
- `--no-failover` is supplied

`--bare` is rejected because Claude ignores OAuth tokens in that mode. Remote
Control is also rejected because setup tokens are inference-only.

If a reset label cannot be parsed, Claude Pool uses a conservative fallback:
five hours and five minutes for a session limit, or seven days and five minutes
for a weekly/model limit.

## Development

Run the zero-dependency test suite with:

```bash
python3 -m unittest discover -s tests -v
```

Build deterministic ZIP and tar releases with:

```bash
python3 scripts/build_release.py
```

Claude Pool is licensed under the repository's
[Apache License 2.0](https://github.com/Certiv-ai/certiv-labs/blob/main/LICENSE.md).
The license covers Certiv's source code only; it does not grant rights to
Anthropic software, services, or trademarks.
