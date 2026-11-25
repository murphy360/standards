# MCP servers: loud failures, canaries and auto-ticketing

Design for issue #71. Not built yet: the owner approves it first, then one follow-up ticket per server.

## Why

A server read a public source, the read went wrong, and the tool answered "nothing found". The agent took that as
the answer. In the case that started this, the pages loaded fine; the parser found no results in them. So a quiet
failure is not only an upstream error: it is any read the server could not understand.

The servers today:

- Results are plain text. An upstream failure comes back as ordinary text, or as an empty list.
- Many parsers turn an unexpected shape into `[]`, and some fetch errors are caught and dropped.
- Every `/healthz` returns a fixed `ok: true`. Nothing calls a tool on a schedule.
- No server files a GitHub issue. Two servers have a live check script, run by hand.
- The host's daily health routine checks only that each MCP route asks for credentials.

## The parts

1. A status on every tool result, with the cause of every failure.
2. Sanity checks that flag a result as suspect.
3. Canaries: known answers, read at startup and on a schedule.
4. Structured logs and counters per source.
5. Auto-ticketing, deduplicated, rate-limited and redacted.
6. A `report_issue` tool, so an agent can report a result it doubts.

All of it ships as one small library, in a TypeScript and a Python package with the same names and behaviour
("The library", below).

## 1. The result

Every tool result carries this envelope as `structuredContent`. The text content keeps the answer, and its first
line states the status, for clients that read only text.

```json
{
  "status": "partial",
  "source_checked_at": "2026-11-24T21:40:12Z",
  "sources": [
    { "id": "codes:place-1", "status": "ok", "checked_at": "2026-11-24T21:40:12Z", "cached": false },
    { "id": "codes:county-1", "status": "upstream_error", "cause": "challenged",
      "reason": "bot check page did not clear", "http_status": 403,
      "url": "https://example.org/codes/county/latest", "checked_at": "2026-11-24T21:40:09Z" }
  ],
  "suspect": null,
  "degraded": ["codes:county-1"],
  "data": {}
}
```

The four values:

| status | meaning |
|---|---|
| `ok` | every source answered and the server understood every answer |
| `no_match` | every source answered, the server understood the answers, and nothing matched |
| `partial` | some sources answered and some failed; `data` holds what was read |
| `upstream_error` | no source gave a usable answer; `data` is empty |

Rules:

- `no_match` only when the source answered **and** the answer parsed. A page with content that yields zero items is
  not `no_match` (cause `parse_failed`, below).
- `upstream_error` sets `isError: true` on the MCP result, so a client shows it as a failure. `partial` does not.
- `source_checked_at` is the oldest `checked_at` of the sources used. A cached answer keeps the time it was fetched,
  never the time it was served.
- `degraded` names the sources whose canary is failing now, even when this call worked.
- `suspect` is a flag beside the status, not a fifth status: `{ "reason": "..." }` or `null` (section 2).
- A 404 stays an answer where the tool asked "does this exist", such as a lookup by id. It is suspect when the
  server already knows the item exists.

## Error causes

Every failure names one cause and a short reason in plain words. An exception is never turned into an empty list.

| cause | when | retried | ticket |
|---|---|---|---|
| `timeout` | no answer in the time limit | once | after 3 in an hour |
| `network` | DNS, refused, reset, TLS | once | after 3 in an hour |
| `rate_limited` | 429 or the source's own limit | after its `Retry-After` | after 3 in an hour |
| `server_error` | 5xx | once | after 3 in an hour |
| `forbidden` | 403 with no bot check page | no | yes |
| `challenged` | a bot check (Cloudflare or similar) came back instead of the page | no | yes |
| `auth_failed` | login rejected, or a 401 after one fresh login | no | yes |
| `browser_failed` | the headless or headful browser did not start or crashed | no | yes |
| `parse_failed` | the answer did not have the expected shape, was not valid JSON, or had content and gave zero items | no | yes |
| `bad_url` | the server built a link the source rejects: our bug | no | yes |
| `quota` | an API key's quota is spent | no | once a day |
| `config` | a key or setting is missing | no | once |

The names match what the codes server already returns for its own fix. A server may add a cause; it adds it to the
library, not locally, so every server means the same thing by it.

`parse_failed` from content: a parser knows what an empty answer looks like on its source. If the page reports a
result count above zero, or holds a marker that results are present, and the parser extracted nothing, that is
`parse_failed`. Each source parser declares that marker.

## 2. Suspect results

A heuristic that something is wrong, short of proof. The status stays what it is; `suspect` says why; the server
files a ticket.

- A section or item the server's registry knows about comes back missing.
- A broad query gets zero hits. Each tool declares what broad means for it (a one-word query, a whole chapter).
- A source returns far fewer items than its last ten answers to the same canary.

Proof goes to `parse_failed`, a guess goes to `suspect`.

## 3. Canaries

