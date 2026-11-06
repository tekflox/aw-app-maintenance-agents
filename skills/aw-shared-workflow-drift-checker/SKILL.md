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
cloned here. Use GitHub code search, once per org (code search `q=` is an
AND of terms, so a cross-org OR needs two calls):

```bash
gh api -X GET search/code --paginate \
  -f q='uses:fredericowu/aw-shared-workflows org:tekflox' \
  --jq '.items[] | "\(.repository.full_name) \(.path)"'

gh api -X GET search/code --paginate \
  -f q='uses:fredericowu/aw-shared-workflows org:fredericowu' \
  --jq '.items[] | "\(.repository.full_name) \(.path)"'
```

Merge and de-duplicate the two result sets (a repo matching both org filters
is impossible, but a repo with the workflow referenced in more than one
caller file is not — keep each `(repo, path)` pair). For each `(repo, path)`
pair, read the live file content:

```bash
gh api repos/<repo>/contents/<path> --jq '.content' | base64 -d
```

and extract every line matching
`uses:\s*fredericowu/aw-shared-workflows/\.github/workflows/(\S+?\.yml)@(v\S+)`
— capture group 1 is `shared_file`, group 2 is `pinned_tag`. A single caller
file can pin more than one of the three shared workflows; treat each match as
its own row: `(consumer_repo, consumer_path, shared_file, pinned_tag)`.

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
`(consumer_repo, shared_file, LATEST)` must not get a second one tomorrow:

```bash
gh pr list --repo <consumer_repo> --state open \
  --head "devteam/wfbump-<LATEST>-<shared_file slug>" --json number
```

If one exists, skip this row (it's still being reviewed/merged) — do not
open a duplicate.

Otherwise, shallow-clone the consumer's default branch, edit the one line,
and open the PR:

```bash
git clone -q --depth 1 https://github.com/<consumer_repo>.git \
  .tmp/shared-workflow-drift/<consumer_repo slug>
cd .tmp/shared-workflow-drift/<consumer_repo slug>
git checkout -b devteam/wfbump-<LATEST>-<shared_file slug>
# edit <consumer_path>: the one `uses: ...@<pinned_tag>` line -> `@<LATEST>`
git commit -am "ci: bump <shared_file> pin to <LATEST>"
git push -u origin devteam/wfbump-<LATEST>-<shared_file slug>
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

Do not touch the consumer repo. Open a Kanban card instead:

```
create_kanban_task(
  title="Bump <shared_file> pin + caller permissions on <consumer_repo> (v<pinned_tag> -> v<LATEST>)",
  body=<see below>,
  agent_slug="coder-sonnet",
  target_slug="aw-pipelines-test-coverage",
  tags=["testing", "ci-cd", "shared-workflows"],
)
```

Body must include: the consumer repo and caller file path, old and new tag,
the full diff of the three contract blocks (permissions / workflow_call
inputs / workflow_call secrets) between `<pinned_tag>` and `<LATEST>`, and an
explicit citation of `docs/standards/pipeline-testing.md` §2a (the caller
checklist this mirrors). State plainly that the coder's job is to bump the
pin **and** edit the consumer's own `permissions:`/secrets to match — not
just the one-line bump.

No PR-count cap applies to this path (it's a Kanban card, not a PR) — open
one per contract-changed row found, every run, same idempotency concern as
step 5: before opening, check whether an open card with this same
`(consumer_repo, shared_file, LATEST)` combination already exists
(`search_knowledge_base` and/or a Kanban search on the title) and skip if so.

## 7. Report

End with a plain-text report, not a presentation and not `mark_flow_done`:

- `LATEST` tag found.
- Total `(consumer, shared_file, pinned_tag)` rows discovered, across both
  org searches.
- Rows skipped because already at `LATEST`.
- Rows skipped because content-identical despite a stale tag (the expected
  majority — name them or at least give the count).
- Rows that were real drift, split mechanical vs. contract-changed.
- PRs opened (links), PRs skipped as already-open-duplicate, and — if the
  10-PR cap was hit — exactly which rows were deferred to the next run.
- Kanban cards opened for contract-changed rows (links), and cards skipped
  as already-existing.
- Anything you could not verify (a `gh api search/code` call that errored or
  looked incomplete, a repo you could not clone) — say so explicitly rather
  than silently treating a failed search as "no consumers there."
