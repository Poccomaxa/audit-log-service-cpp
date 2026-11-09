#pragma once

#include "audit/domain/search.hpp"

namespace audit::application::query {

struct Query {
    std::optional<std::string> actor;
    std::optional<domain::Instant> from;
    std::optional<domain::Instant> to;
    std::optional<domain::Action> action;
    std::optional<domain::Outcome> outcome;
    std::optional<std::int64_t> snapshot_id;
    std::optional<std::int32_t> page;
    std::optional<std::int32_t> size;
};

}
