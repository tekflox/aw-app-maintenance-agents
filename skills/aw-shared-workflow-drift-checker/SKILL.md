---
name: aw-shared-workflow-drift-checker
description: Daily content-aware check for consumer repos whose pinned fredericowu/aw-shared-workflows tag is behind latest — auto-opens a mechanical one-line bump PR when the caller contract is unchanged, or routes a Kanban card to a coder when it changed. Use whenever the first user message begins with `/aw-shared-workflow-drift-checker`, or when the "Shared-workflow drift check" scheduled task fires.
---

# aw-shared-workflow-drift-checker — content-aware pin drift → auto-PR

You check whether consumer repos' pinned `uses:
fredericowu/aw-shared-workflows/...@vX` tags are **behind the file's actual
content**, not just behind the tag number — most pins are tag-stale but
content-current, because the shared repo deliberately runs one `v*` tag line
across three independent workflow files (a fix to one cuts a tag the other
two's callers never need to bump). Design: Kanban card
`4105bf3b-9510-8135-87b3-efd239f88ae4`, comments "DESIGN — shared-workflow
drift auto-PR" and the rejected-alternatives comment right after it — read
both before changing this skill; this file implements that design, it does
not re-derive it.

Two rules that override anything you might infer on your own:

1. **Compare by content, diffing the actual file between tags — never by tag
   number alone.** A tag-stale pin whose file is byte-identical at both tags
   is not drift; skip it silently. This is the entire point — a tag-number
   bumper floods every consumer with a no-op PR on every cut.
2. **Never merge anything, and never touch a tag or branch inside
   `aw-shared-workflows` itself.** You only ever edit a `uses:` string in a
   *consumer* repo, and only ever open a PR there — the consumer's own
   PR-first review/merge loop decides the rest, same as every other Dev Team
   delivery.

This checker is deliberately the **fallback** path, not the only path: when a
consumer repo already has some other PR open and touching it (e.g. a
fleet-migration batch), the convention for *that other dispatch* is to bump
the shared-workflow pin inline on its own PR rather than wait for this
checker. This checker does not need to implement "is there an open PR
already on this repo" detection itself — it only has to not flood a repo that
nothing else bumped, which the idempotency checks in steps 5 and 6 already
cover.

Not run inside Agents Flow — end with a plain-text report, not
`mark_flow_done`.

---

## 0. Setup

```bash
cd <scratch clone of fredericowu/aw-shared-workflows>   # see step 1
```

You need a local checkout of `fredericowu/aw-shared-workflows` with tags
fetched, and the ambient admin `gh`/`git` identity this container already
carries for Dev Team work (no new secret, no new credential — this is the
whole point of the design's rejected-Dependabot/rejected-PAT alternatives).
If `/opt/aw-workspace/repos/aw-shared-workflows` already exists, use it —
`git fetch origin --tags -q` first, don't assume it's current. Otherwise
shallow-clone it to `.tmp/shared-workflow-drift/aw-shared-workflows` (see
`AGENTS.md`'s `.tmp/` convention):

```bash
git clone -q https://github.com/fredericowu/aw-shared-workflows.git \
  .tmp/shared-workflow-drift/aw-shared-workflows
```

## 1. Discover the latest tag

```bash
gh api repos/fredericowu/aw-shared-workflows/tags --jq '.[].name'
```

Sort as semver (`v1.10.0` > `v1.9.0`, not lexically) and take the highest
`v*` tag — call it `LATEST`.

## 2. Discover consumers — live, not from a manifest

Not a committed list, not local checkouts under `repos/` — both go stale as
the fleet migration adds consumers weekly and some consumers are never
cloned here.

**Do not use `gh api search/code`.** Verified live (2026-11-07): GitHub's
REST code-search endpoint is unreliable once a `repo:`/`org:` qualifier is
added — it returned `total_count: 0` against `tekflox/aw-stack` for the
exact pin line confirmed present by direct content fetch, and the same
zero-result failure reproduced against a massively popular, long-indexed
public repo (`tiangolo/fastapi`) for a trivial term (`def`) and against this
very workspace's own public repo (`fredericowu/aw-workspace`). An *unscoped*
query (no `repo:`/`org:`) does return real results (millions of hits), so
the scoped index specifically is the broken part — `repo:torvalds/linux` is
the only scoped query that returned anything in testing, suggesting the
legacy code-search backend only reliably indexes a handful of
exceptionally old/large repos, not an ordinary consumer repo regardless of
visibility or popularity. Relying on it means this checker finds zero
consumers on every run and silently reports "nothing drifted" — worse than
a visible failure.

Instead, **enumerate repos directly and grep their content** — no search
index involved, same `contents` API already used to read files:

```bash
# once per org
gh repo list tekflox --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner' \
  > .tmp/shared-workflow-drift/consumers.txt
