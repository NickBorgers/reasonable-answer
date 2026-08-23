## D-writer-resolver-budget — writer reads and verification share resolver call pools

**Finding.** D-writer-source-reads deliberately gave the reader and evidence lens one
`SourceFetcher`: a page is downloaded once, both consumers see the same bytes, and a failed page is
not retried into a different answer later in the run. The fetcher also owns one resolver ladder.
Consequently, a writer read whose direct fetch yields no body spends the same identifier,
open-access, and extraction call pools that later verification draws on. The original decision
specified the shared character-cap consequence but left this call-budget consequence unstated, and
the extraction tier's derived ceiling reserved calls only for cited URLs.

**Decision.** Keep the shared resolver as part of the shared-fetcher contract. Writer reads are
supposed to benefit from the configured resolver tiers; a separate resolver would duplicate
provider calls and could let the writer and critic observe different outcomes for the same URL.
The tier budgets are therefore whole-run pools across writer reads and verification. The derived
extraction ceiling reserves `search.max_sources * budgets.hard_cap` calls for the maximum citation
shape and, when `search.read_sources` is on, another `search.read_budget` calls for candidate pages.
An explicit `sources.extraction.max_calls_per_run`, and the fixed identifier and open-access caps,
remain operator-selected limits and may be lower than that structural demand.

This does not let reading switch on the evidence channel: `search.verify_sources` still alone
controls whether `Runtime.fetcher` exists and clips what the evidence path sees. It does mean that
reading can change a later resolution outcome to `budget_exhausted`, which is recorded rather than
misstated as a property of the source. Tests exercise the shared-pool transition and the enlarged
derived extraction ceiling.
