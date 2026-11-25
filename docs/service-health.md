# Service health and self-repair

Design for issue #71. Not built yet: the owner approves it first, then one ticket per part and per adopter.

## Goal

When an operation fails, the caller is told so, the right person or agent hears about it once, and a fault in our
own code ends in a tested fix with as little human work as is safe. The end state: a parser broken by a site change
is found by a probe, reproduced from a recorded answer, fixed in a pull request with a test, deployed and verified,
and the owner only reviews the pull request.

It applies to any **operation**: an MCP tool, an HTTP endpoint, a scheduled job, a CLI command. The MCP servers are
the first adopters, because a quiet failure there started this: a tool answered "nothing found" when it had read a
page and failed to understand it.

## Terms

| term | meaning |
|---|---|
| operation | one thing a service does for a caller: a tool, an endpoint, a job run, a command |
| dependency | something an operation reads from or writes to: a website, an API, a database, a browser, a model |
| result | what an operation returns, in the envelope below |
| problem | one failed or doubtful result, emitted as an event |
| incident | the problems that share a fingerprint, tracked from first sight to verified fix |
| probe | an operation called on a schedule with a known answer (a canary, a synthetic check) |
| artifact | what is needed to replay a problem: the request, the recorded answer, the code version |
| fault owner | who has to act: `our_code`, `upstream`, `environment`, `caller` |
| remediation | the agent loop that turns an incident with a fault in `our_code` into a pull request |

## Principles

- **Never quiet.** An exception is never turned into an empty answer. "Nothing matched" is said only when every
  dependency answered and the answer was understood.
- **Standards first.** OpenTelemetry for telemetry, a fixed set of error codes as in gRPC, HAR for recorded HTTP,
  JSON Schema for output contracts. We write only what has no standard: the incident states and the repair loop.
- **Emit, don't act.** A service reports what happened. One central triage service decides what to do. Services
  hold no GitHub credential.
- **Redact at the source.** Data is labelled where it is read. Nothing leaves a service before redaction, and
  triage scrubs again before anything goes to GitHub.
- **Reproduce before repair.** No patch without a test that fails before the fix and passes after it.
- **Gates are enforced, not requested.** What a patch may touch is checked by triage and CI, not left to the agent.

## The three layers

```
 service (any language)            triage (one service)               remediation (an agent)
 ┌──────────────────────┐  events  ┌──────────────────────────┐ brief ┌────────────────────────┐
 │ health library       │ ───────▶ │ intake, grouping         │ ────▶ │ reproduce from artifact │
 │  result envelope     │ OTLP     │ incidents (state machine)│       │ fix inside allowed paths│
 │  errors, invariants  │          │ policy, tickets          │ ◀──── │ draft pull request      │
 │  redaction, artifacts│ ◀─────── │ probes (black box)       │  PR   └────────────────────────┘
 └──────────────────────┘  probes  └──────────────────────────┘
```

## Layer 1: emit

A small library in every service, in TypeScript and Python, with the same names and a shared set of conformance
cases both packages run.

### The result envelope

Every operation returns this. On MCP it is the tool's `structuredContent`, and the tool declares an `outputSchema`
that wraps its own data schema. The text content keeps the answer, with the status on its first line.

