"""The Scheduler integration."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_NAME,
    DOMAIN,
    PLATFORMS,
)
from .scheduler import SchedulerCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Scheduler from a config entry."""
    if not await async_migrate_entry(hass, entry):
        _LOGGER.error("Migration failed for entry %s", entry.entry_id)
        return False

    try:
        # Create coordinator
        coordinator = SchedulerCoordinator(hass, entry)
        
        # Store coordinator
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Setup services (only once)
        if len(hass.data[DOMAIN]) == 1:
            try:
                await async_setup_services(hass)
            except Exception as e:
                _LOGGER.error("Failed to setup services: %s", e, exc_info=True)
                # Continue anyway - services might already be registered

        # Create device entry
        try:
            device_registry = dr.async_get(hass)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.data.get(CONF_NAME, entry.title),
                manufacturer="Pinout",
                model="Scheduler",
            )
        except Exception as e:
            _LOGGER.error("Failed to create device entry: %s", e, exc_info=True)
            # Continue anyway - device entry is optional

        # Forward setup to platforms
        try:
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except Exception as e:
            _LOGGER.error("Failed to setup platforms: %s", e, exc_info=True)
            # Continue anyway - some platforms might have loaded

        # Start coordinator
        try:
            await coordinator.async_start()
        except Exception as e:
            _LOGGER.error("Failed to start coordinator: %s", e, exc_info=True)
            # Continue anyway - coordinator might still work partially

        # No entry.add_update_listener (OptionsUpdateListener): options changes (e.g. entity_max_runtime,
        # items, enabled) are applied only when services or config flow call config_entries.async_update_entry
        # and then coordinator.async_reload(). This is intentional: we avoid automatic full reload on every
        # options save; services (set_items, set_active_button, etc.) call async_reload() after updating
        # options so the coordinator picks up new values. See README "Options and config updates".

        return True
        
    except Exception as e:
        _LOGGER.error("Critical error during Scheduler setup: %s", e, exc_info=True)
        # Clean up if we stored coordinator
        if DOMAIN in hass.data and entry.entry_id in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN].pop(entry.entry_id, None)
        # Return False to indicate setup failed, but don't crash HA
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        # Stop coordinator if it exists
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            try:
                coordinator: SchedulerCoordinator = hass.data[DOMAIN][entry.entry_id]
                await coordinator.async_stop()
            except Exception as e:
                _LOGGER.error("Error stopping coordinator: %s", e, exc_info=True)

        # Unload platforms
        try:
            unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        except Exception as e:
            _LOGGER.error("Error unloading platforms: %s", e, exc_info=True)
            unload_ok = True  # Assume success to continue cleanup

        if unload_ok:
            if DOMAIN in hass.data:
                hass.data[DOMAIN].pop(entry.entry_id, None)

            # Unload services if this was the last entry
            if DOMAIN in hass.data and not hass.data[DOMAIN]:
                try:
                    await async_unload_services(hass)
                except Exception as e:
                    _LOGGER.error("Error unloading services: %s", e, exc_info=True)

        return unload_ok
        
    except Exception as e:
        _LOGGER.error("Critical error during Scheduler unload: %s", e, exc_info=True)
        # Try to cleanup anyway
        if DOMAIN in hass.data:
            hass.data[DOMAIN].pop(entry.entry_id, None)
        return True  # Return True to allow HA to proceed


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    try:
        await async_unload_entry(hass, entry)
        await async_setup_entry(hass, entry)
    except Exception as e:
        _LOGGER.error("Error reloading entry: %s", e, exc_info=True)
        # Don't re-raise - just log the error


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating entry from version %s", entry.version)

    if entry.version == 1:
        # Migrate from old structure (with switch_entity) to new structure
        from .const import CONF_SWITCH_ENTITY, CONF_NAME
        
        new_data = {}
        new_options = dict(entry.options)
        
        # Preserve name if exists
        if CONF_NAME in entry.data:
            new_data[CONF_NAME] = entry.data[CONF_NAME]
        elif entry.title:
            new_data[CONF_NAME] = entry.title
        
        # Migrate items: add entity_id to each item if it doesn't have one
        # If old entry had switch_entity, use it for all items
        old_switch_entity = entry.data.get(CONF_SWITCH_ENTITY)
        items = new_options.get("items", [])
        
        if old_switch_entity and items:
            from .const import ITEM_ENTITY_ID
            for item in items:
                if ITEM_ENTITY_ID not in item:
                    item[ITEM_ENTITY_ID] = old_switch_entity
            new_options["items"] = items
            _LOGGER.info("Migrated %d items with entity_id %s", len(items), old_switch_entity)
        
        # Update entry
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=2,
        )
        _LOGGER.info("Migrated entry %s from version 1 to 2", entry.entry_id)
        return True

    if entry.version == 2:
        # Rename entities: sensor.homie_schedule_scheduler_info -> sensor.homie_scheduler_info,
        # switch.homie_schedule_schedule_enabled -> switch.homie_scheduler_enabled
        try:
            registry = er.async_get(hass)
            for entity_entry in list(registry.entities.values()):
                if entity_entry.config_entry_id != entry.entry_id:
                    continue
                old_eid = entity_entry.entity_id
                new_eid = None
                if old_eid.startswith("sensor.homie_schedule_scheduler_info"):
                    new_eid = "sensor.homie_scheduler_info" + old_eid[len("sensor.homie_schedule_scheduler_info"):]
                elif old_eid.startswith("switch.homie_schedule_schedule_enabled"):
                    new_eid = "switch.homie_scheduler_enabled" + old_eid[len("switch.homie_schedule_schedule_enabled"):]
                if new_eid and new_eid != old_eid:
                    registry.async_update_entity(old_eid, new_entity_id=new_eid)
                    _LOGGER.info("Migrated entity %s -> %s", old_eid, new_eid)
            hass.config_entries.async_update_entry(entry, version=3)
            _LOGGER.info("Migrated entry %s from version 2 to 3 (entity renames)", entry.entry_id)
        except Exception as e:
            _LOGGER.warning("Migration to v3 (entity renames) failed: %s", e)
        return True

    return True
