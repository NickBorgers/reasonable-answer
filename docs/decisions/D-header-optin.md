## D-header-optin — the notification opt-in lives in the shell, and only until the device is subscribed

**The problem.** D-stop-notification shipped the opt-in as a control at the bottom of the index's
"Your runs" panel — below a table that grows without bound. Two things were wrong with that, and the
second is the one that matters.

It was hard to reach: with a dozen runs listed, the control sat below a screenful of rows, on the one
page a person visits least once they have started something. And it was *absent* exactly where it was
wanted. Starting a run redirects to `/runs/<id>`; the moment someone decides they want telling is the
moment they have just kicked off a 10-25 minute run and are about to put the phone down. On that page
there was no control at all, and no way to get to one without navigating back.

**Installed to a home screen there is no navigating back.** A standalone app has no address bar, no
reload button and no visible history - that is what `display: standalone` means. A control reachable
only from one page, on a surface with no chrome to reach it with, is a control that is not there.

**Decision.** The opt-in moves into the layout shell, beside `how this works`, so it is on every page
the app renders - index, run page, report. One mount point, one script, one state machine.

**It is shown only while this device has no subscription.** A toggle reading "Notifications on" would
be a permanent header element that never changes and never helps, and the header is the most expensive
real estate the page has. Once notifications are on there is nothing left to offer, so the steady
state is an empty header. Turning them *off* belongs to the OS, which owns the permission and exposes
it in Settings on every platform that implements Web Push; a page-level "off" switch would be a second
control that can disagree with the real one. What the script does keep is *reconciliation*: a
permission revoked out of band leaves a subscription the server would go on pushing to, so a `denied`
permission on load tells the server to forget that endpoint rather than waiting for the push service
to start answering `410`.

**It is emitted only for a signed-in caller, which is a rule about strangers and not about tidiness.**
Every `GET` under `/runs/` answers an anonymous reader (D-id-as-credential), so the run and report
pages are reached by people who were handed a link. They have no runs to be notified about, and
`POST /push/subscribe` is a gated write that would refuse them, so a control there is an invitation to
a 403. The key is withheld unless `request.state.viewer` is set.

**On iOS in a browser tab the control stays hidden rather than explaining itself.** `PushManager`
exists there and `subscribe()` rejects, so feature detection alone would show a button that fails.
D-stop-notification put an inline hint in its place; in a header there is no room for a sentence, and
the install affordance is one the browser already offers. Hidden is the honest state: the feature
genuinely is unavailable until the app is installed.
