# Pagination investigation — concluded

## Summary

New audit events arriving between page requests shift the results of offset-based pagination. The user confirmed this as the cause of the reported overlap: page 0 returned IDs `12, 11, 10`, then a new event arrived ahead of those results, so page 1 skipped the first three rows of the changed list and returned `10, 9, 8`. ID 10 appeared twice, and a traversal limited to the original four pages missed ID 1; this does not mean ID 1 was deleted.

## Evidence and mechanism

The client supplied the following observations:

| Time | Request | Result |
| --- | --- | --- |
| 10:02:14.120 | `GET /api/v1/audit-events?page=0&size=3` | IDs `12, 11, 10`; total 12 |
| 10:02:15.890 | `POST /api/v1/audit-events` | 201 |
| 10:02:17.440 | `GET /api/v1/audit-events?page=1&size=3` | IDs `10, 9, 8`; total 13 |

The [read repository](../../src/persistence/query/postgres_read_repository.cpp) orders by `recorded_at DESC` and uses `LIMIT` and `OFFSET`. Each request reads independently, without a shared pagination boundary. When a new matching event sorts ahead of the first page, the second request's offset of three includes ID 10 again and pushes older events onto later pages. The [query contract](../api/query-api.md) specifies descending recording time but does not promise a stable view across requests.

The user confirmed the intervening new log lines as the cause. The request results and access log above were supplied by the user; they were not independently inspected or reproduced during this investigation.

## Other hypotheses

| Hypothesis | Conclusion |
| --- | --- |
| Equal recording times changed the order | Skipped as a cause of this incident. The query has no secondary sort key, so ties remain a separate ordering risk. |
| Different request filters caused the overlap | Skipped. The supplied GET URLs have the same page size and no other filters. |
| Client display handling caused the duplicate | Skipped. The supplied responses already contain ID 10 on both pages. |

## Handover

The product owner needs to decide whether a multi-page traversal must provide a stable view while new events arrive; the current contract does not decide this. No service behavior was changed during the investigation.
