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
#include <string>
#include <string_view>

namespace {

template <typename... Functions> struct Overloaded : Functions... {
  using Functions::operator()...;
};

std::filesystem::path repository_path(const std::string_view relative_path) {
  return std::filesystem::path{ITCHLAB_SOURCE_DIR} / relative_path;
}

std::string alpha_string(const auto& field) { return std::string{itchlab::trimmed_alpha(field)}; }

std::string alpha_char(const char value) { return std::string(1, value); }

nlohmann::json header_fields(const itchlab::MessageHeader& header) {
  return {
      {"stock_locate", header.stock_locate},
      {"tracking_number", header.tracking_number},
      {"timestamp_ns", header.timestamp_ns},
  };
}

nlohmann::json message_fields(const itchlab::ItchMessage& message) {
  return std::visit(
      Overloaded{
          [](const itchlab::SystemEvent& event) {
            auto fields = header_fields(event.header);
            fields["event_code"] = alpha_char(event.event_code);
            return fields;
          },
          [](const itchlab::StockDirectory& directory) {
            auto fields = header_fields(directory.header);
            fields.update({
                {"stock", alpha_string(directory.stock)},
                {"market_category", alpha_char(directory.market_category)},
                {"financial_status", alpha_char(directory.financial_status)},
                {"round_lot_size", directory.round_lot_size},
                {"round_lots_only", alpha_char(directory.round_lots_only)},
                {"issue_classification", alpha_char(directory.issue_classification)},
                {"issue_sub_type", alpha_string(directory.issue_sub_type)},
                {"authenticity", alpha_char(directory.authenticity)},
                {"short_sale_threshold_indicator",
                 alpha_char(directory.short_sale_threshold_indicator)},
                {"ipo_flag", alpha_char(directory.ipo_flag)},
                {"luld_reference_price_tier", alpha_char(directory.luld_reference_price_tier)},
                {"etp_flag", alpha_char(directory.etp_flag)},
                {"etp_leverage_factor", directory.etp_leverage_factor},
                {"inverse_indicator", alpha_char(directory.inverse_indicator)},
            });
            return fields;
          },
          [](const itchlab::AddOrder& order) {
            auto fields = header_fields(order.header);
            fields.update({
                {"order_reference", order.order_reference},
                {"side", alpha_char(order.side == itchlab::Side::buy ? 'B' : 'S')},
                {"shares", order.shares},
                {"stock", alpha_string(order.stock)},
                {"price4", order.price4},
            });
            return fields;
          },
          [](const itchlab::OrderDelete& order) {
            auto fields = header_fields(order.header);
            fields["order_reference"] = order.order_reference;
            return fields;
          },
      },
      message);
}

nlohmann::json decode_diagnostics(itchlab::ByteSource& source) {
  itchlab::FramedMessageReader reader{source};
  const itchlab::ItchDecoder decoder;
  auto diagnostics = nlohmann::json::array();
  while (true) {
    const auto frame_result = reader.next();
    REQUIRE_FALSE(frame_result.error.has_value());
    if (frame_result.end_of_file()) {
      break;
    }

    const auto& frame = *frame_result.frame;
    const auto decode_result = decoder.decode(frame.payload);
    REQUIRE(decode_result.valid());
    const auto source_type = std::to_integer<char>(frame.payload.front());
    diagnostics.push_back({
        {"fields", message_fields(*decode_result.message)},
        {"frame_offset", frame.source_offset},
        {"message_index", frame.message_index},
        {"payload_length", frame.payload.size()},
        {"payload_offset", frame.source_offset + 2},
        {"type", std::string(1, source_type)},
    });
  }
  return diagnostics;
}

nlohmann::json expected_minimal_diagnostics() {
  std::ifstream input{repository_path("tests/golden/itch50/synthetic_expected.json")};
  REQUIRE(input.good());
  nlohmann::json expected;
  input >> expected;

  for (const auto& stream : expected.at("streams")) {
    if (stream.at("name") == "synthetic_minimal") {
      nlohmann::json diagnostics = nlohmann::json::array();
      for (const auto& message : stream.at("messages")) {
        diagnostics.push_back({
            {"fields", message.at("fields")},
            {"frame_offset", message.at("frame_offset")},
            {"message_index", message.at("message_index")},
            {"payload_length", message.at("payload_length")},
            {"payload_offset", message.at("payload_offset")},
            {"type", message.at("type")},
        });
      }
      return diagnostics;
    }
  }
  FAIL("synthetic_minimal golden stream is missing");
  return nlohmann::json::array();
}

} // namespace

TEST_CASE("IT-001 uncompressed S R A D diagnostics match independent golden fields",
          "[TASK-005][IT-001][integration][decoder]") {
  auto opened = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  REQUIRE(opened.valid());
  REQUIRE(decode_diagnostics(*opened.source) == expected_minimal_diagnostics());
}

TEST_CASE("IT-002 gzip and plain S R A D streams decode to identical typed diagnostics",
          "[TASK-005][IT-002][integration][decoder][gzip]") {
  auto plain = itchlab::open_file_source(repository_path("tests/fixtures/synthetic_minimal.itch"));
  auto gzip =
      itchlab::open_gzip_source(repository_path("tests/fixtures/synthetic_minimal.itch.gz"));
  REQUIRE(plain.valid());
  REQUIRE(gzip.valid());

  const auto plain_diagnostics = decode_diagnostics(*plain.source);
  const auto gzip_diagnostics = decode_diagnostics(*gzip.source);
  REQUIRE(gzip_diagnostics == plain_diagnostics);
  REQUIRE(gzip_diagnostics == expected_minimal_diagnostics());
}

TEST_CASE("TASK-005 committed decoder corruptions return documented typed errors",
          "[TASK-005][integration][decoder][security]") {
  struct Case {
    std::string_view path;
    itchlab::ErrorCode expected;
  };
  constexpr std::array cases{
      Case{"tests/fixtures/corrupt/synthetic_corrupt_wrong_known_length.itch",
           itchlab::ErrorCode::message_length},
      Case{"tests/fixtures/corrupt/synthetic_corrupt_unknown_type.itch",
           itchlab::ErrorCode::unknown_message},
  };
  const itchlab::ItchDecoder decoder;

  for (const auto& test_case : cases) {
    auto opened = itchlab::open_file_source(repository_path(test_case.path));
    REQUIRE(opened.valid());
    itchlab::FramedMessageReader reader{*opened.source};
    const auto frame_result = reader.next();
    REQUIRE(frame_result.frame.has_value());
    REQUIRE(frame_result.frame->message_index == 0);
    REQUIRE(frame_result.frame->source_offset == 0);

    const auto decode_result = decoder.decode(frame_result.frame->payload);
    REQUIRE(decode_result.error->code == test_case.expected);
    REQUIRE_FALSE(decode_result.message.has_value());
    REQUIRE(reader.next().end_of_file());
  }
}