gh repo list fredericowu --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner' \
  >> .tmp/shared-workflow-drift/consumers.txt

# per repo: list its workflow files (skip repos with no .github/workflows — 404)
gh api repos/<repo>/contents/.github/workflows --jq '.[].name' 2>/dev/null

# per workflow file: fetch content
gh api repos/<repo>/contents/.github/workflows/<file> --jq '.content' | base64 -d
```

This was dry-run live end-to-end against `tekflox/aw-stack` (list its 10
workflow files, fetch each, grep) and correctly found all 3 real pins
(`ai-review.yml`, `code-review.yml`, `security-scan.yml` — three different
shared files, three different tags). A `contents` 404 (repo has no
`.github/workflows` dir) just means skip that repo — not an error. This path
also sidesteps `search/code`'s separate, much tighter `code_search` rate
limit (10 requests/minute) since `contents` uses the normal 5000/hour quota.

For each repo's fetched file content, extract every line matching
`uses:\s*fredericowu/aw-shared-workflows/\.github/workflows/(\S+?\.yml)@(v\S+)`
— capture group 1 is `shared_file`, group 2 is `pinned_tag`. A single caller
file can pin more than one of the three shared workflows; treat each match as
its own row: `(consumer_repo, consumer_path, shared_file, pinned_tag)`.

**`shared_file_slug` is defined once, deterministically, and reused
everywhere a slug is needed** (branch names in step 5, the idempotency
check in step 5, the `finding_key` in step 6): strip the trailing `.yml`
from `shared_file` and apply no other transformation — e.g.
`code-review-reusable.yml` → `code-review-reusable`. There are only three
possible shared files, so this is a fixed, closed set; do not re-derive or
paraphrase it each run.

## 3. Compare by content, not tag number

For each row, skip immediately if `pinned_tag == LATEST` (nothing to check —
already current). Otherwise, in the local `aw-shared-workflows` checkout:

```bash
git diff <pinned_tag> <LATEST> -- .github/workflows/<shared_file>
```

**Empty diff → skip.** This is the tag-stale-but-content-current case that
is the whole reason this checker exists instead of a tag-number bumper — do
not open anything for it, do not comment anywhere, just move on.

**Non-empty diff → real drift.** Continue to step 4 for this row.

## 4. Classify: mechanical bump vs. contract-changed

Read the diff from step 3. The "caller contract" is: the shared file's own
top-level `permissions:` block, its `on.workflow_call.inputs:` block, and its
`on.workflow_call.secrets:` block (see `docs/standards/pipeline-testing.md`
§2a — this is exactly the checklist that section's own test,
`test_the_reusable_requests_every_scope_the_caller_checklist_demands`,
machine-compares).

- **Unchanged** (the diff touches step logic, comments, or anything outside
  those three blocks) → **mechanical bump**, step 5.
- **Changed** (any line inside `permissions:`, `workflow_call.inputs:`, or
  `workflow_call.secrets:` differs between the two tags) → **contract-changed
  bump**, step 6. Do not attempt to resolve this yourself by editing the
  consumer's own `permissions:`/secrets — that is a judgment call for a
  coder with the specific repo in front of them.

## 5. Mechanical bump — auto-open the PR

**Idempotency check first** — a bump PR already open for this exact
`(consumer_repo, shared_file, LATEST)` must not get a second one tomorrow.
Use the `shared_file_slug` defined in step 2 — never re-slug the filename
by hand:

```bash
gh pr list --repo <consumer_repo> --state open \
  --head "devteam/wfbump-<LATEST>-<shared_file_slug>" --json number
```

If one exists, skip this row (it's still being reviewed/merged) — do not
open a duplicate.

Otherwise, shallow-clone the consumer's default branch, edit the one line,
and open the PR:

```bash
git clone -q --depth 1 https://github.com/<consumer_repo>.git \
  .tmp/shared-workflow-drift/<consumer_repo slug>
