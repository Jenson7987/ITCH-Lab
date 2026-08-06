#include "itchlab/core/cancellation.hpp"
#include "itchlab/signal_adapter.hpp"

#include <catch2/catch_test_macros.hpp>

#include <csignal>

TEST_CASE("TASK-012 cancellation source is one-way and tokens are read-only views",
          "[TASK-012][E2E-004][cancellation][unit]") {
  itchlab::CancellationSource source;
  const auto token = source.token();
  REQUIRE_FALSE(token.is_cancellation_requested());

  source.request_cancellation();
  REQUIRE(token.is_cancellation_requested());
  REQUIRE(source.token().is_cancellation_requested());
  REQUIRE_FALSE(itchlab::CancellationToken{}.is_cancellation_requested());
}

TEST_CASE("TASK-012 signal adapter converts the first SIGINT into cancellation only",
          "[TASK-012][E2E-004][cancellation][signal]") {
  const itchlab::cli::SignalAdapter signals;
  REQUIRE(signals.installed());
  REQUIRE_FALSE(signals.token().is_cancellation_requested());

  REQUIRE(std::raise(SIGINT) == 0);
  REQUIRE(signals.token().is_cancellation_requested());
}
