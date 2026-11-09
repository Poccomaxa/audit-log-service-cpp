# Pagination investigation — working record

## Short summary

Page 0 returned IDs 12, 11, 10; page 1 repeated ID 10. A successful POST appeared in the access log between the GETs, and the reported total increased from 12 to 13. The leading explanation is that the new event shifted the ordered list between requests. Another possibility is that events with equal recording times changed order. Neither cause is confirmed: check the POST response and the relevant event timestamps and ordering in the client's database.

## Symptom and boundary

The client report says `GET /api/v1/audit-events?page=0&size=3` returned IDs `12, 11, 10`, and `page=1&size=3` returned `10, 9, 8`. ID 10 appeared twice; the client reported not seeing ID 1 after four pages. These are supplied observations, not responses or database contents independently inspected here. The user subsequently supplied the request times and totals: at 10:02:14.120 page 0 returned `12, 11, 10` with `totalElements: 12`; at 10:02:17.440 page 1 returned `10, 9, 8` with `totalElements: 13`. The remaining page responses, event timestamps and exact database changes are unknown. A missing ID in a traversal does not establish that the event was deleted.

Inspected checked-out branch `blitz-session-2-a_lebedev`, revision `46b9deb22f5b1278f5100fac51b56749e56c2123`. No service, Docker or database was started, and no events were created. The current [Query API specification](../api/query-api.md) exists: pages are zero-based, size defaults to 50 (maximum 200), and results sort by `recordedAt` descending. It does not promise a stable view across requests or specify a tie-breaker for equal timestamps.

## Request path in plain words

