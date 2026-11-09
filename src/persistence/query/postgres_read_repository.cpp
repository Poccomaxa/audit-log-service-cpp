#include "audit/persistence/postgres_read_repository.hpp"

#include "../instant.hpp"
#include "../libpq.hpp"

#include <nlohmann/json.hpp>

namespace audit::persistence {

domain::EventPage PostgresReadRepository::find(const domain::Search& search) {
  detail::Connection connection(conninfo_);
  detail::Transaction transaction(connection, true);
  std::string where = " WHERE TRUE";
  detail::Parameters parameters;
  const auto filter = [&](const std::string& column, const std::string& op, std::string value,
                          const std::string& cast = "") {
    parameters.emplace_back(std::move(value));
    where += " AND " + column + op + "$" + std::to_string(parameters.size()) + cast;
  };
  if (search.actor) filter("actor", " = ", *search.actor);
  if (search.from) filter("recorded_at", " >= ", detail::to_pg_instant(*search.from), "::timestamptz");
  if (search.to) filter("recorded_at", " < ", detail::to_pg_instant(*search.to), "::timestamptz");
  if (search.action) filter("action", " = ", std::string(domain::to_string(*search.action)));
  if (search.outcome) filter("outcome", " = ", std::string(domain::to_string(*search.outcome)));
  if (search.snapshot_id) filter("id", " <= ", std::to_string(*search.snapshot_id), "::bigint");

  auto page_parameters = parameters;
  page_parameters.emplace_back(std::to_string(search.size));
  page_parameters.emplace_back(std::to_string(static_cast<std::int64_t>(search.page) * search.size));
  const auto rows = connection.exec(
      "SELECT id, to_char(recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\" BC'), "
      "actor, action, resource_type, resource_id, outcome, reason, metadata::text FROM audit_events" +
          where + " ORDER BY recorded_at DESC, id DESC LIMIT $" + std::to_string(parameters.size() + 1) +
          "::integer OFFSET $" + std::to_string(parameters.size() + 2) + "::bigint",
      page_parameters);

  std::vector<domain::AuditEvent> content;
  content.reserve(PQntuples(rows.get()));
  try {
    for (int row = 0; row < PQntuples(rows.get()); ++row) {
      const auto metadata = detail::nullable_text(rows, row, 8);
      content.emplace_back(std::stoll(PQgetvalue(rows.get(), row, 0)),
                           detail::from_pg_instant(PQgetvalue(rows.get(), row, 1)),
                           PQgetvalue(rows.get(), row, 2),
                           domain::parse_action(PQgetvalue(rows.get(), row, 3)),
                           detail::nullable_text(rows, row, 4), detail::nullable_text(rows, row, 5),
                           domain::parse_outcome(PQgetvalue(rows.get(), row, 6)),
                           detail::nullable_text(rows, row, 7),
                           metadata ? nlohmann::json::parse(*metadata).get<std::map<std::string, std::string>>()
                                    : std::map<std::string, std::string>{});
    }
  } catch (const std::exception&) {
    throw DatabaseError("Invalid stored audit event", "22000");
  }

  std::int64_t total;
  if (content.size() < static_cast<std::size_t>(search.size) && (search.page == 0 || !content.empty())) {
    total = static_cast<std::int64_t>(search.page) * search.size + static_cast<std::int64_t>(content.size());
  } else {
    const auto count = connection.exec("SELECT count(*) FROM audit_events" + where, parameters);
    total = std::stoll(PQgetvalue(count.get(), 0, 0));
  }
  transaction.commit();
  return {std::move(content), search.page, search.size, total};
}

}  // namespace audit::persistence
