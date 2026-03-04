"""Config flow for Scheduler integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
    ENTITY_MAX_RUNTIME_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)

SUBENTRY_TYPE_ENTITY_MAX_RUNTIME = "entity_max_runtime"


class SchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scheduler."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=DEFAULT_NAME,
            data={CONF_NAME: DEFAULT_NAME},
            options={
                "items": [],
                "enabled": True,
            },
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {SUBENTRY_TYPE_ENTITY_MAX_RUNTIME: EntityMaxRuntimeSubentryFlow}


class EntityMaxRuntimeSubentryFlow(ConfigSubentryFlow):
    """Sub-entry flow: add or edit one entity + max run time (minutes)."""

    @staticmethod
    def _make_title(entity_id: str, minutes: int) -> str:
        return f"{entity_id} — {minutes} min"

    def _make_schema(
        self,
        default_entity: str | None = None,
        default_minutes: int = 30,
    ) -> vol.Schema:
        entity_key = (
            vol.Required("entity_id", default=default_entity)
            if default_entity
            else vol.Required("entity_id")
        )
        return vol.Schema(
            {
                entity_key: EntitySelector(
                    EntitySelectorConfig(domain=ENTITY_MAX_RUNTIME_DOMAINS)
                ),
                vol.Required("max_minutes", default=default_minutes): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=1440,
                        mode=NumberSelectorMode.BOX,
                        step=1,
                        unit_of_measurement="min",
                    )
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new entity + max run time."""
        if user_input is not None:
            entity_id = (user_input.get("entity_id") or "").strip()
            try:
                minutes = int(user_input["max_minutes"])
            except (TypeError, ValueError, KeyError):
                minutes = 0
            if entity_id and 1 <= minutes <= 1440:
                return self.async_create_entry(
                    title=self._make_title(entity_id, minutes),
                    data={"entity_id": entity_id, "max_minutes": minutes},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self._make_schema(),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an existing entity + max run time sub-entry."""
        subentry = self._get_reconfigure_subentry()
        saved_entity = str(subentry.data.get("entity_id") or "")
        saved_minutes = int(subentry.data.get("max_minutes", 30))

        if user_input is not None:
            entity_id = (user_input.get("entity_id") or "").strip()
            try:
                minutes = int(user_input["max_minutes"])
            except (TypeError, ValueError, KeyError):
                minutes = 0
            if entity_id and 1 <= minutes <= 1440:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=self._make_title(entity_id, minutes),
                    data={"entity_id": entity_id, "max_minutes": minutes},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._make_schema(
                default_entity=saved_entity or None,
                default_minutes=saved_minutes,
            ),
        )
