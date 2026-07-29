# Security policy

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities, credential exposure,
or release-safety concerns.

While this repository is internal, report findings through Certiv's internal
security channel and include:

- the affected tool and version or commit;
- reproduction steps;
- potential impact;
- any suggested mitigation.

Before public release, this document must be updated with the approved private
GitHub security-advisory or security-email process.

## Data handling

The current tools perform local static analysis. They do not make network
requests, upload source code, or emit telemetry. A change to that property
requires an explicit security review and prominent README disclosure.
