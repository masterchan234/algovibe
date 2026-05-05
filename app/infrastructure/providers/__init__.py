"""
Exchange provider adapters.

Each adapter implements BaseExchangeAdapter to give the rest of Algovibe
a single, exchange-agnostic interface regardless of which venue is active.
"""

from .base_adapter import BaseExchangeAdapter
from .indodax_adapter import IndodaxAdapter

__all__ = ["BaseExchangeAdapter", "IndodaxAdapter"]
