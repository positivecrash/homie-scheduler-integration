"""Switch platform for Scheduler integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME, DOMAIN
from .scheduler import SchedulerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Scheduler switch."""
    coordinator: SchedulerCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([SchedulerSwitch(coordinator, entry)], True)


class SchedulerSwitch(SwitchEntity):
    """Switch to enable/disable the scheduler."""

    _attr_has_entity_name = True
    _attr_name = "Enabled"

    def __init__(
        self,
        coordinator: SchedulerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_schedule_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME, entry.title),
            manufacturer="Custom",
            model="Scheduler",
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._entry.options.get("enabled", True)

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:calendar-clock" if self.is_on else "mdi:calendar-remove"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.debug("Enabling scheduler for %s", self._entry.entry_id)
        
        new_options = {**self._entry.options, "enabled": True}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        # Soft update: reload coordinator without full entry reload
        try:
            await self._coordinator.async_reload()
        except Exception as e:
            _LOGGER.error("Error reloading coordinator: %s", e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.debug("Disabling scheduler for %s", self._entry.entry_id)
        
        new_options = {**self._entry.options, "enabled": False}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        # Soft update: reload coordinator without full entry reload
        try:
            await self._coordinator.async_reload()
        except Exception as e:
            _LOGGER.error("Error reloading coordinator: %s", e)
