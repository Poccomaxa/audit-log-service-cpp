#!/usr/bin/env python3
"""Run the service against an isolated PostgreSQL cluster and its real HTTP API."""

import contextlib
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "/api/v1/audit-events"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def checked(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


class Service:
    def __init__(self, db_url, psql):
        self.db_url = db_url
        self.psql = psql
        self.base_url = ""

    def sql(self, statement, check=True):
        result = subprocess.run(
            [self.psql, "-X", "-v", "ON_ERROR_STOP=1", "-At", "--dbname", self.db_url, "-c", statement],
            text=True,
            capture_output=True,
            env={**os.environ, "PGCONNECT_TIMEOUT": "5"},
        )
        if check and result.returncode:
            raise RuntimeError(f"psql failed:\n{result.stdout}{result.stderr}")
        return result

    def request(self, method="GET", params=None, body=None, path=ENDPOINT, raw=None, content_type="application/json"):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": content_type} if data is not None else {}
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            try:
                payload = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                payload = payload.decode(errors="replace")
            return response.status, response.headers, payload


@contextlib.contextmanager
def database(force_local=False):
    pg_config = os.environ.get("PG_CONFIG", shutil.which("pg_config"))
    if not pg_config:
        raise RuntimeError("PostgreSQL tools are required; put pg_config on PATH or set PG_CONFIG")
    bindir = Path(checked([pg_config, "--bindir"]))
    psql = str(bindir / "psql")
    external_url = None if force_local else os.environ.get("AUDIT_LOG_TEST_DB_URL")
    if external_url:
        service = Service(external_url, psql)
        relations = service.sql(
            "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname not in ('pg_catalog', 'information_schema') "
            "and n.nspname not like 'pg_toast%' and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')"
        ).stdout.strip()
        if relations != "0":
            raise RuntimeError("AUDIT_LOG_TEST_DB_URL must point to an empty throwaway database")
        yield service
        return

    with tempfile.TemporaryDirectory(prefix="audit-log-cpp-tests-", dir="/tmp") as temporary:
        directory = Path(temporary)
        data = directory / "data"
        log = directory / "postgres.log"
        checked([str(bindir / "initdb"), "-D", str(data), "-A", "trust", "-U", "auditlog", "-E", "UTF8", "--no-locale"])
        port = free_port()
        try:
            checked([
                str(bindir / "pg_ctl"), "-D", str(data), "-l", str(log), "-w", "-t", "30",
                "-o", f"-h 127.0.0.1 -p {port} -k {directory}", "start",
            ])
            yield Service(f"host=127.0.0.1 port={port} dbname=postgres user=auditlog", psql)
        finally:
            if (data / "postmaster.pid").exists():
                checked([str(bindir / "pg_ctl"), "-D", str(data), "-w", "-t", "30", "-m", "fast", "stop"])


@contextlib.contextmanager
def running_service(service, binary, migrations=ROOT / "db" / "migration"):
    port = free_port()
    service.base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "AUDIT_LOG_DB_URL": service.db_url,
        "AUDIT_LOG_MIGRATIONS": str(migrations),
        "AUDIT_LOG_HOST": "127.0.0.1",
        "AUDIT_LOG_PORT": str(port),
    }
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([str(binary)], cwd=ROOT, env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"service exited during startup ({process.returncode})")
                try:
                    if service.request()[0] == 200:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("service did not become ready within 30 seconds")
            yield
        except BaseException:
            log.seek(0)
            output = log.read().replace(service.db_url, "[test database]")
            if output:
                print(f"Service log:\n{output}", file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class ApiTest(unittest.TestCase):
    service = None

    def query(self, **params):
        status, _, page = self.service.request(params=params)
        self.assertEqual(status, 200, page)
        return page

    def assert_ids(self, expected, **params):
        page = self.query(**params)
        self.assertEqual([event["id"] for event in page["content"]], expected)
        self.assertEqual(page["totalElements"], len(expected))
        return page


class QueryTests(ApiTest):
    def test_seeded_first_page(self):
        page = self.query(page=0, size=5)
        self.assertEqual((page["page"], page["size"], page["totalElements"]), (0, 5, 12))
        self.assertEqual([event["id"] for event in page["content"]], [12, 11, 10, 9, 8])
        self.assertEqual(page["content"][0]["recordedAt"], "2026-04-20T09:55:00Z")

    def test_actor_exact_and_missing_matches(self):
        self.assert_ids([12, 8, 4, 1], actor="alice@example.com")
        self.assert_ids([7, 2], actor="bob@example.com")
        self.assert_ids([], actor="alice")
        self.assert_ids([], actor="ALICE@example.com")

    def test_time_window_includes_from_and_excludes_to(self):
        self.assert_ids([8, 7, 6, 5], **{"from": "2026-04-20T09:20:00Z", "to": "2026-04-20T09:40:00Z"})
        self.assert_ids([10, 9, 8, 7, 6], **{"from": "2026-04-20T09:25:00Z", "to": "2026-04-20T09:50:00Z"})
        self.assert_ids([12], **{"from": "2026-04-20T09:55:00Z"})
        self.assert_ids([1], to="2026-04-20T09:05:00Z")

    def test_action_outcome_and_combination(self):
        self.assert_ids([11, 10, 7, 5, 2], outcome="DENIED")
        self.assert_ids([12, 7, 3], action="EXPORT_DATA")
        self.assert_ids([7], action="EXPORT_DATA", outcome="DENIED")

    def test_filters_combine_with_actor_and_window(self):
        self.assert_ids([7], actor="bob@example.com", action="EXPORT_DATA", outcome="DENIED",
                        **{"from": "2026-04-20T09:20:00Z", "to": "2026-04-20T09:40:00Z"})

    def test_page_defaults_clamp_and_empty_page(self):
        page = self.query()
        self.assertEqual((page["page"], page["size"], page["totalElements"]), (0, 50, 12))
        self.assertEqual(self.query(size=999)["size"], 200)
        self.assertEqual([event["id"] for event in self.query(page=1, size=5)["content"]], [7, 6, 5, 4, 3])
        page = self.query(page=10, size=5)
        self.assertEqual(page["content"], [])
        self.assertEqual(page["totalElements"], 12)

    def test_blank_actor_is_ignored(self):
        for actor in ("", "  ", "\t"):
            with self.subTest(actor=actor):
                self.assert_ids(list(range(12, 0, -1)), actor=actor)

    def test_unknown_resource_filters_are_ignored(self):
        self.assert_ids(list(range(12, 0, -1)), resourceType="absent", resourceId="absent")

    def test_reversed_window_returns_empty(self):
        self.assert_ids([], **{"from": "2026-04-20T10:00:00Z", "to": "2026-04-20T09:00:00Z"})

    def test_enum_names_trim_only_java_boundary_whitespace(self):
        self.assert_ids([7], action=" \tEXPORT_DATA \n", outcome=" DENIED ")
        for value in ("0", "\u00a0EXPORT_DATA\u00a0"):
            with self.subTest(value=value):
                self.assertEqual(self.service.request(params={"action": value})[0], 400)

    def test_extended_year_query_bounds(self):
        for bounds in ({"from": "-0001-01-01T00:00:00Z"}, {"from": "0000-01-01T00:00:00Z"},
                       {"to": "+10000-01-01T00:00:00Z"}):
            with self.subTest(bounds=bounds):
                self.assert_ids(list(range(12, 0, -1)), **bounds)
        self.assert_ids([], to="0000-01-01T00:00:00Z")

    def test_invalid_enums_name_parameter(self):
        for parameter, value in (("action", "DOWNLOAD"), ("outcome", "MAYBE")):
            with self.subTest(parameter=parameter):
                status, _, error = self.service.request(params={parameter: value})
                self.assertEqual(status, 400, error)
                self.assertIn(f"Invalid value '{value}'", error["message"])
                self.assertIn(f"parameter '{parameter}'", error["message"])

    def test_spring_number_date_and_blank_query_coercions(self):
        for value, expected in ((" 1 ", 1), ("1 0", 10), ("0x10", 16), ("#10", 16), ("010", 10)):
            self.assertEqual(self.query(page=value)["page"], expected)
        for field in ("page", "size", "from", "to"):
            for blank in (" ", "\t", "\u2003"):
                page = self.query(**{field: blank})
                self.assertEqual((page["page"], page["size"], page["totalElements"]), (0, 50, 12))
        for field in ("action", "outcome"):
            self.assertEqual(self.query(**{field: " "})["totalElements"], 12)
        for invalid in ("+0x1", "+-1", "0x-1"):
            self.assertEqual(self.service.request(params={"page": invalid})[0], 400)
        self.assert_ids([12], **{"from": "1776678900000"})
        for date in ("Mon, 20 Apr 2026 09:25:00 GMT", "Mon, 20 Apr 2026 09:25 GmT"):
            self.assert_ids([12, 11, 10, 9, 8, 7, 6], **{"from": date})
        self.assertEqual(self.service.request(params={"from": "Tue, 20 Apr 2026 09:25:00 GMT"})[0], 400)

    def test_invalid_page_size_and_timestamp(self):
        for params in ({"page": -1}, {"size": 0}, {"size": -1}, {"page": "x"},
                       {"size": "1.5"}, {"page": "2147483648"}, {"from": "yesterday"},
                       {"to": "2026-04-20T09:00:00"}):
            with self.subTest(params=params):
                self.assertEqual(self.service.request(params=params)[0], 400)


class AppendTests(ApiTest):
    @staticmethod
    def event(**values):
        return {"actor": "auditor@example.test", "action": "LOGIN", "outcome": "ALLOWED", **values}

    def test_server_assigns_fields_and_persists(self):
        payload = self.event(resourceType="session", resourceId="s-demo", reason="demo login", metadata={"ip": "10.1.1.5"})
        before = datetime.datetime.now(datetime.timezone.utc)
        status, headers, event = self.service.request("POST", body=payload)
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual(status, 201, event)
        self.assertIsInstance(event["id"], int)
        self.assertGreater(event["id"], 12)
        self.assertTrue(headers["Location"].endswith(f"{ENDPOINT}/{event['id']}"))
        recorded = datetime.datetime.fromisoformat(event["recordedAt"].replace("Z", "+00:00"))
        self.assertLessEqual(before, recorded)
        self.assertLessEqual(recorded, after)
        for key, value in payload.items():
            self.assertEqual(event[key], value)
        self.assertEqual(self.service.sql(f"select count(*) from audit_events where id={event['id']}").stdout.strip(), "1")

    def test_client_server_fields_and_unknown_properties_are_ignored(self):
        status, _, event = self.service.request("POST", body=self.event(id=999999999, recordedAt="1900-01-01T00:00:00Z", extra="ignored"))
        self.assertEqual(status, 201, event)
        self.assertNotEqual(event["id"], 999999999)
        self.assertNotEqual(event["recordedAt"], "1900-01-01T00:00:00Z")
        self.assertNotIn("extra", event)

    def test_optional_fields_and_metadata_defaults(self):
        for metadata in ({}, {"metadata": None}):
            with self.subTest(metadata=metadata):
                status, _, event = self.service.request("POST", body=self.event(**metadata))
                self.assertEqual(status, 201, event)
                for field in ("resourceType", "resourceId", "reason"):
                    self.assertIsNone(event[field])
                self.assertEqual(event["metadata"], {})

    def test_required_values_and_enums(self):
        invalid = []
        for field in ("actor", "action", "outcome"):
            missing = self.event()
            del missing[field]
            invalid.extend((missing, self.event(**{field: None})))
        invalid.extend((self.event(actor=""), self.event(actor=" \t "),
                        self.event(action="DOWNLOAD"), self.event(outcome="MAYBE")))
        before = self.service.sql("select count(*) from audit_events").stdout
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.service.request("POST", body=payload)[0], 400)
        self.assertEqual(self.service.sql("select count(*) from audit_events").stdout, before)

    def test_json_media_types_are_matched_as_media_types(self):
        for media_type in ("Application/JSON", "application/json; charset=UTF-8",
                           "application/vnd.audit+json; charset=UTF-8"):
            with self.subTest(media_type=media_type):
                status, _, event = self.service.request("POST", body=self.event(), content_type=media_type)
                self.assertEqual(status, 201, event)
        for media_type in ("application/jsonx", "application/json-seq", "text/json", "application/x y+json"):
            with self.subTest(media_type=media_type):
                self.assertEqual(self.service.request("POST", body=self.event(), content_type=media_type)[0], 415)

    def test_body_enums_preserve_jackson_coercions(self):
        for action, outcome, expected in ((0, 0, ("LOGIN", "ALLOWED")),
                                          (6, 1, ("REVOKE_ROLE", "DENIED")),
                                          ("2", "1", ("EXPORT_DATA", "DENIED")),
                                          (" \tLOGIN\n", " ALLOWED ", ("LOGIN", "ALLOWED"))):
            with self.subTest(action=action, outcome=outcome):
                status, _, event = self.service.request("POST", body=self.event(action=action, outcome=outcome))
                self.assertEqual(status, 201, event)
                self.assertEqual((event["action"], event["outcome"]), expected)
        for action in (-1, 7, 2147483648, 18446744073709551615, 0.0, True, "00", "+0", "0x0", "0.0", "0junk"):
            with self.subTest(action=action):
                self.assertEqual(self.service.request("POST", body=self.event(action=action))[0], 400)
        self.assertEqual(self.service.request("POST", body=self.event(outcome=2))[0], 400)

    def test_structured_metadata_and_wrong_body_types_are_rejected(self):
        invalid = (self.event(metadata=[]), self.event(metadata="text"),
                   self.event(metadata={"nested": {"value": "x"}}), self.event(metadata={"array": []}),
                   self.event(actor=[]), self.event(resourceType={}), [], "text")
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.service.request("POST", body=payload)[0], 400)
        self.assertEqual(self.service.request("POST", raw=b'{"actor":')[0], 400)

    def test_string_values_are_preserved(self):
        payload = self.event(actor="  Аудитор@example.test  ", resourceType="", resourceId="s-'\\", reason="", metadata={"": "", "note": "привет"})
        status, _, event = self.service.request("POST", body=payload)
        self.assertEqual(status, 201, event)
        for key, value in payload.items():
            self.assertEqual(event[key], value)

    def test_http_has_no_update_or_delete_route(self):
        original = self.service.sql("select actor from audit_events where id=1").stdout
        for method in ("PUT", "PATCH", "DELETE"):
            for path in (ENDPOINT, ENDPOINT + "/1"):
                with self.subTest(method=method, path=path):
                    status, _, _ = self.service.request(method, body=self.event(actor="mutated"), path=path)
                    self.assertIn(status, (404, 405))
        self.assertEqual(self.service.sql("select actor from audit_events where id=1").stdout, original)

    def test_database_rejects_update_and_delete(self):
        for statement in ("update audit_events set actor='mutated' where id=1", "delete from audit_events where id=1"):
            with self.subTest(statement=statement):
                result = self.service.sql(statement, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("append-only", result.stderr)
        self.assertEqual(self.service.sql("select actor from audit_events where id=1").stdout.strip(), "alice@example.com")


class PaginationTest(ApiTest):
    @staticmethod
    def ids(page):
        return [event["id"] for event in page["content"]]

    def insert_row(self, actor, recorded_at):
        actor = actor.replace("'", "''")
        recorded_at = recorded_at.replace("'", "''")
        result = self.service.sql(
            "insert into audit_events (recorded_at, actor, action, resource_type, resource_id, outcome, reason) "
            f"values ('{recorded_at}'::timestamptz, '{actor}', 'LOGIN', 'session', 's-snapshot', "
            "'ALLOWED', 'snapshot boundary fixture') returning id"
        )
        return int(result.stdout.splitlines()[0])

    def append_event(self, actor):
        status, _, event = self.service.request("POST", body={
            "actor": actor, "action": "LOGIN", "resourceType": "session",
            "resourceId": "s-window-shift", "outcome": "ALLOWED",
            "reason": "appended between page requests",
        })
        self.assertEqual(status, 201, event)
        return event["id"]


class SnapshotResponseTraversalTests(PaginationTest):
    def test_global_snapshot_is_returned_and_pins_traversal(self):
        suffix = uuid.uuid4().hex
        actor_a = f"snapshot-a-{suffix}@example.test"
        actor_b = f"snapshot-b-{suffix}@example.test"
        original = [self.append_event(actor_a), self.append_event(actor_a)]
        boundary = self.append_event(actor_b)

        first = self.query(actor=actor_a, page=0, size=1)
        self.assertIn("snapshotId", first, "first page must return the effective snapshot boundary")
        snapshot = first["snapshotId"]
        self.assertIs(type(snapshot), int)
        self.assertEqual(snapshot, boundary)
        self.assertEqual(first["totalElements"], 2)

        appended = self.append_event(actor_a)
        self.assertGreater(appended, snapshot)
        second = self.query(actor=actor_a, page=1, size=1, snapshotId=snapshot)
        self.assertEqual(second["snapshotId"], snapshot)
        self.assertEqual(second["totalElements"], 2)
        traversed = self.ids(first) + self.ids(second)
        self.assertEqual(len(traversed), 2)
        self.assertEqual(len(set(traversed)), 2)
        self.assertEqual(set(traversed), set(original))
        self.assertNotIn(appended, traversed)


    def test_supplied_snapshot_zero_and_no_matches(self):
        actor = f"snapshot-extra-{uuid.uuid4().hex}@example.test"
        event_id = self.append_event(actor)
        for boundary in (0, event_id, event_id + 100):
            with self.subTest(boundary=boundary):
                page = self.query(actor=actor, snapshotId=boundary)
                self.assertEqual(page["snapshotId"], boundary)
                self.assertEqual(self.ids(page), [] if boundary == 0 else [event_id])
                self.assertEqual(page["totalElements"], 0 if boundary == 0 else 1)
        page = self.query(actor=f"missing-{uuid.uuid4().hex}@example.test")
        self.assertEqual(page["snapshotId"], event_id)
        self.assertEqual(page["content"], [])
        self.assertEqual(page["totalElements"], 0)

    def test_nonzero_start_page_uses_global_boundary(self):
        actor = f"snapshot-page-{uuid.uuid4().hex}@example.test"
        ids = [self.append_event(actor), self.append_event(actor)]
        boundary = self.append_event(f"snapshot-other-{uuid.uuid4().hex}@example.test")
        page = self.query(actor=actor, page=1, size=1)
        self.assertEqual(page["snapshotId"], boundary)
        self.assertEqual(page["totalElements"], 2)
        self.assertEqual(self.ids(page), [ids[0]])

    def test_empty_journal_returns_zero(self):
        with tempfile.TemporaryDirectory(prefix="audit-log-migrations-") as directory:
            migrations = Path(directory)
            for name in ("V1__create_audit_events.sql", "V2__append_only_guard.sql"):
                shutil.copy(ROOT / "db" / "migration" / name, migrations / name)
            with database(force_local=True) as service, running_service(service, self.binary, migrations):
                status, _, page = service.request()
                self.assertEqual(status, 200, page)
                self.assertEqual(page["snapshotId"], 0)
                self.assertEqual(page["content"], [])
                self.assertEqual(page["totalElements"], 0)


class PaginationWindowShiftTests(PaginationTest):
    def test_control_traversal_without_concurrent_append_has_no_overlap(self):
        first = self.query(page=0, size=3)
        second = self.query(page=1, size=3)
        self.assertEqual(second["totalElements"], first["totalElements"])
        self.assertTrue(set(self.ids(first)).isdisjoint(self.ids(second)))

    def test_append_between_page_requests_shifts_the_offset_window(self):
        first = self.query(page=0, size=3)
        appended = self.append_event("window-shift-probe@example.test")
        second = self.query(page=1, size=3)
        self.assertGreater(appended, self.ids(first)[0])
        self.assertEqual(second["totalElements"], first["totalElements"] + 1)
        self.assertEqual([event_id for event_id in self.ids(first) if event_id in self.ids(second)],
                         [self.ids(first)[-1]])
        all_ids = self.ids(self.query(page=0, size=200))
        self.assertTrue(set(self.ids(first)).issubset(all_ids))
        self.assertIn(appended, all_ids)

    def test_snapshot_id_pins_the_traversal_while_the_journal_grows(self):
        first = self.query(page=0, size=3)
        unbounded_total = first["totalElements"]
        # The client picks its own boundary; this stage does not report one yet.
        boundary = max(self.ids(first))
        bounded_total = self.query(page=0, size=3, snapshotId=boundary)["totalElements"]
        appended = self.append_event("snapshot-boundary-probe@example.test")
        self.assertGreater(appended, boundary)
        self.assertEqual(self.query(page=0, size=3)["totalElements"], unbounded_total + 1)
        traversed = []
        for page_number in range(200):
            page = self.query(page=page_number, size=3, snapshotId=boundary)
            self.assertEqual(page["totalElements"], bounded_total)
            if not page["content"]:
                break
            traversed.extend(self.ids(page))
        else:
            self.fail("Bounded traversal did not terminate within 200 pages")
        self.assertEqual(len(traversed), len(set(traversed)))
        self.assertNotIn(appended, traversed)
        self.assertTrue(all(event_id <= boundary for event_id in traversed))
        self.assertEqual(len(traversed), bounded_total)

    def test_equal_recorded_at_is_ordered_by_id_descending(self):
        actor = "tie-break-probe@example.test"
        first_older = self.insert_row(actor, "2026-01-05T10:00:00Z")
        first_newer = self.insert_row(actor, "2026-01-05T11:00:00Z")
        second_older = self.insert_row(actor, "2026-01-05T10:00:00Z")
        second_newer = self.insert_row(actor, "2026-01-05T11:00:00Z")
        self.assertEqual(self.ids(self.query(actor=actor, size=200)),
                         [second_newer, first_newer, second_older, first_older])



class DatabaseTimestampTests(ApiTest):
    def test_extended_year_rows_round_trip(self):
        actor = "timestamp-era@example.test"
        self.service.sql(
            "insert into audit_events (recorded_at, actor, action, outcome) values "
            f"('0002-01-01 00:00:00.123456+00 BC', '{actor}', 'LOGIN', 'ALLOWED'), "
            f"('0001-01-01 00:00:00.123456+00 BC', '{actor}', 'LOGIN', 'ALLOWED'), "
            f"('10000-01-01 00:00:00.123456+00', '{actor}', 'LOGIN', 'ALLOWED')"
        )
        page = self.query(actor=actor, **{"from": "-0001-01-01T00:00:00Z", "to": "+10001-01-01T00:00:00Z"})
        self.assertEqual(page["totalElements"], 3)
        self.assertEqual([event["recordedAt"] for event in page["content"]],
                         ["+10000-01-01T00:00:00.123456Z", "0000-01-01T00:00:00.123456Z",
                          "-0001-01-01T00:00:00.123456Z"])


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "audit-log-service").resolve()
    if not binary.is_file():
        raise RuntimeError(f"Build the service first; executable not found: {binary}")
    with database() as service, running_service(service, binary):
        ApiTest.service = service
        SnapshotResponseTraversalTests.binary = binary
        suite = unittest.TestSuite()
        for case in (QueryTests, SnapshotResponseTraversalTests, AppendTests, PaginationWindowShiftTests, DatabaseTimestampTests):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(f"Integration setup failed: {error}", file=sys.stderr)
        sys.exit(1)
