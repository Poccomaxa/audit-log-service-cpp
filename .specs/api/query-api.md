# Query API Contract

## Endpoint

`GET /api/v1/audit-events`

## Filters

- `actor`: exact actor match.
- `from`: include events where `recordedAt >= from`.
- `to`: include events where `recordedAt < to`.
- `action`: exact action enum match.
- `outcome`: exact outcome enum match.
- `snapshotId`: optional signed 64-bit integer; include events where `id <= snapshotId` in both content and `totalElements`.
- `page`: zero-based page index, default `0`.
- `size`: page size, default `50`, maximum `200`.

## Response

```json
{
  "content": [],
  "page": 0,
  "size": 50,
  "totalElements": 0
}
```

`content` items include:

- `id`
- `recordedAt`
- `actor`
- `action`
- `resourceType`
- `resourceId`
- `outcome`
- `reason`
- `metadata`

## Sort Order

Results are sorted by `recordedAt` descending, then by `id` descending for equal recording times.

To keep a traversal bounded while new events are appended, supply the latest known event ID as `snapshotId` on every page request. Events with larger IDs are excluded. Without `snapshotId`, each request remains unbounded. The response does not supply a snapshot boundary. This bound is not a database snapshot: an event assigned an ID at or below the boundary but committed later can still appear during the traversal.

## Additive Filter Rule

Filters are additive and read-only; adding a filter must never change results when its parameter is absent.

When both `action` and `outcome` are present, they combine with `AND`.

Invalid enum values return `400 Bad Request` with a message naming the invalid parameter.
Invalid or out-of-range `snapshotId` values return `400 Bad Request` with a message naming `snapshotId`. Empty or blank values are treated as absent, consistent with other numeric query parameters.