A canary is a tool call with a known answer. It runs at startup (a minute after the server is up, so it never blocks
it) and on a schedule, inside the server's own process. It calls the tool's handler directly: no HTTP, no token, no
host timer, no new container. The servers already run in-process schedules, so this needs nothing new on the host.

Canaries live in each server's repository, in `canaries.json` beside its sources, because they test that server's
parsers. Shared config holds only the defaults: run every 6 hours, 2 minutes per canary, plus or minus 10 minutes
of jitter.

```json
[
  {
    "id": "place-1-section",
    "source": "codes:place-1",
    "tool": "local_code_section",
    "args": { "place": "place-1", "section": "1503.01" },
    "expect": { "status": "ok", "contains": "Open burning" },
    "every": "6h"
  },
  {
    "id": "place-1-search",
    "source": "codes:place-1",
    "tool": "local_code_search",
    "args": { "place": "place-1", "query": "burning" },
    "expect": { "status": "ok", "min_items": 1 }
  }
]
```

- `expect` holds `status`, and any of `contains` (text in the answer), `min_items` and `matches` (a regex).
- A source behind a bot check runs its canaries every 12 hours, to stay polite.
- A server that reads private accounts (health records, youth records) checks structure only: logged in, at least
  one item. It never compares or keeps the data it read.
- A canary fails after 2 failures in a row and recovers after 2 passes in a row. One bad read does not flip it.
- The existing live check scripts become the first canary files.

`/healthz` reports the canaries. It still answers HTTP 200 when a source is degraded, so Docker never restarts a
server because a website is down:

```json
{
  "ok": true,
  "degraded": ["codes:county-1"],
  "sources": {
    "codes:place-1": { "canary": "pass", "last_ok_at": "...", "errors_1h": 0 },
    "codes:county-1": { "canary": "fail", "cause": "challenged", "since": "..." }
  },
  "ticketing": "on",
  "token_days_left": 41
}
```

The host's daily health routine reads it the way it already reads one server's status, with `docker exec` and no
token, and lists degraded sources and an expiring token on its report.

## 4. Logs and counters

One JSON line per upstream call, on stderr:

```json
{"ts":"...","level":"warn","server":"codes","tool":"local_code_search","source":"codes:place-1",
 "status":"upstream_error","cause":"parse_failed","http_status":200,"ms":2140,
 "url":"https://example.org/codes/place/search","snippet":"<first 300 characters>","fingerprint":"9f3c..."}
```

- `url` drops the query string, except on sources declared public.
- `snippet` only on public sources, at most 300 characters, after redaction.
- On `parse_failed` from a public source, the server keeps the whole page in its data directory
  (`health/pages/<fingerprint>-<time>.html`, the last 5 per fingerprint, for 7 days), so the parser can be fixed
  from the real page. The ticket names the file; the page never goes in the ticket.
- Counters per source, in memory: calls, `ok`, `no_match`, errors by cause, `last_ok_at`, `last_error_at`. They feed
  `/healthz`. A ring of the last 50 log lines per tool, already redacted, feeds `report_issue`.

## 5. Auto-ticketing

What opens a ticket: an `upstream_error` cause whose rule above says so, a suspect result, a canary that fails, and
a `report_issue` call. The ticket goes to that server's own repository.

### Fingerprint

`sha256(server + source + kind + key)`, where `kind` is `error`, `suspect`, `canary` or `report`, and `key` is the
cause for an error, the check's name for a suspect result, the canary's id, or, for a report, the tool and the cause
or the normalized query (lowercase, spaces collapsed, argument keys sorted). The query never goes in the fingerprint
on a private-data server (below).

- The server keeps `health/issues.json` in its data directory: fingerprint to issue number. After a restart, or if
  the file is lost, it searches open issues with the `auto-report` label for the fingerprint, which each issue body
  carries in a comment (`<!-- fingerprint: 9f3c... -->`).
- A repeat adds a comment to the open issue, at most one an hour per fingerprint. Repeats in between are counted, and
  the next comment gives the count since the last one.
- An issue closed by a person and the fingerprint seen again: a new issue that links the old one.

### Limits

- At most 5 new issues a day per server. After that, one issue titled "Auto-reporting paused" collects the rest as
  counts, until the next day.
- Every issue gets the label `auto-report` and one of `canary`, `upstream-error`, `suspect`, `agent-report`. The
  server creates the labels on first use.

### Recovery

- A canary issue gets a comment and is closed when the canary recovers.
- An error or suspect issue gets a comment ("healthy again since ..."), and stays open: a parser bug does not fix
  itself because the source came back.

### The issue

Title: `<source> <cause or check>: <tool>`, such as `codes:place-1 parse_failed: local_code_search`. Body: the
server version, the tool, the status and cause and reason, the HTTP status, the public link, the first and last
time seen, the count, the saved page's file name, the canary status, and the fingerprint comment. Never the raw
inputs of a private-data server.

### Redaction

