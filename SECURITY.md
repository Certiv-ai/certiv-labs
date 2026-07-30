# Security policy

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities, credential exposure,
or release-safety concerns.

During the public preview, use the
[Certiv company contact form](https://certiv.ai/company/#contact), clearly mark
the message **Security report**, and include:

- the affected tool and version or commit;
- reproduction steps;
- potential impact;
- any suggested mitigation.

Before the first supported release, this document must be updated with the
approved private GitHub security-advisory or security-email process.

## Data handling

`selectstar` and `integrationtestnames` perform local static analysis. They do
not make network requests, upload source code, or emit telemetry.

Claude Pool stores subscription OAuth tokens in macOS Keychain and local
account metadata in user-private files. It launches the official Claude Code
worker, which communicates with Anthropic under the user's account. Claude Pool
does not send data to Certiv, and it does not store prompts or responses.

Any change to these properties requires an explicit security review and
prominent README disclosure.
