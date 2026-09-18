"""Put PR screenshots on the repo's `pr-assets` branch through the GitHub API, print raw URLs.

    python publish_assets.py --repo OWNER/NAME --dir <pngs> --prefix <pr-slug> [--branch pr-assets]

Uses `gh api` (already authenticated) and never touches the local checkout: images land on
an orphan branch that is never merged and triggers no workflow, and the PR body references
them by `raw.githubusercontent.com` URL. Re-running with the same prefix replaces the files.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
from pathlib import Path


def gh(method: str, path: str, **fields) -> dict | None:
    cmd = ["gh", "api", "-X", method, path]
    if fields:
        cmd += ["--input", "-"]
    res = subprocess.run(cmd, input=json.dumps(fields) if fields else None, text=True, capture_output=True)
    if res.returncode != 0:
        if "Not Found" in res.stderr or '"status":"404"' in res.stdout:
            return None
        raise SystemExit(f"gh api {method} {path} failed:\n{res.stderr}\n{res.stdout}")
    return json.loads(res.stdout) if res.stdout.strip() else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--branch", default="pr-assets")
    args = ap.parse_args()

    repo, branch = args.repo, args.branch
    pngs = sorted(Path(args.dir).glob("*.png"))
    if not pngs:
        raise SystemExit(f"no .png files in {args.dir}")

    tree_entries = []
    for png in pngs:
        blob = gh(
            "POST", f"repos/{repo}/git/blobs",
            content=base64.b64encode(png.read_bytes()).decode(), encoding="base64",
        )
        tree_entries.append(
            {"path": f"{args.prefix}/{png.name}", "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )

    ref = gh("GET", f"repos/{repo}/git/ref/heads/{branch}")
    parents, base_tree = [], {}
    if ref:
        head = ref["object"]["sha"]
        parents = [head]
        base_tree = {"base_tree": gh("GET", f"repos/{repo}/git/commits/{head}")["tree"]["sha"]}
    tree = gh("POST", f"repos/{repo}/git/trees", tree=tree_entries, **base_tree)
    commit = gh(
        "POST", f"repos/{repo}/git/commits",
        message=f"assets({args.prefix}): PR screenshots", tree=tree["sha"], parents=parents,
    )
    if ref:
        gh("PATCH", f"repos/{repo}/git/refs/heads/{branch}", sha=commit["sha"], force=False)
    else:
        gh("POST", f"repos/{repo}/git/refs", ref=f"refs/heads/{branch}", sha=commit["sha"])

    for entry in tree_entries:
        print(f"https://raw.githubusercontent.com/{repo}/{branch}/{entry['path']}")


if __name__ == "__main__":
    main()