```json
{
  "schema": "health.result/1",
  "status": "partial",
  "data": {},
  "errors": [
    { "code": "PERMISSION_DENIED", "reason": "BOT_CHALLENGE", "fault": "environment", "retryable": false,
      "dependency": "web:source-b", "message": "the bot check page did not clear" }
  ],
  "warnings": [
    { "reason": "KNOWN_ITEM_MISSING", "message": "an item the registry lists was not returned" }
  ],
  "dependencies": [
    { "id": "web:source-a", "status": "ok", "as_of": "2026-11-24T21:40:12Z", "cached": false },
    { "id": "web:source-b", "status": "error", "as_of": "2026-11-24T21:40:09Z", "cached": false }
  ],
  "as_of": "2026-11-24T21:40:09Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

| status | meaning |
|---|---|
| `ok` | every dependency answered and every answer was understood |
| `no_match` | the same, and nothing matched |
| `partial` | some dependencies failed; `data` holds what was read |
| `error` | no usable answer; `data` is empty |

- `as_of` is the oldest time any data in the result was fetched. A cached answer keeps its fetch time.
- `trace_id` is the OpenTelemetry trace. The same id is on the log lines, the incident, the ticket and the fix.
- On MCP, `error` sets `isError: true`. Over HTTP, `error` maps to 500 (`our_code`), 502, 503 or 504 (by code),
  or 4xx (`caller`). A CLI prints the envelope with `--json` and exits non-zero on `error`.
- A first adopter already returns `upstream_error` for this status. Triage reads it as `error`, and the adopter
  moves to `error` when it takes the library.

### Errors

One code from a fixed set, a reason the service picks, and the fault owner. This is the pattern of gRPC status
codes with an `ErrorInfo` reason (Google AIP-193), and it fits RFC 9457 Problem Details over HTTP.

| code | typical reasons | usual fault |
|---|---|---|
| `DEADLINE_EXCEEDED` | `TIMEOUT` | upstream |
| `UNAVAILABLE` | `NETWORK`, `SERVER_ERROR`, `RUNTIME_FAILED` (a browser did not start) | upstream or environment |
| `RESOURCE_EXHAUSTED` | `RATE_LIMITED`, `QUOTA` | environment |
| `PERMISSION_DENIED` | `FORBIDDEN`, `BOT_CHALLENGE` | environment |
| `UNAUTHENTICATED` | `LOGIN_REJECTED`, `TOKEN_EXPIRED` | environment |
| `FAILED_PRECONDITION` | `CONFIG_MISSING` | environment |
| `INVALID_ARGUMENT` | `BAD_INPUT` | caller |
| `NOT_FOUND` | `NO_SUCH_ITEM` (only where a lookup is asked "does this exist") | none: an answer |
| `INTERNAL` | `PARSE_FAILED`, `REQUEST_REJECTED` (the dependency refused a request we built), `UNHANDLED`, `INVARIANT` | our_code |

- The codes are fixed. Reasons are open: a service adds its own in upper snake case, and triage groups by them.
- `fault` decides what happens next (layer 2). `unknown` is allowed; triage then classifies it.
- **Content but no items is a failure.** A parser declares what a non-empty answer looks like on its source (a
  result count, a marker). If that is present and the parser extracted nothing, the result is `INTERNAL` /
  `PARSE_FAILED`, not `no_match`.
- `retryable` and, where known, `retry_after_s` tell the caller whether to try again.

### Contracts and invariants

Each operation declares its output schema and named invariants. An invariant that fails with proof is an error
(`INVARIANT`); one that is only a sign of trouble is a warning. The library gives generic invariants, each set per
operation:

| invariant | fails when |
|---|---|
| `extracted_if_marker` | the answer holds the non-empty marker and nothing was extracted (an error) |
| `known_item_present` | an item a registry lists for this dependency is missing (a warning) |
| `non_empty_for_broad` | an argument the operation calls broad got zero items (a warning) |
| `count_stable` | the item count is far below the recent count for the same probe (a warning) |

A service adds its own invariants as functions, registered by name.

### The manifest

Each service describes itself in `health.json` at its root, checked against a JSON Schema the library publishes.
The service and triage both read it.

```json
{
  "service": "codes",
  "data_class": "public",
  "dependencies": [
    { "id": "web:source-a", "kind": "browser", "data_class": "public", "min_probe_interval": "12h" }
  ],
  "operations": [
    {
      "name": "section_lookup",
      "dependencies": ["web:source-a"],
      "arguments": { "place": "public", "section": "public" },
      "invariants": ["extracted_if_marker", "known_item_present"],
      "patchable": ["src/sources/source-a/**"],
      "probes": [
        { "id": "known-section", "args": { "place": "p1", "section": "10.01" },
          "expect": { "status": "ok", "contains": "Open burning" }, "every": "12h" }
      ]
    }
  ],
  "remediation": { "mode": "suggest", "merge": "owner" }
}
```

### Telemetry

OpenTelemetry, with its semantic conventions:

- One span per operation. On MCP that is the `tools/call <tool>` span of the MCP conventions, with
  `gen_ai.tool.name`. Each dependency call is a child span (`http.request.method`, `http.response.status_code`,
  `server.address`).
- A failed or doubtful result emits a `health.problem` event (an OpenTelemetry log record with `event.name`), with
  `error.type`, `health.code`, `health.reason`, `health.fault`, `health.operation`, `health.dependency`,
  `health.fingerprint` (optional, to override the default grouping), `health.artifact` and the service's
  `service.name`, `service.version` and `vcs.ref.head.revision`.
- Metrics per operation and dependency: calls, results by status, errors by code and reason, latency.
- Export over OTLP/HTTP. Logs go to triage. Traces and metrics go to stdout until a backend is chosen (owner
  decision). A Collector can sit in between later, with no change to the services.

### Redaction

Every argument, field and dependency carries a data class: `public`, `personal`, `health` or `youth`. A service's
own class is the most private one it touches.

1. **Allowlist.** Telemetry holds only what the library builds: names, codes, reasons, statuses, counts, times,
   public links. An argument is copied only if its class is `public`; otherwise it is replaced by its type and length.
2. **Patterns** on whatever passes: bearer tokens, API keys, GitHub tokens, JWTs, cookies, email addresses, phone
   numbers, street addresses, dates of birth and digit runs of 6 or more become `[redacted:<kind>]`.
3. **Again in triage**, before anything is written to GitHub.

A location argument is never copied: telemetry names what the service resolved it to (a registry id). Data of class
`health` or `youth` never leaves the service: no artifact, no free text. The problem event carries only ids, and the
detail stays in the service's data directory.

### Artifacts

For a problem on a `public` dependency, the library records what is needed to replay it, as HAR 1.2 (the standard
for recorded HTTP; Playwright writes it for browser reads):

- the requests and answers of the operation, headers redacted, bodies up to 2 MB;
- the operation, its arguments (public ones only), the envelope it returned;
- the service version, commit and image digest, the trace id.

The library uploads it to triage, which keeps the last 5 per fingerprint for 14 days. The library's test helper
replays a HAR as the dependency (a fake `fetch`, a fake `httpx` transport, a Playwright route), so an artifact is a
test fixture as it stands.

### The service's own endpoints

- `GET /healthz`: liveness. The process answers. Never depends on a dependency, so a site being down never restarts
  the container.
- `GET /health`: readiness and the dependencies, from real traffic: per dependency the last success, the error rate
  over an hour, the last code and reason, and whether the service runs degraded.

## Layer 2: triage

One small service on the host: SQLite, one container. It owns what to do about problems, for every service.

### Intake

- `POST /v1/logs`: OpenTelemetry logs over OTLP/HTTP (JSON). It keeps `health.problem` and `health.report` events.
- `POST /v1/artifacts`: artifacts, `public` class only.
- The services point `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` at it.

### Grouping

The fingerprint is `sha256(service, operation, dependency, code, reason)`, unless the event sets
`health.fingerprint`. An agent's report adds its normalized query (lowercase, spaces collapsed, keys sorted), on
`public` services only. One fingerprint is one incident.

### The incident

```
open ──▶ ticketed ──▶ remediating ──▶ proposed ──▶ merged ──▶ deployed ──▶ verified ──▶ closed
  │          │             │                                                  │
  │          │             └──▶ not_reproduced / gave_up ──▶ (a person)       └──▶ regressed ──▶ (a person)
  │          └──▶ recovered (upstream or environment faults that cleared) ──▶ closed
  └──▶ muted (the owner)
