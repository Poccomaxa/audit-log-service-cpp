#include "audit/application/append/append_service.hpp"
#include "audit/application/query/query_service.hpp"
#include <iostream>
#include <stdexcept>
#include <utility>

namespace {
using namespace audit::domain;

void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template<class Function>
void rejects(Function&& function) {
    try { function(); }
    catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("Expected invalid_argument");
}

struct AppendFake final : AppendRepository {
    int sequence_calls = 0;
    std::vector<AuditEvent> events;
    std::int64_t next_id() override { ++sequence_calls; return 42; }
    AuditEvent append(const AuditEvent& event) override { events.push_back(event); return event; }
};

struct ReadFake final : ReadRepository {
    std::optional<Search> last_search;
    EventPage find(const Search& search) override {
        last_search = search;
        return {{}, search.page, search.size, 0, 42};
    }
};

void append_uses_repository_id_and_server_clock() {
    AppendFake repository;
    const auto instant = parse_instant("2026-10-24T12:00:00.123456789Z");
    audit::application::append::AppendService service(repository, [instant] { return instant; });
    audit::application::append::AppendCommand command{
        "  alice  ", Action::ACCESS_PII, "customer", "123", Outcome::DENIED, "missing role", {{"source", "test"}}};
    const auto event = service.append(command);
    command.metadata["source"] = "changed";
    check(event.id == 42 && event.recorded_at == instant, "Server assigns id and time");
    check(event.actor == "  alice  " && event.metadata.at("source") == "test", "Preserve actor and copy metadata");
    check(repository.sequence_calls == 1 && repository.events.size() == 1, "Append once");
    command.actor = "\xE3\x80\x80";
    rejects([&] { service.append(command); });
    check(repository.sequence_calls == 2 && repository.events.size() == 1, "Invalid actor consumes sequence but is not appended");
}

void query_preserves_baseline_filter_and_pagination_behavior() {
    ReadFake repository;
    audit::application::query::QueryService service(repository);
    audit::application::query::Query query{};
    const auto page = service.find(query);
    check(page.page == 0 && page.size == 50, "Pagination defaults");
    check(page.snapshot_id == 42, "Propagate repository snapshot boundary");
    query.actor = "  alice  ";
    query.size = 999;
    query.from = parse_instant("2026-10-25T00:00:00Z");
    query.to = parse_instant("2026-10-24T00:00:00Z");
    service.find(query);
    check(repository.last_search->actor == query.actor && repository.last_search->size == 200,
          "Preserve nonblank actor and cap size");
    check(repository.last_search->from > repository.last_search->to, "Baseline accepts reversed time bounds");
    query.actor = "\t\xE2\x80\x83\xE3\x80\x80";
    service.find(query);
    check(!repository.last_search->actor, "Drop Java Unicode blank actor filter");
    query.actor = "\xC2\xA0";
    service.find(query);
    check(repository.last_search->actor.has_value(), "Nonbreaking space is not Java whitespace");
    query.size = 0;
    rejects([&] { service.find(query); });
    query.size = -1;
    rejects([&] { service.find(query); });
    query.size = 50;
    query.page = -1;
    rejects([&] { service.find(query); });
}

void instants_preserve_precision_offsets_and_extended_range() {
    const auto precise = parse_instant("2026-10-24T13:00:00.123456789+01:00");
    check(format_instant(precise) == "2026-10-24T12:00:00.123456789Z", "Normalize offset without losing nanos");
    for (const auto* value : {"0000-01-01T00:00:00Z", "1969-12-31T23:59:59.999999999Z",
                              "3000-12-31T23:59:59.001Z", "+10000-01-01T00:00:00Z",
                              "-0001-01-01T00:00:00Z", "-1000000000-01-01T00:00:00Z",
                              "+1000000000-12-31T23:59:59.999999999Z"}) {
        check(format_instant(parse_instant(value)) == value, "Round-trip dates across Java Instant range");
    }
    check(format_instant(parse_instant("2024-02-29T24:00:00Z")) == "2024-03-01T00:00:00Z", "ISO midnight normalization");
    check(format_instant(parse_instant("2016-12-31T23:59:60Z")) == "2016-12-31T23:59:59Z", "Java leap second normalization");
    for (const auto* value : {"2025-02-29T00:00:00Z", "2024-01-01T12:60:00Z", "2024-01-01T24:01:00Z",
                              "2024-01-01T00:00:00.1234567890Z", "2024-01-01T00:00:00+18:01",
                              "2024-01-01T00:00:00Zjunk", "2024-01-01T00:00:00", "999999999999999999999-01-01T00:00:00Z"}) {
        rejects([&] { parse_instant(value); });
    }
}

void enums_use_exact_java_values() {
    for (const auto* value : {"LOGIN", "LOGOUT", "EXPORT_DATA", "DELETE_RECORD", "ACCESS_PII", "GRANT_ROLE", "REVOKE_ROLE"}) {
        check(to_string(parse_action(value)) == value, "Action round-trip");
    }
    check(parse_outcome("ALLOWED") == Outcome::ALLOWED && parse_outcome("DENIED") == Outcome::DENIED, "Outcomes");
    rejects([] { parse_action("login"); });
    rejects([] { parse_outcome("UNKNOWN"); });
}
}

int main() {
    try {
        append_uses_repository_id_and_server_clock();
        query_preserves_baseline_filter_and_pagination_behavior();
        instants_preserve_precision_offsets_and_extended_range();
        enums_use_exact_java_values();
        std::cout << "Unit tests passed\n";
        return 0;
    } catch (const std::exception& exception) {
        std::cerr << exception.what() << '\n';
        return 1;
    }
}
