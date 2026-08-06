#include "itchlab/replay/error_policy.hpp"

#include <catch2/catch_test_macros.hpp>

TEST_CASE("TASK-012 error policy is stage-aware and strict mode stops at the first error",
          "[TASK-012][FR-006][error-policy][strict]") {
  const itchlab::ReplayValidationConfig config{itchlab::ValidationMode::strict, 0, 1};
  itchlab::ErrorPolicy policy{config};

  REQUIRE(policy.safely_skippable(itchlab::ReplayErrorOrigin::decoder,
                                  itchlab::ErrorCode::unknown_message));
  REQUIRE(policy.safely_skippable(itchlab::ReplayErrorOrigin::decoder,
                                  itchlab::ErrorCode::message_length));
  REQUIRE(
      policy.safely_skippable(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::timestamp));
  REQUIRE(
      policy.safely_skippable(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::invariant));
  REQUIRE(policy.safely_skippable(itchlab::ReplayErrorOrigin::book_apply,
                                  itchlab::ErrorCode::order_reference));
  REQUIRE(policy.safely_skippable(itchlab::ReplayErrorOrigin::book_apply,
                                  itchlab::ErrorCode::quantity));
  REQUIRE_FALSE(
      policy.safely_skippable(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::internal));
  REQUIRE_FALSE(policy.safely_skippable(itchlab::ReplayErrorOrigin::book_apply,
                                        itchlab::ErrorCode::invariant));

  const auto decision =
      policy.observe(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::unknown_message);
  REQUIRE(decision.disposition == itchlab::ErrorDisposition::stop);
  REQUIRE_FALSE(decision.counter_overflow);
  REQUIRE(policy.error_count() == 1);
  REQUIRE(policy.skipped_messages() == 0);
  REQUIRE_FALSE(policy.degraded());
}

TEST_CASE("TASK-012 permissive error policy enforces the exact skipped-message budget",
          "[TASK-012][FR-006][error-policy][permissive][budget]") {
  const itchlab::ReplayValidationConfig config{itchlab::ValidationMode::permissive, 2, 1};
  itchlab::ErrorPolicy policy{config};

  REQUIRE(policy.observe(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::unknown_message)
              .disposition == itchlab::ErrorDisposition::skip);
  REQUIRE(policy.observe(itchlab::ReplayErrorOrigin::book_apply, itchlab::ErrorCode::quantity)
              .disposition == itchlab::ErrorDisposition::skip);
  REQUIRE(policy.error_count() == 2);
  REQUIRE(policy.skipped_messages() == 2);
  REQUIRE(policy.degraded());

  const auto exceeded =
      policy.observe(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::message_length);
  REQUIRE(exceeded.disposition == itchlab::ErrorDisposition::budget_exceeded);
  REQUIRE(policy.error_count() == 3);
  REQUIRE(policy.skipped_messages() == 2);
  REQUIRE(policy.counts_by_code().at(itchlab::ErrorCode::unknown_message) == 1);
  REQUIRE(policy.counts_by_code().at(itchlab::ErrorCode::quantity) == 1);
  REQUIRE(policy.counts_by_code().at(itchlab::ErrorCode::message_length) == 1);
}

TEST_CASE("TASK-012 permissive policy never skips unsafe error origins",
          "[TASK-012][FR-006][error-policy][fatal]") {
  const itchlab::ReplayValidationConfig config{itchlab::ValidationMode::permissive, 10, 1};
  itchlab::ErrorPolicy policy{config};

  REQUIRE(policy.observe(itchlab::ReplayErrorOrigin::decoder, itchlab::ErrorCode::internal)
              .disposition == itchlab::ErrorDisposition::stop);
  REQUIRE(policy.observe(itchlab::ReplayErrorOrigin::book_apply, itchlab::ErrorCode::invariant)
              .disposition == itchlab::ErrorDisposition::stop);
  REQUIRE(policy.skipped_messages() == 0);
  REQUIRE_FALSE(policy.degraded());
}