cd .tmp/shared-workflow-drift/<consumer_repo slug>
git checkout -b devteam/wfbump-<LATEST>-<shared_file_slug>
# edit <consumer_path>: the one `uses: ...@<pinned_tag>` line -> `@<LATEST>`
git commit -am "ci: bump <shared_file> pin to <LATEST>"
git push -u origin devteam/wfbump-<LATEST>-<shared_file_slug>
gh pr create --repo <consumer_repo> \
  --title "ci: bump <shared_file> pin <pinned_tag> -> <LATEST>" \
  --body "Mechanical pin bump — <shared_file> content changed between <pinned_tag> and <LATEST> (diffstat below), caller contract (permissions/workflow_call inputs/secrets) is unchanged, so this is a one-line bump with no caller-side edit needed.

\`\`\`
<git diff --stat <pinned_tag> <LATEST> -- .github/workflows/<shared_file>, run inside the aw-shared-workflows checkout>
\`\`\`

Opened by the shared-workflow drift checker (daily scheduled task). Never
merges automatically — this repo's own review/merge loop decides."
```

Opened under the ambient admin `gh` identity (**not** a `GITHUB_TOKEN`
Actions-bot PR — those don't trigger the consumer's own workflows, which
would defeat the point of letting its review loop run). Let the consumer's
own PR-first review/merge loop run as normal; you do not watch it, approve
it, or merge it.

**Cap and stagger: at most 10 PRs opened in a single run.** Process rows in a
stable order (e.g. sorted by `consumer_repo`) and stop at 10 — the remaining
rows are still drifted and will be picked up by tomorrow's run, there is no
urgency pushing you past the cap. If you hit the cap, say so explicitly in
the report (which rows were left for next time), per `AGENTS.md`'s "no
silent caps" — don't let a 60-repo fleet read as "fully checked today" when
it wasn't.

## 6. Contract-changed bump — route to a coder

Do not touch the consumer repo. Open a Kanban card instead, using the real
`create_kanban_task` signature (it has no `body` param — use `input_text`
for the full body, `description` for the short callout):

```
create_kanban_task(
  title="Bump <shared_file> pin + caller permissions on <consumer_repo> (v<pinned_tag> -> v<LATEST>)",
  description="<shared_file> drifted v<pinned_tag> -> v<LATEST> on <consumer_repo>; caller contract changed, needs a coder.",
  finding_key="shared-workflow-drift:<consumer_repo>:<shared_file_slug>:<LATEST>",
  input_text=<see below>,
  agent_slug="coder-sonnet",
  target_slug="aw-pipelines-test-coverage",
  tags=["testing", "ci-cd", "shared-workflows"],
)
```

`input_text` must include: the consumer repo and caller file path, old and
new tag, the full diff of the three contract blocks (permissions /
workflow_call inputs / workflow_call secrets) between `<pinned_tag>` and
`<LATEST>`, and an explicit citation of `docs/standards/pipeline-testing.md`
§2a (the caller checklist this mirrors). State plainly that the coder's job
is to bump the pin **and** edit the consumer's own `permissions:`/secrets to
match — not just the one-line bump.

No PR-count cap applies to this path (it's a Kanban card, not a PR) — call
`create_kanban_task` once per contract-changed row, every run, with the
`finding_key` above. Dedup is the tool's own job, not yours: a repeat
`finding_key` bumps the existing card's `OccurrenceCount` instead of
creating a duplicate (and reopens it if it was already closed as a
regression) — do not also search Kanban or the knowledge base for an
existing card first, the `finding_key` already makes that redundant.

## 7. Report

End with a plain-text report, not a presentation and not `mark_flow_done`:

- `LATEST` tag found.
- Repos enumerated across both orgs, and total `(consumer, shared_file,
  pinned_tag)` rows discovered from them.
- Rows skipped because already at `LATEST`.
- Rows skipped because content-identical despite a stale tag (the expected
  majority — name them or at least give the count).
- Rows that were real drift, split mechanical vs. contract-changed.
- PRs opened (links), PRs skipped as already-open-duplicate, and — if the
  10-PR cap was hit — exactly which rows were deferred to the next run.
- Kanban cards opened for contract-changed rows (links + `finding_key`), and
  which ones were a repeat `finding_key` bump rather than a new card.
- Anything you could not verify (a `contents` fetch that 404'd/errored on a
  repo you expected to have workflows, a repo you could not clone) — say so
  explicitly rather than silently treating a failed fetch as "no consumers
  there."
