"""Declarative synthetic ITCH days and deliberately invalid order lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tests.fixtures.itch50_builder import MessageDefinition, MessageType, message


@dataclass(frozen=True, slots=True)
class SyntheticStreamDefinition:
    """One valid named stream that is committed in plain and gzip form."""

    name: str
    purpose: str
    messages: tuple[MessageDefinition, ...]


@dataclass(frozen=True, slots=True)
class InvalidLifecycleDefinition:
    """One correctly encoded stream whose final mutation is domain-invalid."""

    name: str
    expected_error_code: str
    offending_message_name: str
    messages: tuple[MessageDefinition, ...]


def _system_event(name: str, tracking: int, timestamp_ns: int, code: str) -> MessageDefinition:
    return message(
        name,
        "S",
        stock_locate=0,
        tracking_number=tracking,
        timestamp_ns=timestamp_ns,
        event_code=code,
    )


def _directory(
    name: str, tracking: int, timestamp_ns: int, stock_locate: int, stock: str
) -> MessageDefinition:
    return message(
        name,
        "R",
        stock_locate=stock_locate,
        tracking_number=tracking,
        timestamp_ns=timestamp_ns,
        stock=stock,
        market_category="Q",
        financial_status="N",
        round_lot_size=100,
        round_lots_only="N",
        issue_classification="C",
        issue_sub_type="",
        authenticity="P",
        short_sale_threshold_indicator="N",
        ipo_flag="N",
        luld_reference_price_tier="1",
        etp_flag="N",
        etp_leverage_factor=1,
        inverse_indicator="N",
    )


def _trading_action(
    name: str,
    tracking: int,
    timestamp_ns: int,
    stock_locate: int,
    stock: str,
    state: str,
    reason: str = "",
) -> MessageDefinition:
    return message(
        name,
        "H",
        stock_locate=stock_locate,
        tracking_number=tracking,
        timestamp_ns=timestamp_ns,
        stock=stock,
        trading_state=state,
        reserved="",
        reason=reason,
    )


def _add(
    name: str,
    tracking: int,
    timestamp_ns: int,
    stock_locate: int,
    order_reference: int,
    side: str,
    shares: int,
    stock: str,
    price4: int,
    attribution: str | None = None,
) -> MessageDefinition:
    message_type: MessageType = "F" if attribution is not None else "A"
    fields: dict[str, int | str] = {
        "stock_locate": stock_locate,
        "tracking_number": tracking,
        "timestamp_ns": timestamp_ns,
        "order_reference": order_reference,
        "side": side,
        "shares": shares,
        "stock": stock,
        "price4": price4,
    }
    if attribution is not None:
        fields["attribution"] = attribution
    return message(name, message_type, **fields)


MINIMAL_STREAM: Final = SyntheticStreamDefinition(
    name="synthetic_minimal",
    purpose="First vertical-slice stream containing only S, R, A and D messages.",
    messages=(
        _system_event("start_messages", 1, 1_000, "O"),
        _directory("directory_aapl", 2, 2_000, 1, "AAPL"),
        _system_event("start_system_hours", 3, 28_800_000_000_000, "S"),
        _system_event("start_market_hours", 4, 34_200_000_000_000, "Q"),
        _add(
            "add_aapl_bid",
            5,
            34_200_000_001_000,
            1,
            1_001,
            "B",
            100,
            "AAPL",
            1_000_000,
        ),
        message(
            "delete_aapl_bid",
            "D",
            stock_locate=1,
            tracking_number=6,
            timestamp_ns=34_200_000_002_000,
            order_reference=1_001,
        ),
        _system_event("end_market_hours", 7, 57_600_000_000_000, "M"),
        _system_event("end_system_hours", 8, 72_000_000_000_000, "E"),
        _system_event("end_messages", 9, 72_000_000_001_000, "C"),
    ),
)


MIXED_STREAM: Final = SyntheticStreamDefinition(
    name="synthetic_mixed",
    purpose=(
        "Three-symbol complete day containing every MVP type, partial and full mutations, "
        "trades, a halt/resume and close."
    ),
    messages=(
        _system_event("start_messages", 1, 1_000, "O"),
        _directory("directory_aapl", 2, 2_000, 1, "AAPL"),
        _directory("directory_msft", 3, 3_000, 2, "MSFT"),
        _directory("directory_amzn", 4, 4_000, 3, "AMZN"),
        _system_event("start_system_hours", 5, 28_800_000_000_000, "S"),
        _trading_action("aapl_trading", 6, 34_199_999_997_000, 1, "AAPL", "T"),
        _trading_action("msft_trading", 7, 34_199_999_998_000, 2, "MSFT", "T"),
        _trading_action("amzn_trading", 8, 34_199_999_999_000, 3, "AMZN", "T"),
        _system_event("start_market_hours", 9, 34_200_000_000_000, "Q"),
        _add(
            "add_aapl_bid",
            10,
            34_200_000_001_000,
            1,
            1_001,
            "B",
            100,
            "AAPL",
            1_000_000,
        ),
        _add(
            "add_aapl_attributed_ask",
            11,
            34_200_000_002_000,
            1,
            1_002,
            "S",
            200,
            "AAPL",
            1_001_000,
            "TEST",
        ),
        _add(
            "add_msft_bid",
            12,
            34_200_000_003_000,
            2,
            2_001,
            "B",
            150,
            "MSFT",
            2_000_000,
        ),
        _add(
            "add_amzn_ask",
            13,
            34_200_000_004_000,
            3,
            3_001,
            "S",
            80,
            "AMZN",
            3_000_000,
        ),
        message(
            "partially_execute_aapl_bid",
            "E",
            stock_locate=1,
            tracking_number=14,
            timestamp_ns=34_200_000_005_000,
            order_reference=1_001,
            executed_shares=40,
            match_number=5_001,
        ),
        message(
            "partially_cancel_aapl_bid",
            "X",
            stock_locate=1,
            tracking_number=15,
            timestamp_ns=34_200_000_006_000,
            order_reference=1_001,
            cancelled_shares=10,
        ),
        message(
            "delete_aapl_bid_remainder",
            "D",
            stock_locate=1,
            tracking_number=16,
            timestamp_ns=34_200_000_007_000,
            order_reference=1_001,
        ),
        message(
            "execute_aapl_ask_with_price",
            "C",
            stock_locate=1,
            tracking_number=17,
            timestamp_ns=34_200_000_008_000,
            order_reference=1_002,
            executed_shares=50,
            match_number=5_002,
            printable="Y",
            execution_price4=1_000_900,
        ),
        message(
            "replace_aapl_ask",
            "U",
            stock_locate=1,
            tracking_number=18,
            timestamp_ns=34_200_000_009_000,
            original_order_reference=1_002,
            new_order_reference=1_003,
            shares=125,
            price4=1_001_100,
        ),
        message(
            "fully_execute_replaced_aapl_ask",
            "E",
            stock_locate=1,
            tracking_number=19,
            timestamp_ns=34_200_000_010_000,
            order_reference=1_003,
            executed_shares=125,
            match_number=5_003,
        ),
        message(
            "delete_msft_bid",
            "D",
            stock_locate=2,
            tracking_number=20,
            timestamp_ns=34_200_000_011_000,
            order_reference=2_001,
        ),
        message(
            "replace_amzn_ask",
            "U",
            stock_locate=3,
            tracking_number=21,
            timestamp_ns=34_200_000_012_000,
            original_order_reference=3_001,
            new_order_reference=3_002,
            shares=60,
            price4=2_999_900,
        ),
        message(
            "cancel_replaced_amzn_ask",
            "X",
            stock_locate=3,
            tracking_number=22,
            timestamp_ns=34_200_000_013_000,
            order_reference=3_002,
            cancelled_shares=10,
        ),
        message(
            "fully_execute_amzn_ask",
            "E",
            stock_locate=3,
            tracking_number=23,
            timestamp_ns=34_200_000_014_000,
            order_reference=3_002,
            executed_shares=50,
            match_number=5_004,
        ),
        message(
            "non_cross_trade_aapl",
            "P",
            stock_locate=1,
            tracking_number=24,
            timestamp_ns=34_200_000_015_000,
            order_reference=0,
            side="B",
            shares=75,
            stock="AAPL",
            price4=1_000_500,
            match_number=6_001,
        ),
        message(
            "break_non_cross_trade",
            "B",
            stock_locate=1,
            tracking_number=25,
            timestamp_ns=34_200_000_016_000,
            match_number=6_001,
        ),
        message(
            "opening_cross_msft",
            "Q",
            stock_locate=2,
            tracking_number=26,
            timestamp_ns=34_200_000_017_000,
            shares=1_000,
            stock="MSFT",
            cross_price4=2_000_000,
            match_number=7_001,
            cross_type="O",
        ),
        _trading_action("halt_aapl", 27, 50_000_000_000_000, 1, "AAPL", "H", "LUDP"),
        _trading_action("resume_aapl", 28, 50_000_000_001_000, 1, "AAPL", "T"),
        _system_event("end_market_hours", 29, 57_600_000_000_000, "M"),
        _system_event("end_system_hours", 30, 72_000_000_000_000, "E"),
        _system_event("end_messages", 31, 72_000_000_001_000, "C"),
    ),
)


def _invalid_preamble() -> tuple[MessageDefinition, ...]:
    return (
        _system_event("start_messages", 1, 1_000, "O"),
        _directory("directory_aapl", 2, 2_000, 1, "AAPL"),
        _add("live_order", 3, 3_000, 1, 9_001, "B", 100, "AAPL", 1_000_000),
    )


INVALID_LIFECYCLES: Final[tuple[InvalidLifecycleDefinition, ...]] = (
    InvalidLifecycleDefinition(
        name="synthetic_invalid_duplicate_add",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="duplicate_live_reference",
        messages=_invalid_preamble()
        + (
            _add(
                "duplicate_live_reference",
                4,
                4_000,
                1,
                9_001,
                "S",
                50,
                "AAPL",
                1_001_000,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_missing_execute",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="execute_missing_reference",
        messages=_invalid_preamble()
        + (
            message(
                "execute_missing_reference",
                "E",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=99_001,
                executed_shares=1,
                match_number=80_001,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_missing_execute_with_price",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="execute_with_price_missing_reference",
        messages=_invalid_preamble()
        + (
            message(
                "execute_with_price_missing_reference",
                "C",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=99_001,
                executed_shares=1,
                match_number=80_002,
                printable="Y",
                execution_price4=1_000_000,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_missing_cancel",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="cancel_missing_reference",
        messages=_invalid_preamble()
        + (
            message(
                "cancel_missing_reference",
                "X",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=99_001,
                cancelled_shares=1,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_missing_delete",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="delete_missing_reference",
        messages=_invalid_preamble()
        + (
            message(
                "delete_missing_reference",
                "D",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=99_001,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_missing_replace",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="replace_missing_reference",
        messages=_invalid_preamble()
        + (
            message(
                "replace_missing_reference",
                "U",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                original_order_reference=99_001,
                new_order_reference=99_002,
                shares=100,
                price4=1_000_000,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_over_execute",
        expected_error_code="ERR_QUANTITY",
        offending_message_name="execute_above_remaining",
        messages=_invalid_preamble()
        + (
            message(
                "execute_above_remaining",
                "E",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=9_001,
                executed_shares=101,
                match_number=80_003,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_over_cancel",
        expected_error_code="ERR_QUANTITY",
        offending_message_name="cancel_above_remaining",
        messages=_invalid_preamble()
        + (
            message(
                "cancel_above_remaining",
                "X",
                stock_locate=1,
                tracking_number=4,
                timestamp_ns=4_000,
                order_reference=9_001,
                cancelled_shares=101,
            ),
        ),
    ),
    InvalidLifecycleDefinition(
        name="synthetic_invalid_replace_duplicate_new_reference",
        expected_error_code="ERR_ORDER_REFERENCE",
        offending_message_name="replace_to_live_reference",
        messages=_invalid_preamble()
        + (
            _add("second_live_order", 4, 4_000, 1, 9_002, "S", 50, "AAPL", 1_001_000),
            message(
                "replace_to_live_reference",
                "U",
                stock_locate=1,
                tracking_number=5,
                timestamp_ns=5_000,
                original_order_reference=9_001,
                new_order_reference=9_002,
                shares=100,
                price4=1_000_100,
            ),
        ),
    ),
)

VALID_STREAMS: Final = (MINIMAL_STREAM, MIXED_STREAM)


__all__ = [
    "INVALID_LIFECYCLES",
    "MINIMAL_STREAM",
    "MIXED_STREAM",
    "VALID_STREAMS",
    "InvalidLifecycleDefinition",
    "SyntheticStreamDefinition",
]
