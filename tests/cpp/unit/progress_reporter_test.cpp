#include "itchlab/replay/progress_reporter.hpp"

#include <catch2/catch_test_macros.hpp>

#include <chrono>
#include <string>
#include <vector>

namespace {

class ManualClock final : public itchlab::ProgressClock {
public:
  [[nodiscard]] TimePoint now() const noexcept override { return now_; }

  void advance(const std::chrono::steady_clock::duration duration) { now_ += duration; }

private:
  TimePoint now_{};
};

} // namespace

TEST_CASE("TASK-012 progress observes initial time and subsequent time/message rate limits",
          "[TASK-012][NFR-007][progress][rate]") {
  ManualClock clock;
  std::vector<itchlab::ReplayProgress> updates;
  itchlab::ProgressReporter reporter{
      clock, [&updates](const itchlab::ReplayProgress& update) { updates.push_back(update); }};

  reporter.observe(9'000'000, itchlab::SourceProgress{90, 900}, 12, 1);
  clock.advance(std::chrono::milliseconds{4'999});
  reporter.observe(9'999'999, itchlab::SourceProgress{99, 999}, 13, 1);
  REQUIRE(updates.empty());

  clock.advance(std::chrono::milliseconds{1});
  reporter.observe(10'000'000, itchlab::SourceProgress{100, 1'000}, 14, 2);
  REQUIRE(updates.size() == 1);
  REQUIRE(std::string{updates[0].stage} == "replay");
  REQUIRE(updates[0].messages == 10'000'000);
  REQUIRE(updates[0].source_bytes == 100);
  REQUIRE(updates[0].selected_events == 14);
  REQUIRE(updates[0].elapsed_ms == 5'000);
  REQUIRE(updates[0].error_count == 2);

  clock.advance(std::chrono::seconds{29});
  reporter.observe(19'999'999, itchlab::SourceProgress{199, 1'999}, 20, 2);
  REQUIRE(updates.size() == 1);

  reporter.observe(20'000'000, itchlab::SourceProgress{200, 2'000}, 21, 2);
  REQUIRE(updates.size() == 2);
  REQUIRE(updates[1].messages == 20'000'000);

  clock.advance(std::chrono::seconds{30});
  reporter.observe(20'000'001, itchlab::SourceProgress{201, 2'001}, 22, 3);
  REQUIRE(updates.size() == 3);
  REQUIRE(updates[2].elapsed_ms == 64'000);
  REQUIRE(updates[2].error_count == 3);
}