```

- **deployed**: triage sees events or `/health` from a service version that contains the fix commit.
- **verified**: the probes of that operation pass twice in a row on that version, and the fingerprint does not
  recur for 24 hours.
- **regressed**: the fingerprint returns after `verified`. It reopens, links the earlier fix, and never starts a
  second automatic attempt.

### Policy

Per service, in triage's config, with these defaults:

| fault | ticket when | goes to |
|---|---|---|
| `our_code` | first occurrence | remediation, if eligible |
| `upstream` | error rate over 20% for an hour, and at least 3 errors | a ticket; closes itself on recovery |
| `environment` | first occurrence | a ticket labelled `needs-owner` |
| `caller` | never | a weekly summary (often a schema or docs fix) |
| `unknown` | first occurrence | classification first (below) |

- A service-level objective per operation (default: 99% of results not `error` over 7 days). Burning it faster
  than 14 times the allowed rate over an hour opens an incident of its own.
- At most 5 new tickets a day per service, and one comment an hour per incident. Past the limit, one ticket
  "Reporting paused" collects counts until the next day.
- Labels: `incident`, one of `our-code`, `upstream`, `environment`, `agent-report`, plus `unverified` and `sev:*`
  where they apply.

### Tickets

A GitHub issue in the service's repository. Title `<operation> <code>/<reason>: <dependency>`. Body: the service
version, the status, the code, reason and message, the dependency's public link, first and last seen, the count,
the trace id, the artifact id and the replay command, the probe status, and a fingerprint comment
(`<!-- fingerprint: ... -->`). Never the raw arguments of a non-public service.

Triage writes with a GitHub App of its own: Issues read and write, Pull requests read, Contents read, installed only
on the services' repositories. An App gives one-hour tokens and nothing to renew. A token per repository would
expire and spread credentials over many env files, and a coding agent's App can push code, which this service
never needs.

### Probes

Triage runs each operation's probes from its manifest, black box: through the service's real interface (an MCP
client over Streamable HTTP, or an HTTP call), with a probe credential the service accepts and the least access it
offers. That exercises transport, auth and registration, and notices a service that is down. A probe fails after 2
failures in a row and recovers after 2 passes. A probe on a dependency behind a bot check respects its
`min_probe_interval`. On a `health` or `youth` service a probe checks structure only (signed in, at least one item)
and keeps nothing it read.

Triage serves `GET /status` (every service, dependency, probe and open incident) for the host's health routine and
any dashboard.

### Classification

An incident with `fault: unknown` gets a short agent pass: read the problem, the artifact and the code, answer one
fault owner with a confidence. Below the threshold it goes to a person. The answer is written on the ticket.

## Layer 3: remediation

### Eligible

All of these:

- fault `our_code` (given, or classified with confidence);
- an artifact, so the dependency is `public`;
- the operation declares `patchable` paths;
- remediation mode is not `off` for the repository;
- no open attempt on the incident, it never regressed, and the day's budget has room.

### The loop

Triage hands a coding agent, signed in as its own GitHub App, a brief: the incident, the artifact, the replay
command, the patchable paths and the rules. The agent:

1. Branches from the default branch in its own worktree: `fix/incident-<id>`.
2. **Reproduces:** turns the artifact into a test that fails on the default branch for the incident's reason. If it
   cannot, it stops: `not_reproduced`, and the ticket says what it tried.
3. **Fixes** inside the patchable paths until that test and every existing test pass.
4. Opens a draft pull request: the incident, the trace id, the failing test before and after, the diff summary.
5. Marks it ready when CI passes. Merging follows the repository's `merge` policy.

### Gates triage enforces

Triage checks the pull request itself through the API and closes it, with the reason on the ticket, if:

- a changed file is outside the patchable paths and the tests;
- a test was deleted, skipped or marked expected to fail (the standard's silencer count must not rise);
- it touches auth, redaction, secrets, the health library, CI, or a dependency version;
- it changes more than 200 lines;
- the reproduction test does not fail on the default branch, or does not pass on the branch.

### Modes

Per repository, set by the owner:

| mode | what happens |
|---|---|
| `off` | tickets only |
| `suggest` | draft pull request; a person reviews and marks it ready |
| `propose` | ready pull request once CI passes; a person merges |
| `auto` | merged once CI passes and the gates hold; the deploy stays the repository's own |

Every repository starts at `suggest`. Moving up is the owner's call, on the measures below.

### What every fix leaves

- The reproduction test and its HAR, kept in the repository as a regression fixture.
- If no probe caught the problem, the pull request adds one to the manifest.

### Measures

Per repository and mode: share of incidents reproduced, share of pull requests merged without a human change,
regressions within 30 days, time from first problem to verified fix, and silent failures found (problems a probe
caught that no live call had reported as `error`).

## Agents can report: `report_issue`

Every MCP service built on the library has this tool, on by default, because redaction runs first. It emits a
`health.report` event, which triage groups like any problem.

```
report_issue(tool_name, inputs, expected, actual, evidence_url?, severity?)
```

- `severity`: `low`, `medium` or `high`; `medium` when not given.
- `inputs`: at most 4 KB once serialised. Longer is cut, and the report says so and from what size.
- No `evidence_url`: accepted, labelled `unverified`.
- Reporter: the client's name from the MCP handshake (`clientInfo.name`) and the credential it connected with.
- Context: the trace ids of that tool's calls in the last hour, and the probe status of its dependencies.
- Returns `{ "incident": "...", "issue_url": "..." | null, "action": "opened" | "merged" | "held" }`. `held` means
  under the ticket threshold or the rate limit.

On a `health` or `youth` service, `inputs`, `expected` and `actual` stay in the service's data directory. The event
carries the tool, the severity, the reporter and the local report id.

## What gets built

| part | what it is |
|---|---|
| health library | TypeScript and Python: envelope, errors, invariants, manifest loader, redaction, OpenTelemetry setup, dependency wrappers (`fetch`, `httpx`, Playwright), artifacts, `/healthz` and `/health`, the MCP adapter (`outputSchema`, `report_issue`), the HAR replay helper, the conformance cases |
| triage | one container: OTLP/HTTP log intake, artifacts, incidents, policy, probes, tickets, gates, `/status` |
| remediation runner | how triage starts a coding agent with the brief; the agent itself is an existing one |
| in this repository | the manifest's JSON Schema, and later a `standards-check` rule: a service with a manifest has a probe for every dependency |

## Rollout

Each step is a ticket, and each ends with something running.

1. **Library 0.1:** envelope, errors, redaction, manifest, telemetry to stdout. Pilot: one public-data MCP service,
   whose own status fix already uses these statuses.
2. **Triage 0.1:** intake, grouping, incidents, tickets. Pilot and a second service.
3. **Probes:** from manifests, black box; `/health`; `/status` read by the host's health routine.
4. **Artifacts:** HAR and the replay helper; every `our_code` ticket carries a replay command a person can run.
5. **Remediation in `suggest` mode** on the pilot only. Read the measures after a month.
6. **More adopters:** the other MCP services (private-data ones without artifacts or remediation), then HTTP
   services and scheduled jobs.

## Owner decisions

1. **Where it lives.** Recommended: one new public repository with the library packages and triage, generic and
   holding no private detail, so an image build installs a tagged version with no token.
2. **The triage GitHub App**, which only the owner can create and install.
3. **A trace backend**, or none for now (logs only, traces to stdout).
4. **Who runs and pays for the remediation agent**, and its daily budget. This is the same question as #34.
5. **Each repository's mode and merge policy** (default `suggest` and `owner`).
