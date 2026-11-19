# ADR 0007: Injectable clock and a deterministic seed

Status: accepted

## Context

Several rules depend on time: the 30-day refund window, the 7-day reopen window, SLA breaches,
API key expiry. The benchmark needs identical data and identical outcomes on every run, and the
tests need to move time.

## Decision

- Services read time from an injected `Clock`. The default is the system clock; tests and demos
  use `FrozenClock`. `HELPDESK_FROZEN_TIME` pins the clock of a running service.
- The seed takes `(seed, as_of)` and uses one seeded `random.Random` and a seeded Faker.
  Every generated timestamp is relative to `as_of`; nothing reads the wall clock. The same inputs
  produce the same rows, checked by a content fingerprint in tests.
- The seed also builds named scenarios (an order that needs lead approval, an order outside the
  refund window, a ticket solved 12 days ago ...) and writes them to
  `bench/tasks/fixtures.json`, so benchmark tasks refer to facts that are guaranteed to exist.
  A test checks each scenario against the live API.
- Seeded data contains no API keys or audit events, so it holds no secrets and no random salts.

## Consequences

- Running the API on the real clock against seeded data makes most orders look old. The Makefile
  and docs set `HELPDESK_FROZEN_TIME` for demos. Production leaves it unset.
- Changing the seed code changes the dataset; fixtures and any benchmark results must be
  regenerated together.
- Faker output can change between major Faker versions; `uv.lock` pins the version used.
