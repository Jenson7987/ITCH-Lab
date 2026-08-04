#include "itchlab/book/order_book.hpp"

#include "itchlab/core/errors.hpp"
#include "itchlab/core/sha256.hpp"
#include "itchlab/input/file_source.hpp"
#include "itchlab/input/framed_reader.hpp"
#include "itchlab/input/gzip_source.hpp"
#include "itchlab/itch/decoder.hpp"
#include "itchlab/itch/messages.hpp"

#include <catch2/catch_test_macros.hpp>
#include <nlohmann/json.hpp>

#include <array>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <variant>

namespace {

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

nlohmann::json side_state(const itchlab::OrderBook& book, const itchlab::Side side,
                          const std::vector<std::optional<itchlab::AggregatedLevel>>& levels) {
  auto state = nlohmann::json::array();
  for (const auto& aggregate : levels) {
    if (!aggregate) {
      state.push_back(nullptr);
      continue;
    }

    const auto level = book.level(side, aggregate->price4);
    REQUIRE(level.has_value());
    auto fifo = nlohmann::json::array();
    for (const auto& order : level->fifo_orders) {
      fifo.push_back({
          {"order_reference", order.order_reference},
          {"priority_sequence", order.priority_sequence},
          {"remaining", order.remaining},
      });
    }
    state.push_back({
        {"fifo", std::move(fifo)},
        {"price4", aggregate->price4},
        {"total_quantity", aggregate->total_quantity},
    });
  }
  return state;
}

nlohmann::json book_state(const itchlab::OrderBook& book, const itchlab::Frame& frame) {
  const auto top = book.top_levels(2);
  return {
      {"asks", side_state(book, itchlab::Side::sell, top.asks)},
      {"bids", side_state(book, itchlab::Side::buy, top.bids)},
      {"digest", itchlab::content_hash_to_hex(book.digest())},
      {"message_index", frame.message_index},
      {"order_count", book.order_count()},
      {"type", std::string(1, std::to_integer<char>(frame.payload.front()))},
  };
}

nlohmann::json trace_minimal_book(itchlab::ByteSource& source) {
  itchlab::FramedMessageReader reader{source};
  const itchlab::ItchDecoder decoder;
  itchlab::OrderBook book{1};
  auto states = nlohmann::json::array();

  while (true) {
    const auto frame_result = reader.next();
    REQUIRE_FALSE(frame_result.error.has_value());
    if (frame_result.end_of_file()) {
      break;
    }

    const auto& frame = *frame_result.frame;
    const auto decoded = decoder.decode(frame.payload);
    REQUIRE(decoded.valid());
    if (const auto* add = std::get_if<itchlab::AddOrder>(&*decoded.message)) {
      const auto result =
          book.apply(itchlab::BookAdd{frame.message_index, add->header.stock_locate,
                                      add->order_reference, add->side, add->shares, add->price4});
      REQUIRE(result.valid());
    } else if (const auto* delete_order = std::get_if<itchlab::OrderDelete>(&*decoded.message)) {
      const auto result = book.apply(itchlab::BookDelete{
          frame.message_index, delete_order->header.stock_locate, delete_order->order_reference});
      REQUIRE(result.valid());
    }
    REQUIRE(book.check_invariants().valid());
    states.push_back(book_state(book, frame));
  }
  return states;
}

nlohmann::json expected_minimal_book_trace() {
  std::ifstream input{repository_path("tests/golden/itch50/synthetic_minimal_book.json")};
  REQUIRE(input.good());
  nlohmann::json expected;
  input >> expected;
  REQUIRE(expected.at("fixture") == "synthetic_minimal.itch");
  REQUIRE(expected.at("schema_version") == 1);
  return expected.at("states");
}

struct InvalidLifecycleResult {
  itchlab::ErrorCode code;
  bool digest_unchanged;
  bool invariants_valid;
};

InvalidLifecycleResult replay_invalid_lifecycle(const std::string_view relative_path) {
  auto opened = itchlab::open_file_source(repository_path(relative_path));
  REQUIRE(opened.valid());
  itchlab::FramedMessageReader reader{*opened.source};
  const itchlab::ItchDecoder decoder;
  itchlab::OrderBook book{1};

  while (true) {
    const auto frame = reader.next();
    REQUIRE_FALSE(frame.error.has_value());
    REQUIRE_FALSE(frame.end_of_file());
    const auto decoded = decoder.decode(frame.frame->payload);
    REQUIRE(decoded.valid());

    std::optional<itchlab::BookMessage> message;
    if (const auto* add = std::get_if<itchlab::AddOrder>(&*decoded.message)) {
      message = itchlab::BookAdd{frame.frame->message_index,
                                 add->header.stock_locate,
                                 add->order_reference,
                                 add->side,
                                 add->shares,
                                 add->price4};
    } else if (const auto* delete_order = std::get_if<itchlab::OrderDelete>(&*decoded.message)) {
      message = itchlab::BookDelete{frame.frame->message_index, delete_order->header.stock_locate,
                                    delete_order->order_reference};
    }
    if (!message) {
      continue;
    }

    const auto digest_before = book.digest();
    const auto result = book.apply(*message);
    if (result.error) {
      return InvalidLifecycleResult{result.error->code, book.digest() == digest_before,
                                    book.check_invariants().valid()};
    }
    REQUIRE(result.valid());
  }
}

} // namespace

TEST_CASE("TASK-006 minimal S R A D fixture has the exact golden book state after every message",
          "[TASK-006][integration][book][golden]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  auto gzip =
      itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_minimal.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto expected = expected_minimal_book_trace();
  REQUIRE(trace_minimal_book(*plain.source) == expected);
  REQUIRE(trace_minimal_book(*gzip.source) == expected);
}

TEST_CASE("TASK-006 committed duplicate and missing-reference fixtures fail atomically",
          "[TASK-006][integration][book][atomic]") {
  struct Case {
    std::string_view path;
    itchlab::ErrorCode expected;
  };
  constexpr std::array cases{
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_duplicate_add.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_delete.itch",
           itchlab::ErrorCode::order_reference},
  };

  for (const auto& test_case : cases) {
    const auto result = replay_invalid_lifecycle(test_case.path);
    REQUIRE(result.code == test_case.expected);
    REQUIRE(result.digest_unchanged);
    REQUIRE(result.invariants_valid);
  }
}
