"""Deterministic per-symbol inventory-limit decisions for proposed passive quotes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from itchlab_research.errors import ErrorCode, SimulationError
from itchlab_research.simulation.accounting import MAX_INT64, MIN_INT64
from itchlab_research.simulation.order import MAX_UINT64


class RiskDecisionReason(StrEnum):
    """Stable reason for suppressing a proposed quote."""

    PROJECTED_INVENTORY_LIMIT = "projected_inventory_limit"


@dataclass(frozen=True, slots=True)
class InventoryRiskDecision:
    """One non-mutating decision over the quote's complete-fill inventory."""

    permitted: bool
    current_inventory: int
    projected_inventory: int
    inventory_limit: int
    risk_increasing: bool
    reason: RiskDecisionReason | None


def _fail(message: str) -> SimulationError:
    return SimulationError(ErrorCode.INVENTORY_LIMIT, message)


def _valid_int(value: object, *, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


class InventoryRiskLimit:
    """Evaluate proposed quote quantities against one absolute per-symbol share limit."""

    def __init__(self, inventory_limit: int) -> None:
        if not _valid_int(inventory_limit, minimum=1, maximum=MAX_INT64):
            raise _fail("Inventory limit is invalid.")
        self._inventory_limit = inventory_limit

    @property
    def inventory_limit(self) -> int:
        """Return the configured absolute share limit."""
        return self._inventory_limit

    def evaluate(
        self, *, current_inventory: int, side: int, quantity: int
    ) -> InventoryRiskDecision:
        """Return whether the quote remains safe if its full quantity fills."""
        if not _valid_int(
            current_inventory,
            minimum=-self._inventory_limit,
            maximum=self._inventory_limit,
        ):
            raise _fail("Current inventory is outside the configured limit.")
        if side not in {-1, 1} or isinstance(side, bool):
            raise _fail("Quote side must be +1 or -1.")
        if not _valid_int(quantity, minimum=1, maximum=MAX_UINT64):
            raise _fail("Quote quantity must be positive.")
        projected = current_inventory + side * quantity
        if not _valid_int(projected, minimum=MIN_INT64, maximum=MAX_INT64):
            raise _fail("Projected inventory overflowed signed 64-bit shares.")
        permitted = -self._inventory_limit <= projected <= self._inventory_limit
        return InventoryRiskDecision(
            permitted=permitted,
            current_inventory=current_inventory,
            projected_inventory=projected,
            inventory_limit=self._inventory_limit,
            risk_increasing=abs(projected) > abs(current_inventory),
            reason=None if permitted else RiskDecisionReason.PROJECTED_INVENTORY_LIMIT,
        )


__all__ = ["InventoryRiskDecision", "InventoryRiskLimit", "RiskDecisionReason"]
