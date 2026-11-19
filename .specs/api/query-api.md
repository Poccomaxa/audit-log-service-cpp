# Query API Contract

## Endpoint

`GET /api/v1/audit-events`

## Filters

- `actor`: exact actor match.
- `from`: include events where `recordedAt >= from`.
- `to`: include events where `recordedAt < to`.
- `action`: exact action enum match.
- `outcome`: exact outcome enum match.
- `page`: zero-based page index, default `0`.
- `size`: page size, default `50`, maximum `200`.
- `snapshotId`: optional upper bound on the event identifier.

## Response

```json
{
  "content": [],
  "page": 0,
  "size": 50,
  "totalElements": 0,
  "snapshotId": 0
}
```

Content items include `id`, `recordedAt`, `actor`, `action`, `resourceType`, `resourceId`, `outcome`, `reason` and `metadata`.

## Snapshot Boundary

When `snapshotId` is omitted, the service resolves S as the global maximum event ID before filters, or 0 for an empty journal. When supplied, its signed 64-bit value is used unchanged, including zero. Both content and `totalElements` are restricted to `id <= S` under the same filters, and the response returns S as numeric `snapshotId`. A filter with no matches retains S with empty content and zero total.

Repeat S, filters and size across pages to keep traversal bounded against events appended after the first request completes. Each request that omits S resolves a new boundary. This ID ceiling is not an MVCC snapshot and does not guarantee behavior for arbitrary overlapping transaction commits.

## Sort Order

Results are sorted by `recordedAt` descending, then `id` descending. The unique id gives equal timestamps a defined relative order.

## Additive Filter Rule

Filters are additive and read-only; adding one must preserve results when its parameter is absent. A blank actor is dropped. Supplied action and outcome combine with AND.

Invalid enum values return `400 Bad Request` with a message naming the invalid parameter. Existing page/size defaults, size cap and validation remain unchanged.
