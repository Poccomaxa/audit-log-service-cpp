#include "audit/domain/search.hpp"
#include "audit/domain/audit_event.hpp"
#include <stdexcept>
#include <utility>

namespace audit::domain {

Search::Search(std::optional<std::string> actor, std::optional<Instant> from, std::optional<Instant> to,
               std::optional<Action> action, std::optional<Outcome> outcome,
               std::int32_t page, std::int32_t size, std::optional<std::int64_t> snapshot_id)
    : actor(std::move(actor)), from(from), to(to), action(action), outcome(outcome),
      snapshot_id(snapshot_id), page(page), size(size) {
    if (this->actor && is_blank(*this->actor)) this->actor.reset();
    if (page < 0) throw std::invalid_argument("page must be greater than or equal to 0");
    if (size < 1 || size > 200) throw std::invalid_argument("size must be between 1 and 200");
}

}