Redaction runs before anything is logged, stored or sent. Two layers:

1. **Allowlist.** The ticket and the log hold only fields the library builds: tool name, source id, cause, reason,
   HTTP status, link, counts, times. Each tool declares how its arguments are treated: `public` (copied, after
   layer 2), `redact` (each value replaced by its type and length) or `never`. Each server declares whether it reads
   private data. On a private-data server every argument is `never`, and so is every free-text field of
   `report_issue`.
2. **Patterns**, on whatever layer 1 lets through: bearer tokens, API keys and GitHub tokens, JWTs, cookies, email
   addresses, phone numbers, street addresses (a number and a street word), dates of birth, and digit runs of 6 or
   more. A match is replaced by `[redacted:<kind>]`.

An address given to a location tool is never copied. The ticket names the place the server resolved it to (a
registry id), not the address.

Medical and youth data never go in an issue. On a private-data server the ticket says what failed and where, and
the details stay in the server's data directory (`health/reports/<id>.json`), which the ticket names by id.

### The token

One fine-grained GitHub token per server repository, made by the owner (agents cannot):

- Repository access: only that server's repository.
- Permissions: Issues read and write. Metadata read comes with it. Nothing else.
- An expiry, the longest GitHub offers. GitHub sends the expiry date on every API answer
  (`github-authentication-token-expiration`); the server shows the days left in `/healthz`, and the host's routine
  warns 14 days before.
- Kept in the server's env file on the host as `HEALTH_ISSUES_TOKEN`, with `HEALTH_ISSUES_REPO=<owner>/<repo>`.

With no token, everything else still works: tickets go to the log as `would_ticket` lines and `/healthz` says
`"ticketing": "off"`.

Not chosen: an agent's GitHub App token. It reaches every repository and can push code, too much for a server that
is up all day and reads the internet.

## 6. `report_issue`

Every server has the tool, on by default, because redaction runs first.

```
report_issue(tool_name, inputs, expected, actual, evidence_url?, severity?)
```

- `severity`: `low`, `medium` or `high`; `medium` when not given.
- `inputs`: at most 4 KB once serialised. Longer is cut, and the report says it was cut and from what size.
- No `evidence_url`: the report is accepted and labelled `unverified`.
- Reporter: the client's name from the MCP handshake (`clientInfo.name`) and the credential it connected with, such
  as `claude-code via owner-token`.
- Server context: the redacted log lines for that tool and source from the last hour, at most 20, and the current
  canary status of the sources the tool reads.
- The fingerprint is the `report` kind above. A match comments on the open issue.
- The same limits apply as for the server's own tickets. A report over the limit is kept locally and counted.
- Returns `{ "issue_url": "...", "action": "opened" | "merged" | "held", "fingerprint": "..." }`. `held` means
  rate-limited, or no token, and gives the local report id instead of a link.

On a private-data server, `inputs`, `expected` and `actual` are kept in the local report only. The issue holds the
tool, severity, reporter, the cause if the server saw one, and the local report id.

## The library

The status has to be set where the fetch and the parse happen, inside the server, so a sidecar cannot do it. One
library, two packages with the same names and the same tests (a shared set of JSON cases both run):

```ts
const health = createHealth({ server: "codes", privateData: false, canaries, sources });
const page = await health.fetch("codes:place-1", url, { timeoutMs: 15000 }); // classifies and logs
return health.result({ sources: [page.outcome], data, text });               // envelope plus text
health.registerReportIssue(mcpServer);
app.get("/healthz", (_, res) => res.json(health.status()));
```

- The TypeScript package wraps `McpServer.registerTool` and the servers' shared `answer()` wrapper, so a thrown error
  becomes `upstream_error` with a cause, not a bare message.
- The Python package does the same for the Python SDK's tool decorator and returns the envelope as the tool's
  structured output.
- A browser read reports through the same outcome (`health.outcome(...)`), so a browser source is classified the
  same way as a fetch.
- `health.parse(sourceId, page, parser, { resultsMarker })` runs a parser and applies the zero-items rule.

## Rollout

Each step is one ticket in that server's repository, after this design is approved.

1. The library, both packages, with the shared test cases.
2. Pilot: the codes server. Its own status fix already uses these values; the library replaces that code. Canaries
   from its live check script.
3. The public medical reference server: the same TypeScript template, and a live check script to turn into canaries.
4. The youth-records server: private data, so arguments `never`, structural canaries, local reports.
5. The food safety server: the Python package's first user; health data, so private.
6. The rest, one ticket each.
7. The host: the daily routine reads each `/healthz`, and the owner makes the tokens.

## Owner decisions

- Where the library lives. Recommended: a new public repository of its own, generic and holding no private detail,
  so a server's image build installs it from a tag with no token. The TypeScript package installs from a git tag;
  the Python package with pip from the same tag. The alternative, a directory in this repository, ties its versions
  to the standard's releases.
- The tokens: one per server, made by the owner.
