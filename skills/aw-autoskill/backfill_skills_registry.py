#!/usr/bin/env python3
"""
aw-autoskill/backfill_skills_registry.py

One-shot backfill: publish every skill already sitting in this workspace's
aw-autoskill store (.aw-workspace/data/aw-autoskill/skills/<slug>/SKILL.md)
to the aw-skills registry, so the registry stops being the write-time-only
path and actually matches the store that predates it.

Design (card 4115bf3b, Architect execution design, WP1):
  - Direct REST, not the aw__skills__* MCP tools — this runs as a standalone
    script, and the in-process MCP token is known to go stale (separately
    tracked). Auth token comes from this workspace's own skills-app config
    (`aw-workspace-cli apps skills --json` -> config.identity_token), the
    same one the Onda-3 architect proved works directly against the service.
  - owner_kind/owner_ref are assigned server-side from the caller's identity
    token on every write — nothing to pass for those.
  - visibility='private', tenant_slug='aw', source='mcp' on every write (the
    Onda-3 "Peça 6 / Decisão 1" mechanism this backfill is the catch-up for).
  - Dup-check via GET /api/skills/{slug}/content BEFORE any POST/PUT — never
    blind-retry into aw-skills' single-worker git push queue.
  - sha256 byte-compare of the registry's returned `content` (UTF-8 encoded,
    no normalization) against the local SKILL.md bytes. Exact match -> noop.
    Mismatch on an existing skill -> one reconcile PUT with the local bytes,
    then re-GET and re-compare; still divergent -> named per-slug failure,
    and that slug is left alone (never retried again in the same run).
  - Smoke test first: the alphabetically-first slug is processed and
    byte-verified alone before touching the rest. --smoke-only stops there.
  - Sequential, never parallel — the registry's git worker is single-worker
    (PUSH_WAIT_S=8 per write already serializes on the server side); a small
    extra sleep between skills (--sleep-s) keeps GETs from hammering it too.

Usage:
  python3 skills/aw-autoskill/backfill_skills_registry.py [--smoke-only]
      [--store-dir PATH] [--base-url URL] [--tenant-slug aw]
      [--sleep-s 0.5] [--report-file PATH]

Output: a human-readable summary to stdout, and a JSON report (counts +
per-slug outcome) written to --report-file for evidence.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR = Path(os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"))
DEFAULT_STORE_DIR = WORKSPACE_DIR / ".aw-workspace" / "data" / "aw-autoskill" / "skills"
DEFAULT_BASE_URL = os.environ.get("AW_SKILLS_BASE_URL", "http://aw-skills:10016")
DEFAULT_REPORT_FILE = WORKSPACE_DIR / ".tmp" / "aw-autoskill-registry-backfill-report.json"
REQUEST_TIMEOUT_S = 20


def get_identity_token() -> str:
    """Pull this workspace's skills-app identity token the same way the
    Onda-3 architect proved works directly against the service — never
    through the in-process MCP path, which can serve a stale token."""
    out = subprocess.run(
        ["aw-workspace-cli", "apps", "skills", "--json"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    cfg = json.loads(out.stdout)["config"]
    token = cfg["identity_token"]
    if not token:
        raise RuntimeError("empty identity_token in `aw-workspace-cli apps skills --json`")
    return token


def parse_description(content: str) -> str | None:
    """Pull the one-line `description:` out of the SKILL.md frontmatter."""
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    for line in content[3:end].splitlines():
        line = line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip().strip('"').strip("'")
    return None


class RegistryClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"detail": raw.decode(errors="replace")}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Connection refused / reset / timed out — not an HTTP response at
            # all, so there's no status code. Surface it as one, 0, so every
            # caller's `status == 200` / `status == 404` checks fall through
            # to the generic failure branch instead of raising past them and
            # losing every slug already processed this run.
            return 0, {"detail": str(e)}

    def get_content(self, slug: str) -> tuple[int, dict | None]:
        return self._request("GET", f"/api/skills/{slug}/content")

    def create(self, slug: str, description: str | None, content: str, tenant_slug: str) -> tuple[int, dict | None]:
        body = {
            "slug": slug,
            "description": description,
            "content": content,
            "visibility": "private",
            "tenant_slug": tenant_slug,
            "source": "mcp",
        }
        return self._request("POST", "/api/skills", body)

    def update(self, slug: str, content: str, message: str) -> tuple[int, dict | None]:
        body = {"content": content, "message": message, "source": "mcp"}
        return self._request("PUT", f"/api/skills/{slug}", body)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def backfill_one(client: RegistryClient, slug: str, local_content: str, tenant_slug: str) -> dict:
    """Returns one ledger-shaped result dict for this slug."""
    local_hash = sha256_hex(local_content)
    status, body = client.get_content(slug)

    if status == 404:
        description = parse_description(local_content)
        c_status, c_body = client.create(slug, description, local_content, tenant_slug)
        if c_status != 201:
            return {"slug": slug, "outcome": "create_failed", "http_status": c_status, "detail": c_body}
        # Re-GET and byte-compare per spec — never trust the write response alone.
        v_status, v_body = client.get_content(slug)
        if v_status != 200:
            return {"slug": slug, "outcome": "create_verify_failed", "http_status": v_status, "detail": v_body}
        registry_hash = sha256_hex(v_body["content"])
        if registry_hash != local_hash:
            return {"slug": slug, "outcome": "create_divergent", "local_sha256": local_hash, "registry_sha256": registry_hash}
        return {"slug": slug, "outcome": "created", "sha256": local_hash}

    if status != 200:
        return {"slug": slug, "outcome": "get_failed", "http_status": status, "detail": body}

    registry_hash = sha256_hex(body["content"])
    if registry_hash == local_hash:
        return {"slug": slug, "outcome": "noop_identical", "sha256": local_hash}

    # Exists and diverges (the aw-autoskill-aw-roblox-publish case) — one
    # reconcile write with the store's bytes, then re-verify. Never retry
    # beyond this single reconcile attempt.
    u_status, u_body = client.update(slug, local_content, "backfill reconcile")
    if u_status != 200:
        return {"slug": slug, "outcome": "reconcile_failed", "http_status": u_status, "detail": u_body}
    v_status, v_body = client.get_content(slug)
    if v_status != 200:
        return {"slug": slug, "outcome": "reconcile_verify_failed", "http_status": v_status, "detail": v_body}
    final_hash = sha256_hex(v_body["content"])
    if final_hash != local_hash:
        return {"slug": slug, "outcome": "still_divergent", "local_sha256": local_hash, "registry_sha256": final_hash}
    return {"slug": slug, "outcome": "reconciled", "sha256": local_hash}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store-dir", type=Path, default=DEFAULT_STORE_DIR)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--tenant-slug", default="aw")
    ap.add_argument("--sleep-s", type=float, default=0.5)
    ap.add_argument("--smoke-only", action="store_true", help="process only the first slug, then exit")
    ap.add_argument("--report-file", type=Path, default=DEFAULT_REPORT_FILE)
    args = ap.parse_args()

    if not args.store_dir.is_dir():
        print(f"store dir not found: {args.store_dir}", file=sys.stderr)
        return 2

    slugs = sorted(
        d.name for d in args.store_dir.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )
    if not slugs:
        print(f"no SKILL.md dirs under {args.store_dir}", file=sys.stderr)
        return 2

    token = get_identity_token()
    client = RegistryClient(args.base_url, token)

    results: list[dict] = []

    # Smoke test: the first slug alone, verified, before touching the rest.
    smoke_slug = slugs[0]
    smoke_content = (args.store_dir / smoke_slug / "SKILL.md").read_text()
    smoke_result = backfill_one(client, smoke_slug, smoke_content, args.tenant_slug)
    results.append(smoke_result)
    print(f"[smoke] {smoke_slug}: {smoke_result['outcome']}")
    if smoke_result["outcome"] not in ("created", "noop_identical", "reconciled"):
        print("Smoke test did not succeed — stopping before the rest of the batch.", file=sys.stderr)
        _write_report(args.report_file, results, args.store_dir, aborted=True)
        return 1

    if args.smoke_only:
        _write_report(args.report_file, results, args.store_dir, aborted=False)
        return 0

    aborted = False
    try:
        for slug in slugs[1:]:
            time.sleep(args.sleep_s)
            content = (args.store_dir / slug / "SKILL.md").read_text()
            try:
                result = backfill_one(client, slug, content, args.tenant_slug)
            except Exception as e:  # noqa: BLE001 — one slug's bug must not sink the other 70's evidence
                result = {"slug": slug, "outcome": "exception", "error": repr(e)}
            results.append(result)
            print(f"{slug}: {result['outcome']}")
    finally:
        # Always write what was gathered so far — a crash partway through
        # must still leave a reviewable report of every slug already
        # processed, not an empty file.
        _write_report(args.report_file, results, args.store_dir, aborted=aborted)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print("\n--- summary ---")
    print(f"total: {len(results)}")
    for outcome, n in sorted(counts.items()):
        print(f"  {outcome}: {n}")

    failures = [r for r in results if r["outcome"] not in ("created", "noop_identical", "reconciled")]
    return 1 if failures else 0


def _write_report(path: Path, results: list[dict], store_dir: Path, aborted: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "store_dir": str(store_dir),
        "total_local_skills": len(results),
        "aborted": aborted,
        "results": results,
    }
    path.write_text(json.dumps(payload, indent=2))
    print(f"report written: {path}")


if __name__ == "__main__":
    sys.exit(main())
