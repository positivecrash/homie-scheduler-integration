"""Config flow for Scheduler integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_ENTITY_MAX_RUNTIME,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scheduler."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step - create entry immediately without asking for parameters."""
        # Create entry immediately with default values
        # Use a unique ID based on domain
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        
        return self.async_create_entry(
            title=DEFAULT_NAME,
            data={
                CONF_NAME: DEFAULT_NAME,
            },
            options={
                CONF_ENTITY_MAX_RUNTIME: {},  # Dict: {entity_id: max_minutes}
                "items": [],
                "enabled": True,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SchedulerOptionsFlow:
        """Get the options flow for this handler."""
        return SchedulerOptionsFlow()


class SchedulerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Scheduler."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options - single boiler configuration."""
        if user_input is not None:
            # Build entity_max_runtime dict from user input
            entity_max_runtime = {}
            
            entity_id = user_input.get("boiler_entity")
            max_time = user_input.get("boiler_time", 0)
            
            # Log for debugging
            _LOGGER.warning(f"[CONFIG_FLOW] Options save: entity_id={entity_id}, max_time={max_time}, type={type(entity_id)}")
            
            # Check entity first - if empty, always remove settings
            if entity_id is None or not isinstance(entity_id, str) or not entity_id.strip():
                # Entity is empty - remove all boiler settings
                entity_max_runtime = {}
                _LOGGER.warning(f"[CONFIG_FLOW] Entity is empty - removing boiler config")
            elif max_time <= 0:
                # Entity exists but time = 0 - also remove
                entity_max_runtime = {}
                _LOGGER.warning(f"[CONFIG_FLOW] Time is 0 - removing boiler config for {entity_id}")
            else:
                # Valid entity and time > 0 - save
                entity_max_runtime[entity_id] = max_time
                _LOGGER.warning(f"[CONFIG_FLOW] Saving boiler: {entity_id} -> {max_time} min")
            
            # Preserve items and enabled state
            current_options = self.config_entry.options
            new_options = dict(current_options)
            new_options[CONF_ENTITY_MAX_RUNTIME] = entity_max_runtime
            
            _LOGGER.warning(f"[CONFIG_FLOW] Final entity_max_runtime: {entity_max_runtime}")
            
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data=new_options)

        # Get fresh entry data when showing form
        entry = self.hass.config_entries.async_get_entry(self.config_entry.entry_id)
        current_options = entry.options if entry else self.config_entry.options
        
        # Get current settings
        entity_max_runtime = current_options.get(CONF_ENTITY_MAX_RUNTIME, {})
        
        _LOGGER.warning(f"[CONFIG_FLOW] Loading form: entity_max_runtime={entity_max_runtime}")
        
        # Get first boiler (we only support one now)
        boiler_entity = None
        boiler_time = 120
        if entity_max_runtime:
            boiler_entity = list(entity_max_runtime.keys())[0]
            boiler_time = entity_max_runtime[boiler_entity]
            _LOGGER.warning(f"[CONFIG_FLOW] Found boiler: {boiler_entity} -> {boiler_time} min")
        else:
            _LOGGER.warning("[CONFIG_FLOW] No boiler configured")
        
        # Build schema with single boiler
        schema_dict = {}
        
        # Use suggested_value instead of default to allow clearing
        if boiler_entity:
            schema_dict[vol.Optional("boiler_entity", description={"suggested_value": boiler_entity})] = EntitySelector(
                EntitySelectorConfig(domain="switch")
            )
        else:
            schema_dict[vol.Optional("boiler_entity")] = EntitySelector(
                EntitySelectorConfig(domain="switch")
            )
        
        schema_dict[vol.Optional("boiler_time", default=boiler_time, description={"suggested_value": boiler_time})] = NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=1440,
                mode=NumberSelectorMode.BOX,
                step=1,
                unit_of_measurement="minutes"
            )
        )
        
        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )
