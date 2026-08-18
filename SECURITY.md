# Security policy

## Scope

This package is a standalone approval API foundation. It does not modify or
wire the live Hermes approval manager, gateway adapters, profiles, or services.

## Reporting

Do not open a public issue for a suspected vulnerability. Contact the
maintainer privately with a description, reproduction, affected version, and
safe disclosure timeline. Do not include real credentials or production
approval data; use a temporary database and synthetic values.

## Security boundaries

- Hosts must provide the canonical dashboard authorization callback.
- Every store is constructed with an explicit profile and database path.
- `session_id` is an internal compatibility key and is never an API DTO field.
- Explanations and source labels are redacted and length-bounded before
  persistence. The policy covers token/password/API-key credentials,
  client-id/client-secret and private-key variants, Bearer values, and
  Basic values in shell and key/value forms. Quoted JSON-style credential keys
  are recognized with quoted or unquoted textual values, such as
  `"token":"value"` and `"token":value`. Quoted credential values may contain
  spaces. PEM private-key blocks are redacted through their matching END marker
  across newlines; malformed or unterminated blocks consume the remainder of
  the input fail-closed. This is an accepted textual policy, not an arbitrary
  structured JSON/YAML sanitizer; ordinary prose is not redacted unless it
  uses a credential-looking form.
- Host installation must use the adapter factory; the manifest intentionally
  does not claim automatic backend mounting.
