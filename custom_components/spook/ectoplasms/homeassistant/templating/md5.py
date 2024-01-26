"""Spook - Not your homie."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from ....templating import AbstractSpookTemplateFunction

if TYPE_CHECKING:
    from collections.abc import Callable


class SpookTemplateFunction(AbstractSpookTemplateFunction):
    """Spook template function to generate md5 hashes."""

    name = "md5"

    requires_hass_object = False
    is_available_in_limited_environment = True
    is_filter = True
    is_global = True

    def _function(
        self,
        value: str,
    ) -> str:
        """Generate md5 hash from a string."""
        return hashlib.md5(value.encode()).hexdigest()  # noqa: S324

    def function(self) -> Callable[..., Any]:
        """Return the python method that runs this template function."""
        return self._function
