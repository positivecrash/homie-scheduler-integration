"""Sensor platform for Scheduler integration."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import (
    ATTR_ACTIVE_ITEMS,
    ATTR_CONTROLLED_ENTITY,
    ATTR_ENTRY_ID,
    ATTR_ITEMS,
    ATTR_NEXT_RUN,
    CONF_NAME,
    DOMAIN,
    ITEM_ENTITY_ID,
    STORAGE_ACTIVE_BUTTONS,
)
from .scheduler import SchedulerCoordinator

_LOGGER = logging.getLogger(__name__)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Scheduler sensors."""
    coordinator: SchedulerCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        SchedulerNextRunSensor(coordinator, entry),
        SchedulerStatusSensor(coordinator, entry),
        SchedulerBridgeSensor(coordinator, entry),
    ], True)


class SchedulerNextRunSensor(SensorEntity):
    """Sensor showing next scheduled run time."""

    _attr_has_entity_name = True
    _attr_name = "Next Run"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: SchedulerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_next_run"
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
        # Only update if state actually changed to prevent infinite loops
        try:
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error updating bridge sensor state: %s", e)

    @property
    def native_value(self) -> datetime | None:
        """Return the state of the sensor."""
        return self._coordinator.next_transition

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:clock-outline"


class SchedulerStatusSensor(SensorEntity):
    """Sensor showing current status."""

    _attr_has_entity_name = True
    _attr_name = "Status"

    def __init__(
        self,
        coordinator: SchedulerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_status"
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
        # Only update if state actually changed to prevent infinite loops
        try:
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error updating bridge sensor state: %s", e)

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        # Get status for all entities or first active one
        items = self._coordinator.items
        if not items:
            return "Off • No schedules"
        
        # Check if any entity is on
        any_on = False
        for item in items:
            entity_id = item.get(ITEM_ENTITY_ID)
            if not entity_id:
                continue
            switch_state = self.hass.states.get(entity_id)
            if switch_state and switch_state.state == "on":
                any_on = True
                break
        
        status = "On" if any_on else "Off"
        
        next_run = self._coordinator.next_transition
        if next_run and self._coordinator.is_enabled:
            # Format next run
            now = dt_util.now()
            diff = next_run - now
            
            if diff.days == 0 and diff.seconds < 3600:
                # Less than an hour
                minutes = diff.seconds // 60
                status += f" • Next in {minutes}m"
            elif diff.days == 0:
                # Today
                status += f" • Next {next_run.strftime('%H:%M')}"
            elif diff.days == 1:
                # Tomorrow
                status += f" • Tomorrow {next_run.strftime('%H:%M')}"
            else:
                # Future day
                weekday = WEEKDAY_NAMES[next_run.weekday()]
                status += f" • {weekday} {next_run.strftime('%H:%M')}"
        elif not self._coordinator.is_enabled:
            status += " • Disabled"
        
        return status

    @property
    def icon(self) -> str:
        """Return the icon."""
        active_count = self._coordinator.get_active_items_count()
        if active_count > 0:
            return "mdi:calendar-clock"
        return "mdi:calendar-clock-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        return {
            ATTR_ACTIVE_ITEMS: self._coordinator.get_active_items_count(),
        }


class SchedulerBridgeSensor(SensorEntity):
    """Bridge sensor with controlled_entity and entry_id attributes for UI card."""

    _attr_has_entity_name = True
    _attr_name = "Scheduler Info"

    def __init__(
        self,
        coordinator: SchedulerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_bridge"
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
        # Only update if state actually changed to prevent infinite loops
        try:
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error updating bridge sensor state: %s", e)

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return "active" if self._coordinator.is_enabled else "inactive"

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:information-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for UI card discovery."""
        # Get all items (card will filter by entity_id)
        items = self._entry.options.get("items", [])
        
        # Get unique entity_ids
        entity_ids = list(set(item.get(ITEM_ENTITY_ID) for item in items if item.get(ITEM_ENTITY_ID)))
        
        # Get next run info from coordinator (earliest start time across all entities)
        next_start, next_run_duration = self._coordinator.get_next_start(None)
        next_run_iso = next_start.isoformat() if next_start else None
        
        # Get active buttons state
        active_buttons = self._entry.options.get(STORAGE_ACTIVE_BUTTONS, {})
        
        return {
            ATTR_ENTRY_ID: self._entry.entry_id,
            ATTR_ITEMS: items,
            "entity_ids": entity_ids,  # List of all controlled entities
            "integration": DOMAIN,
            "next_run": next_run_iso,  # ISO format datetime string
            "next_run_duration": next_run_duration,  # Duration in minutes
            "active_buttons": active_buttons,  # Active button states: {entity_id: {button_id, timer_end, duration}}
        }
