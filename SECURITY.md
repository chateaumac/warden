# Security Policy

Warden holds an ADB key that can run shell commands on every TV that authorized it,
so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub Security Advisories](https://github.com/chateaumac/warden/security/advisories/new)
with steps to reproduce and the affected version.

## Hardening checklist

- Set `WARDEN_AUTH_MODE=oidc` (or `basic`) — never expose `none` beyond an isolated network.
- Firewall the TVs' ADB port (TCP 5555) so only the Warden host can reach it.
- Provide the ADB key via `WARDEN_ADB_KEY_FILE` from a secret store and set
  `WARDEN_ADB_KEYGEN=false`.
- After setup, use **Revoke USB debugging authorizations** on each TV and re-approve only Warden.
