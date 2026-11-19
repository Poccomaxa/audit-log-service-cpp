#include "audit/api/query/routes.hpp"
#include "audit/api/common/json.hpp"
#include "audit/api/common/parameters.hpp"
#include "audit/domain/action.hpp"
#include "audit/domain/outcome.hpp"
#include "audit/domain/instant.hpp"
#include <type_traits>
#include <stdexcept>

namespace audit::api::query {
namespace {
template<class T, class Parser>
std::optional<T> parameter(const httplib::Request& request, const char* name, Parser parser) {
  if (!request.has_param(name)) return std::nullopt;
  auto value = request.get_param_value(name);
  if (value.empty()) return std::nullopt;
  if constexpr (std::is_integral_v<T> || std::is_same_v<T, domain::Instant>) {
    if (domain::is_blank(value)) return std::nullopt;
  }
  if constexpr (std::is_same_v<T, domain::Action> || std::is_same_v<T, domain::Outcome>) {
    if (trim_parameter(value).empty()) return std::nullopt;
  }
  try { return parser(value); }
  catch (const std::invalid_argument&) {
    throw std::invalid_argument("Invalid value '" + value + "' for parameter '" + name + "'");
  }
}

}
void register_routes(httplib::Server& server, application::query::QueryService& service) {
  server.Get("/api/v1/audit-events", [&service](const auto& request, auto& response) {
    application::query::Query query;
    if (request.has_param("actor")) query.actor = request.get_param_value("actor");
    query.from = parameter<domain::Instant>(request, "from", parameter_instant);
    query.to = parameter<domain::Instant>(request, "to", parameter_instant);
    query.action = parameter<domain::Action>(request, "action", parameter_action);
    query.outcome = parameter<domain::Outcome>(request, "outcome", parameter_outcome);
    query.page = parameter<std::int32_t>(request, "page", parameter_integer<std::int32_t>);
    query.size = parameter<std::int32_t>(request, "size", parameter_integer<std::int32_t>);
    query.snapshot_id = parameter<std::int64_t>(request, "snapshotId", parameter_integer<std::int64_t>);
    const auto page = service.find(query);
    Json content = Json::array();
    for (const auto& event : page.content) content.push_back(event_json(event));
    response.set_content(Json{{"content", content}, {"page", page.page}, {"size", page.size},
                              {"totalElements", page.total_elements}, {"snapshotId", page.snapshot_id}}.dump(), "application/json");
  });
}
}
