"""Sensor platform for Scheduler integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ENTRY_ID,
    ATTR_ITEMS,
    CONF_ENTITY_MAX_RUNTIME,
    CONF_NAME,
    DOMAIN,
    ITEM_ENTITY_ID,
    STORAGE_ACTIVE_BUTTONS,
)
from .scheduler import SchedulerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Scheduler sensors."""
    coordinator: SchedulerCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([SchedulerBridgeSensor(coordinator, entry)], True)


class SchedulerBridgeSensor(SensorEntity):
    """Bridge sensor with controlled_entity and entry_id attributes for UI card."""

    _attr_has_entity_name = True
    _attr_name = "Info"

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
        
        # Get next run info from coordinator (earliest start time across all entities) - for backward compat
        next_start, next_run_duration = self._coordinator.get_next_start(None)
        next_run_iso = next_start.isoformat() if next_start else None
        
        # Per-entity next run times (so each card shows its own entity's next run)
        entity_next_runs = {}
        for eid in entity_ids:
            eid_next_start, eid_duration = self._coordinator.get_next_start(eid)
            if eid_next_start:
                entity_next_runs[eid] = {
                    "next_run": eid_next_start.isoformat(),
                    "duration": eid_duration,
                }
        
        # Per-entity next transition (start OR end — actual next event for this entity)
        entity_next_transitions = {
            eid: dt.isoformat()
            for eid, dt in self._coordinator._entity_next_transition.items()
        }
        
        # Get active buttons state
        active_buttons = self._entry.options.get(STORAGE_ACTIVE_BUTTONS, {})
        
        # Get max_runtime turn-off times (from coordinator) so cards can show countdown when boiler runs under max_runtime
        max_runtime_turn_off_times = self._coordinator._max_runtime_turn_off_times
        
        # Entity max runtime limits (entity_id -> minutes) so cards can show countdown fallback from last_changed
        entity_max_runtime = self._entry.options.get(CONF_ENTITY_MAX_RUNTIME, {}) or {}
        # Last run per entity: {entity_id: {started_at, ended_at, duration_minutes}} for status card
        entity_last_runs = getattr(self._coordinator, "_entity_last_run", {}) or {}

        return {
            ATTR_ENTRY_ID: self._entry.entry_id,
            ATTR_ITEMS: items,
            "entity_ids": entity_ids,  # List of all controlled entities
            "integration": DOMAIN,
            "next_run": next_run_iso,  # ISO format datetime string (global, backward compat)
            "next_run_duration": next_run_duration,  # Duration in minutes (global)
            "entity_next_runs": entity_next_runs,  # Per-entity: {entity_id: {next_run: iso, duration: min}}
            "entity_next_transitions": entity_next_transitions,  # Per-entity: {entity_id: iso} — next event (start or end)
            "active_buttons": active_buttons,  # Active button states: {entity_id: {button_id, timer_end, duration}}
            "max_runtime_turn_off_times": max_runtime_turn_off_times,  # Expected turn-off from max_runtime: {entity_id: turn_off_time_ms}
            "entity_max_runtime": entity_max_runtime,  # Max minutes per entity: {entity_id: minutes} for fallback countdown
            "entity_last_runs": entity_last_runs,  # Per-entity last run: {entity_id: {started_at, ended_at, duration_minutes}}
        }
