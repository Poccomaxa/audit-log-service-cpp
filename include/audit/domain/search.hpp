#pragma once

#include "audit/domain/action.hpp"
#include "audit/domain/instant.hpp"
#include "audit/domain/outcome.hpp"
#include <cstdint>
#include <optional>
#include <string>

namespace audit::domain {

struct Search {
    std::optional<std::string> actor;
    std::optional<Instant> from;
    std::optional<Instant> to;
    std::optional<Action> action;
    std::optional<Outcome> outcome;
    std::optional<std::int64_t> snapshot_id;
    std::int32_t page;
    std::int32_t size;

    Search(std::optional<std::string> actor, std::optional<Instant> from, std::optional<Instant> to,
           std::optional<Action> action, std::optional<Outcome> outcome,
           std::int32_t page, std::int32_t size, std::optional<std::int64_t> snapshot_id = std::nullopt);
};

}
