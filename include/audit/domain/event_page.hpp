#pragma once

#include "audit/domain/audit_event.hpp"
#include <cstdint>
#include <vector>

namespace audit::domain {

struct EventPage {
    std::vector<AuditEvent> content;
    std::int32_t page;
    std::int32_t size;
    std::int64_t total_elements;
    std::int64_t snapshot_id;
};

}
