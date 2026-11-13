## D-search-trail — every writer search is on the record: query, outcome, URLs, round, writer, attempt

**The finding.** The audit trail could not reconstruct a writer's source discovery.
`search.make_tool_handler` logged the *length* of each query and the number of results;
`events.jsonl` recorded `searches` (a count of tool calls) on the `generate` event only, so the
searches spent by writer attempts that later failed were not even counted. Query text and returned
URLs were recorded nowhere. Whether searches were poor, or useful results were found and dropped,
was unanswerable. That mechanism is checkable in this repository and is the warrant for this
decision.

**Why only lengths were recorded, and what that rule actually protects.** RA-016 classifies
`runs/<id>/` as sensitive and splits it in two: `CONTENT_DIRS` (drafts, critiques, disputes,
support manifests, refinements), purged by `purge --content-only` and the retention sweep, and the
signal record (`events.jsonl`, `signals/`), which outlives a content purge and therefore carries
counts and closed vocabularies only. The comment in `search.py` gives the reason the query stays
out of logs: a writer composes it while looking at the question, the seed and the draft, so it can
quote private run material, and a log line would copy that out of the 0700 tree. Neither RA-016
nor any decision forbids *recording* the query. They forbid putting it where content must not go:
the process log and the event log. D-dispute-evidence-prior-draft, D-writer-source-reads (the
support manifest) and D-question-refinement each kept content in a purgeable directory for exactly
this reason. This decision does the same and supersedes nothing.

**Decision.**

1. **One JSON line per `web_search` call**, appended to `runs/<id>/searches/rNN.jsonl` (NN is the
   round the generation produces) as the call returns: `round`, `attempt` (1-based position in the
   generation's writer walk), `alias`, `writer` (resolved identity), `query`, `outcome` (closed:
   `ok`, `budget_exhausted`, `error`) and `results`, a list of `{url, title}`. Snippets are not
   kept: they are the bulkiest untrusted field and add nothing an investigator cannot get by
   opening the URL.
2. **Failed attempts are recorded.** The record is written by a new `on_search` callback that
   `search.make_tool_handler` calls for every query that reached the client, success or failure,
   bound per attempt by `graph._SearchTrail`. A call that later raises (`tool_loop_no_answer`,
   `unparsed_tool_markup`) or is set aside (`unread_draft`) has already written its lines.
   `search.SearchBudgetExhausted` (a `SearchError` subclass) lets the outcome be a closed token
   instead of a parsed message.
3. **`searches` is a `CONTENT_DIRS` entry.** `purge --content-only` and the retention sweep remove
   it with the drafts. `store.read_searches` reads it back, tolerating a missing directory and a
   partially-flushed line.
4. **`audit.json` serves it as `searches`.** The production instance's only path an investigator
   (or agent) can reach is `audit.json` (D-id-as-credential, docs/run-provenance.md); a trail on
   disk only would not have answered the question that motivated this. The report this route's
   siblings already serve publicly is made from the same question, seed and drafts the queries are
   composed from, so serving the queries beside it widens exposure in volume, not in kind.
5. **The signal record carries integers only.** `generate` and `generate_failed` gain `queries`,
   the number of `web_search` calls the attempt made (`searches` on `generate` keeps meaning every
   tool call, reads included). Summing `queries` over `generate_failed` shows how much of the run's
   budget failing attempts spent. Log lines are unchanged: lengths and counts.

**Isolation.** Nothing here enters a model context. The trail is written by the tool handler and
read only by the web layer's `audit.json`; it has no path into `OrchestratorView`, the controller,
any critic, or any writer prompt, so the blind orchestrator, author exclusion and the
untrusted-text boundary are untouched. The search results a writer sees are unchanged.

**Residuals, accepted.** (1) A query may quote a fragment of a seed, and `seed.md` itself is not
served on a public route; the drafts that revise the seed are, so this is the same material at
the same credential. An owner who wants seeds strictly private should not share run ids. (2) A
resumed generation appends a second set of lines for the round it re-runs; `ts` and `attempt` tell
them apart. (3) A full disk drops a trail line with a WARNING (exception type only) rather than
failing the writer's call.

**Rollback.** Remove the `searches` key from `audit.json` to stop serving it; the on-disk trail is
retained under the ordinary content retention.
