## D-self-refreshing-index — the runs list corrects itself, because an installed app cannot be reloaded by hand

**The problem.** The index is a snapshot. A run finishes, and the table goes on saying `running` until
something reloads the page. In a browser tab that is a non-issue - the reader hits reload. Installed
to a home screen it is a defect with no user-side fix: a standalone app has no address bar, no reload
button and no pull-to-refresh inside the page — the removed browser chrome D-installable-pwa and
D-header-optin already record as what `display: standalone` means. The one mechanism the index relied
on is the one the platform removes.

That is not cosmetic. D-installable-pwa states the rule this violates outright - *a finished run
displayed as still running is the one output this interface must not produce* - and spends its whole
service-worker design on preventing it in the cache. It was being produced anyway, one layer up, by a
page that had no way to correct itself.

**Decision.** The runs table refreshes itself. `render_index_rows` renders the `<tbody>` as a
fragment, `GET /runs-table` serves it, and the page swaps the element in place.

**The visibility handler is the load-bearing half, not the interval.** The realistic failure is not a
page left open in the foreground for five minutes; it is an app backgrounded for an hour and then
swiped back to. A suspended iOS PWA runs no JavaScript at all — the platform behavior D-stop-notification
already rests on — so an interval alone is frozen while backgrounded, resumes late on return
and shows stale rows for a beat first, precisely at the moment of maximum attention. Refreshing on
`visibilitychange` means the list is current *before* it is looked at. `pageshow` with `persisted`
covers the back-forward cache, which restores a page wholesale and runs no tick at all.

**The interval runs only while something is live, and stops itself.** `data-live` is computed on the
server from the same `is_live` the rows are rendered from and travels *on the fragment*, so the flag
and the rows it describes can never disagree - and the client never infers liveness by scraping status
text. When a refresh returns `data-live="0"` the loop ends, so an idle index left open on a phone
costs nothing.

**One renderer, so the two views cannot drift.** The page and the endpoint both call
`render_index_rows`. The fragment is the whole `<tbody>` rather than its rows so the swap is one node
and the refreshed flag arrives with the rows in a single step.

**`/runs-table`, not `/runs/table`.** `_PUBLIC_GET_PREFIX` is the string `"/runs/"` and every `GET`
beneath it is anonymous (D-id-as-credential). This is a per-viewer, owner-scoped list - the index's own
body - so it must be gated, and the sibling name is what keeps it outside the prefix by construction
rather than by a special case. `/runs/table` would have read as the natural name and would have
published one person's index to anyone holding any run id. A test asserts the path is not inside the
prefix.

**Unconditional, unlike refine and notifications.** This is not a feature to opt into; it is the
repair of a staleness the installed app cannot fix by hand. It needs no new opt-in, no external-service
credential and no outbound request. `/runs-table` stays authenticated and owner-scoped exactly like the
index whose body it is — identity-required in `authentication.md`, gated by construction outside the
public `/runs/` prefix — so it reveals nothing the index does not already show its own viewer.
