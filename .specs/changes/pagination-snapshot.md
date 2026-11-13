# Change card: snapshotId in the response

## Behavior we change

A GET without `snapshotId` currently reads an unbounded result set and returns no boundary. It will resolve an upper boundary S once, apply `id <= S` to its own content and `totalElements`, and return S as `snapshotId`.

This addresses the moving OFFSET window described in [Pagination window shift — handover](../investigations/pagination-window-shift.md), including its session-3 handover. The decisions below come from [Session 3 — The next contract](../../.training/session-3/README.md#the-next-contract). The owner approved the contract and plan; implementation has not been authorized.

## Behavior we preserve

- Keep zero-based `page`, default page 0, default size 50, maximum size 200, and existing validation.
- Preserve actor, time, action and outcome filters, including blank-actor handling and enum validation.
- Keep `recordedAt DESC, id DESC` ordering.
- Apply a supplied `snapshotId` to both content and total under the same filters.
- Preserve append-only events, database UPDATE/DELETE guards, and the separate read path.

## Input and output of the new contract

- Endpoint: `GET /api/v1/audit-events`.
- Input: existing filters, `page`, `size`, and optional signed 64-bit `snapshotId`.
- If `snapshotId` is absent, S is the global maximum stored event ID before user filters; an empty journal gives S=0. S is independent of the requested page and is not derived from a row in that page.
- If `snapshotId` is supplied, use and return exactly that S, including zero, without recalculation.
- Output: existing `content`, `page`, `size` and `totalElements`, plus numeric `snapshotId` containing the effective S.
- A filter with no matches returns empty content and `totalElements=0`, retaining S.
- Later requests repeat S, filters and size. Events appended after the first GET completes have IDs above S and remain outside that traversal; its total stays constant and its pages cover the original matching set without duplicates or gaps.

This intentionally replaces the current [Query API contract](../api/query-api.md#snapshot-boundary) behavior for an omitted S. Global versus filtered S and empty-result behavior, left open in the investigation, are answered by the approved session-3 selections above.

## What is outside this change

- No append-path changes, event UPDATE/DELETE behavior, migrations, dependency installation, CI changes or unrelated refactoring.
- No new endpoint, opaque token, cursor, keyset pagination or caching.
- No guarantee for arbitrary overlapping transaction commits: an identifier ceiling is not an MVCC snapshot. The exercise covers appends after the first GET completes.
- Approval of this plan does not authorize a code change.

## Executable readiness checks

The session-3 acceptance scenario uses HTTP and real PostgreSQL without parallel writes:

1. Create two events for a new actor A, then one for a new actor B; save all three IDs.
2. GET `actor=A&page=0&size=1` without S. Expect `snapshotId` equal to B's ID and `totalElements=2`, proving that S is global rather than filtered.
3. After that GET completes, append another A event.
4. GET `actor=A&page=1&size=1&snapshotId=S`. Both responses must retain S and `totalElements=2`; together their pages contain exactly the two original A events, without duplicates or the new A.

Additional contract checks cover an empty journal (S=0), a filter with no matches (global S retained), reuse of supplied S including zero, and an initial request at a nonzero page. An empty-journal check must use an isolated fixture and must not clear an existing database.

For an authorized implementation, the repository checks are:

```sh
cmake -S . -B build
cmake --build build -j2
ctest --test-dir build --output-on-failure
tests/run-postgres.sh build/audit-log-service
git diff --check
```

Current evidence is source inspection only; no build, unit or PostgreSQL integration checks have been run for this task. Existing pagination checks cover the earlier explicit-input boundary, not automatic boundary resolution and response reporting.

| Requirement | Runtime evidence |
| --- | --- |
| Resolve global S and apply it to the first GET | UNKNOWN — not run; current code has no automatic resolution |
| Return and reuse S, including zero | UNKNOWN — not run; current response has no snapshotId field |
| Stable traversal after a completed GET followed by append | UNKNOWN — new acceptance scenario not run |
| Empty journal, no matches and nonzero starting page | UNKNOWN — not run |
| Preserve filters, validation, ordering and append-only guards | UNKNOWN — existing checks inspected, not run |

## Approved plan and expected files

1. Add the combined HTTP/PostgreSQL scenario above to `tests/integration.py`, registering its class after `QueryTests`. Run it against the unchanged implementation and confirm that it fails because snapshot behavior is missing, rather than because of setup or fixtures.
2. Add checks in `tests/integration.py` for supplied S including zero, no matches, a nonzero starting page, and an empty journal using a separate temporary fixture with the existing schema and guards and no demo seeds. Do not clear a database.
3. Add the effective boundary to `include/audit/domain/event_page.hpp`. In `src/persistence/query/postgres_read_repository.cpp`, resolve an absent S once from the global `COALESCE(MAX(id), 0)`, retain a supplied S, apply it to content and count, and return it. In `src/api/query/routes.cpp`, serialize it as `snapshotId`. Update the fake page result and check service propagation in `tests/unit.cpp`. The existing service and repository interface can remain unchanged.
4. Align `.specs/api/query-api.md` with the approved behavior. Record actual changed files and check results in this card. Preserve existing tests, including the moving-window characterization for separate requests that each omit S.
5. Run the commands listed above before implementation and after the change, plus `git diff --stat` and `git status --short`. The integration runner executes the full suite; record the named scenario and actual result. If a check is blocked, record the blocker and mark the evidence UNKNOWN.

Expected changed files: this card, `tests/integration.py`, `include/audit/domain/event_page.hpp`, `src/persistence/query/postgres_read_repository.cpp`, `src/api/query/routes.cpp`, `tests/unit.cpp`, and `.specs/api/query-api.md`.

Actual changed files so far: this card only. No application or test changes have been made.

## When the agent must stop

- Stop after saving the approved plan. Do not begin tests or implementation without authorization.
- If later implementation is authorized, stop and clarify any conflict with the approved contract or any need to exceed its scope.
- Do not weaken tests or append-only guards. If a prerequisite prevents a check, record the actual blocker and mark the affected evidence UNKNOWN; never claim a historical result as a current PASS.

The owner approved the plan and its contract. Implementation authorization remains separate.
