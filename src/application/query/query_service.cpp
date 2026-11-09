#include "audit/application/query/query_service.hpp"
#include <algorithm>

namespace audit::application::query {

QueryService::QueryService(domain::ReadRepository& repository) : repository_(repository) {}

domain::EventPage QueryService::find(const Query& query) {
    return repository_.find(domain::Search(query.actor, query.from, query.to, query.action,
                                           query.outcome, query.page.value_or(0),
                                           std::min(query.size.value_or(50), 200), query.snapshot_id));
}

}
