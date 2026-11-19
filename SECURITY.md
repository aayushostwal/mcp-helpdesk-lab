# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private vulnerability
reporting: the repository's **Security** tab, then **Report a vulnerability**. Include what you
found, how to reproduce it and the version or commit.

You can expect an acknowledgement within a few days. This is a reference project maintained by
one person, so there is no formal SLA, but valid reports are fixed and credited.

## Supported versions

Only the latest commit on `main` is supported.

## Scope and known limits

This repository is a reference implementation with synthetic data. In scope: authentication and
authorisation, injection, secret handling, request smuggling through the middleware, the
idempotency layer. Known and documented limits, not vulnerabilities:

- There is no rate limiting. Put the service behind a gateway that provides it.
- Webhook delivery has a basic SSRF guard (private and loopback addresses are refused by
  name or literal IP). It does not resolve DNS, so a public name that points at a private
  address is not caught. Run outbound traffic through an egress proxy in production.
- API keys are shown once and stored as `sha256(salt + secret)`. SHA-256 is a fast hash, which is
  appropriate for high-entropy random keys (256 bits) but would not be for human passwords.
- Webhook signing secrets are stored in clear text because HMAC signing needs the raw value.

## Handling secrets

Never commit tokens or `.env` files. `.env` is git-ignored; `.env.example` contains no secrets.
