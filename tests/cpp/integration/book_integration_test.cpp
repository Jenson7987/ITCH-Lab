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
#include <map>
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
      nlohmann::json order_state{
          {"order_reference", order.order_reference},
          {"priority_sequence", order.priority_sequence},
          {"remaining", order.remaining},
      };
      if (order.attribution) {
        order_state["attribution"] =
            std::string{order.attribution->data(), order.attribution->size()};
      }
      fifo.push_back(std::move(order_state));
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

std::optional<itchlab::BookMessage> to_book_message(const itchlab::MessageIndex message_index,
                                                    const itchlab::ItchMessage& decoded) {
  if (const auto* add = std::get_if<itchlab::AddOrder>(&decoded)) {
    return itchlab::BookAdd{message_index,        add->header.stock_locate,
                            add->order_reference, add->side,
                            add->shares,          add->price4,
                            std::nullopt};
  }
  if (const auto* add = std::get_if<itchlab::AddOrderWithAttribution>(&decoded)) {
    return itchlab::BookAdd{
        message_index, add->header.stock_locate, add->order_reference, add->side, add->shares,
        add->price4,   add->attribution};
  }
  if (const auto* execute = std::get_if<itchlab::OrderExecuted>(&decoded)) {
    return itchlab::BookExecute{message_index, execute->header.stock_locate,
                                execute->order_reference, execute->executed_shares};
  }
  if (const auto* execute = std::get_if<itchlab::OrderExecutedWithPrice>(&decoded)) {
    return itchlab::BookExecute{message_index, execute->header.stock_locate,
                                execute->order_reference, execute->executed_shares};
  }
  if (const auto* cancel = std::get_if<itchlab::OrderCancel>(&decoded)) {
    return itchlab::BookCancel{message_index, cancel->header.stock_locate, cancel->order_reference,
                               cancel->cancelled_shares};
  }
  if (const auto* delete_order = std::get_if<itchlab::OrderDelete>(&decoded)) {
    return itchlab::BookDelete{message_index, delete_order->header.stock_locate,
                               delete_order->order_reference};
  }
  if (const auto* replace = std::get_if<itchlab::OrderReplace>(&decoded)) {
    return itchlab::BookReplace{message_index,
                                replace->header.stock_locate,
                                replace->original_order_reference,
                                replace->new_order_reference,
                                replace->shares,
                                replace->price4};
  }
  return std::nullopt;
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
      const auto result = book.apply(itchlab::BookAdd{frame.message_index, add->header.stock_locate,
                                                      add->order_reference, add->side, add->shares,
                                                      add->price4, std::nullopt});
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

nlohmann::json trace_mixed_books(itchlab::ByteSource& source) {
  itchlab::FramedMessageReader reader{source};
  const itchlab::ItchDecoder decoder;
  std::map<itchlab::StockLocate, itchlab::OrderBook> books;
  std::map<itchlab::StockLocate, std::string> symbols;
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
    if (const auto* directory = std::get_if<itchlab::StockDirectory>(&*decoded.message)) {
      const auto locate = directory->header.stock_locate;
      books.try_emplace(locate, locate);
      symbols.emplace(locate, itchlab::trimmed_alpha(directory->stock));
      continue;
    }

    const auto message = to_book_message(frame.message_index, *decoded.message);
    if (!message) {
      continue;
    }
    const auto locate =
        std::visit([](const auto& book_message) { return book_message.stock_locate; }, *message);
    auto book = books.find(locate);
    REQUIRE(book != books.end());
    const auto applied = book->second.apply(*message);
    REQUIRE(applied.valid());
    REQUIRE(book->second.check_invariants().valid());

    auto state = book_state(book->second, frame);
    state["stock_locate"] = locate;
    state["symbol"] = symbols.at(locate);
    states.push_back(std::move(state));
  }
  return states;
}

nlohmann::json expected_mixed_book_trace() {
  std::ifstream input{repository_path("tests/golden/itch50/synthetic_mixed_book.json")};
  REQUIRE(input.good());
  nlohmann::json expected;
  input >> expected;
  REQUIRE(expected.at("fixture") == "synthetic_mixed.itch");
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

    const auto message = to_book_message(frame.frame->message_index, *decoded.message);
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

TEST_CASE("IT-003 full mixed lifecycle has exact per-mutation book states",
          "[TASK-010][IT-003][integration][book][golden]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_mixed.itch"));
  auto gzip = itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_mixed.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto expected = expected_mixed_book_trace();
  REQUIRE(expected.size() == 14);
  REQUIRE(trace_mixed_books(*plain.source) == expected);
  REQUIRE(trace_mixed_books(*gzip.source) == expected);
}

TEST_CASE("TASK-006 and TASK-010 committed invalid lifecycles fail atomically",
          "[TASK-006][TASK-010][integration][book][atomic]") {
  struct Case {
    std::string_view path;
    itchlab::ErrorCode expected;
  };
  constexpr std::array cases{
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_duplicate_add.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_delete.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_execute.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_execute_with_price.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_cancel.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_missing_replace.itch",
           itchlab::ErrorCode::order_reference},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_over_execute.itch",
           itchlab::ErrorCode::quantity},
      Case{"tests/fixtures/invalid_lifecycle/synthetic_invalid_over_cancel.itch",
           itchlab::ErrorCode::quantity},
      Case{
          "tests/fixtures/invalid_lifecycle/synthetic_invalid_replace_duplicate_new_reference.itch",
          itchlab::ErrorCode::order_reference},
  };

  for (const auto& test_case : cases) {
    const auto result = replay_invalid_lifecycle(test_case.path);
    REQUIRE(result.code == test_case.expected);
    REQUIRE(result.digest_unchanged);
    REQUIRE(result.invariants_valid);
  }
}
