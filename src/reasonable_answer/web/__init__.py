"""Web interface: submit a question, watch the loop converge, browse the audit trail.

Deployment posture is **tailnet-only, no authentication** (see app.py). Do not put
this on the public internet without adding real auth in front of it.
"""

from .app import create_app

__all__ = ["create_app"]