1. [Startup](../../src/main.cpp#L19) creates the read repository and query service, connects them, and registers the HTTP routes. The server then listens for requests.
2. The [query route](../../src/api/query/routes.cpp#L30) accepts `GET /api/v1/audit-events`, reads the filters and page parameters, and asks the query service to find events. It converts the returned events and page details to JSON (`content`, `page`, `size`, `totalElements`).
3. The [query service](../../src/application/query/query_service.cpp#L8) supplies defaults of page 0 and size 50, caps size at 200, and passes the search to a read repository. A repository is the component responsible for getting records from storage. [Search validation](../../src/domain/search.cpp#L8) rejects a negative page or a size below 1; the service caps larger sizes before validation.
4. The PostgreSQL [read repository](../../src/persistence/query/postgres_read_repository.cpp#L10) builds the filters, selects from `audit_events`, and returns the page and total. It opens a read-only transaction per call; separate HTTP requests do not share a saved pagination boundary.

The **single best source to open** for the selection itself is [postgres_read_repository.cpp](../../src/persistence/query/postgres_read_repository.cpp#L10): the `SELECT`, `ORDER BY`, `LIMIT` and `OFFSET` are together there.

## What the code establishes

The selection sorts by `recorded_at DESC`, takes at most `size` rows (`LIMIT`), and skips `page × size` rows (`OFFSET`). Thus size 3 means page 0 skips 0 rows and page 1 skips 3. It sorts by time, not by ID; the displayed IDs do not prove their timestamp order. There is no secondary sort key for equal timestamps and no snapshot identifier in the query. The repository derives the total from a short nonempty page (or the first page), otherwise it runs a matching count query. A total can change between requests if matching data changes.

For illustration only: if page 0 sees `12, 11, 10, 9, ...` in that order, and a new matching event is inserted ahead of 12 before page 1, the new order starts `new, 12, 11, 10, 9, 8, ...`. Skipping three then returns `10, 9, 8`. The access log now shows a successful POST between the logged GETs, which supports this explanation. It does not show the new event timestamp or prove its position in the ordered list.

## Hypotheses and next probes

The supplied gateway access-log fragment shows these entries (times are as displayed, with no timezone or request duration supplied):

```text
10:02:14.120 GET  /api/v1/audit-events?page=0&size=3  200
10:02:15.890 POST /api/v1/audit-events                201
10:02:17.440 GET  /api/v1/audit-events?page=1&size=3  200
```

The earlier supplied responses report totals of 12 and 13 respectively. The log supports a successful POST between the logged GETs. In this branch, the [POST route](../../src/api/append/routes.cpp#L11) calls the append service before returning 201, and the [append repository](../../src/persistence/append/postgres_append_repository.cpp#L16) inserts an event. The log does not include the POST response body, the event's `recordedAt`, request completion times, or proof that the client environment runs this exact revision or uses the same database for all requests.

| Explanation | Evidence and next distinguishing check | Status |
| --- | --- | --- |
| The POST added an event ahead of ID 10, shifting the page boundary | The successful POST and total increase are consistent with this. Obtain the POST response body and the relevant rows' timestamps, plus visibility/order at both GETs. If the new event was visible for the second GET and sorted before ID 10, this supports the shift. Tied timestamps require actual observed ordering too. | Stronger lead, not yet confirmed: the log alone does not show the inserted row's position. |
| Alternative: tied recording times changed the order | The [SELECT](../../src/persistence/query/postgres_read_repository.cpp#L27) orders only by `recorded_at DESC`, without an ID tie-breaker. If ID 10 and other boundary events have equal timestamps, their order could differ between GETs, causing overlap even if the new event sorted after the boundary. Check timestamps and, if available, the actual order at both times. | Possible, not established. The POST and total increase still need explaining; this alternative concerns what caused the overlap. |

The access log shows the same reported page size and no other URL filters on these two GETs, so different URL filters are no longer a leading explanation for this pair. The reported response already contains ID 10 twice, so client display handling is not needed to explain that overlap. Neither observation establishes why ID 1 was absent after four pages; those later requests and responses remain unavailable.
User-supplied follow-up: the first two hypotheses were checked on the default empty library with test data, and everything was OK. The exact test setup, requests, results, and whether the checks reproduced an insertion or equal timestamps were not provided, so this does not establish what happened in the client's database. Check the client's relevant data, timestamps, and changes between requests to investigate further. This follow-up was reported by the user; it was not independently run or verified during this investigation.

## Probe: did a new event shift the boundary?

Ask for a database snapshot or reliable audit/ingestion records from each request time, preserving the relevant rows and their `id` and `recorded_at`. Compare the matching rows at 10:02:14.120 and 10:02:17.440, using the same filters as the requests (none were reported). Identify which row became visible between the two snapshots, then compare its `recorded_at` with the timestamps of IDs 12, 11 and 10. If a new matching row was visible only for the second request and sorts ahead of the original first three, that supports the shift explanation. If no such row appeared ahead of the boundary, investigate other changes and ordering ties. If timestamps tie, the recorded timestamps alone cannot establish their order: capture the actual ordered results at both times, if available.

For a controlled reproduction when permitted, start with known matching data, capture the full ordered list and page 0 with size 3, insert one matching event with a later `recorded_at` than the original page 0, then request page 1. Record raw responses and totals. A one-element increase in total and an overlap containing the last item from page 0 would demonstrate the mechanism. Run a control with adjacent reads and no intervening insert. This reproduces a mechanism; it does not independently prove what happened in the client's earlier requests. Current database state alone cannot reliably reconstruct the earlier visibility or the order of tied timestamps.

## Evidence log and handover

| Evidence | What it establishes |
| --- | --- |
| Client report, supplied in the task and follow-up | Page 0 at 10:02:14.120: IDs `12, 11, 10`, total 12; page 1 at 10:02:17.440: IDs `10, 9, 8`, total 13. Also reports missing ID 1 after four pages. Not independently verified. The total increase supports a change in matching rows, but does not establish their position. |
| Supplied gateway access-log image | A POST returned 201 between the logged GETs; it does not show its body, event timestamp, or request completion times. |
| [Query contract](../api/query-api.md) and the linked code above | The documented endpoint and the checked-out implementation's request path and offset selection. |
| Runtime requests and database state | Not inspected; no runtime conclusion or test result is claimed. |

Next, obtain the POST response and relevant historical database state or trainer-supplied evidence, plus the later page responses. Compare the event timestamps and whether matching rows appeared between page reads; also check a no-change traversal as a control. Ask the product owner whether a multi-page traversal must represent a stable view while new events arrive; the current contract does not decide that. No production changes were made.
