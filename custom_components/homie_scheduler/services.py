"""Service handlers for Scheduler integration."""
from __future__ import annotations

import logging
import re
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_DURATION,
    ATTR_ENABLED,
    ATTR_ENTRY_ID,
    ATTR_ENTITY_ID,
    ATTR_ITEM_ID,
    ATTR_ITEMS,
    ATTR_TIME,
    ATTR_WEEKDAYS,
    CONF_ENTITY_MAX_RUNTIME,
    DOMAIN,
    ITEM_DURATION,
    ITEM_ENTITY_ID,
    ITEM_ENABLED,
    ITEM_ID,
    ITEM_SERVICE_START,
    ITEM_SERVICE_END,
    ITEM_TIME,
    ITEM_WEEKDAYS,
    SERVICE_ADD_ITEM,
    SERVICE_DELETE_ITEM,
    SERVICE_SET_ENABLED,
    SERVICE_SET_ITEMS,
    SERVICE_TOGGLE_ENABLED,
    SERVICE_UPDATE_ITEM,
    SERVICE_SET_ACTIVE_BUTTON,
    SERVICE_CLEAR_ACTIVE_BUTTON,
    STORAGE_ACTIVE_BUTTONS,
    WEEKDAYS,
)
from .scheduler import SchedulerCoordinator

_LOGGER = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r"^([0-1][0-9]|2[0-3]):([0-5][0-9])$")

# Service schemas
SERVICE_SET_ITEMS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ITEMS): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Optional(ITEM_ID): cv.string,
                        vol.Required(ITEM_ENTITY_ID): cv.entity_id,
                        vol.Optional(ITEM_ENABLED, default=True): cv.boolean,
                        vol.Required(ITEM_TIME): cv.string,
                        vol.Optional(ITEM_DURATION): cv.positive_int,  # Any positive integer, no hard limits
                        vol.Required(ITEM_WEEKDAYS): vol.All(
                            cv.ensure_list,
                            [vol.In(WEEKDAYS)],
                            vol.Length(min=1),
                        ),
                        vol.Required(ITEM_SERVICE_START): vol.Schema({
                            vol.Required("name"): cv.string,
                            vol.Required("value"): dict,
                        }),
                        vol.Optional(ITEM_SERVICE_END): vol.Schema({
                            vol.Required("name"): cv.string,
                            vol.Required("value"): dict,
                        }),
                    }
                )
            ],
        ),
    }
)

# Simplified schema - we'll validate manually in handler
SERVICE_ADD_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ITEM_ENTITY_ID): cv.string,
        vol.Optional(ITEM_ENABLED, default=True): cv.boolean,
        vol.Required(ITEM_TIME): cv.string,
        vol.Optional(ITEM_DURATION): vol.Any(cv.positive_int, cv.string),  # Optional, accepts both int and string
        vol.Required(ITEM_WEEKDAYS): cv.ensure_list,  # Just ensure it's a list
        vol.Required(ITEM_SERVICE_START): vol.Schema({
            vol.Required("name"): cv.string,
            vol.Required("value"): dict,
        }),
        vol.Optional(ITEM_SERVICE_END): vol.Schema({
            vol.Required("name"): cv.string,
            vol.Required("value"): dict,
        }),
    },
    extra=vol.ALLOW_EXTRA  # Allow extra keys (like item_id) to be ignored
)

SERVICE_UPDATE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ITEM_ID): cv.string,
        vol.Optional(ITEM_ENTITY_ID): cv.entity_id,
        vol.Optional(ITEM_ENABLED): cv.boolean,
        vol.Optional(ITEM_TIME): cv.string,
        vol.Optional(ITEM_DURATION): cv.positive_int,  # Any positive integer, no hard limits
        vol.Optional(ITEM_WEEKDAYS): vol.All(
            cv.ensure_list,
            [vol.In(WEEKDAYS)],
            vol.Length(min=1),
        ),
        vol.Optional(ITEM_SERVICE_START): vol.Schema({
            vol.Required("name"): cv.string,
            vol.Required("value"): dict,
        }),
        vol.Optional(ITEM_SERVICE_END): vol.Schema({
            vol.Required("name"): cv.string,
            vol.Required("value"): dict,
        }),
        vol.Optional("title"): cv.string,  # Optional title for the slot
    }
)

SERVICE_DELETE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ITEM_ID): cv.string,
    },
    extra=vol.ALLOW_EXTRA  # Allow extra keys to be ignored
)

SERVICE_SET_ENABLED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ENABLED): cv.boolean,
    }
)

SERVICE_TOGGLE_ENABLED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
    }
)


def validate_time(time_str: str) -> bool:
    """Validate time string format (HH:MM)."""
    return TIME_PATTERN.match(time_str) is not None


def validate_item(item: dict[str, Any]) -> bool:
    """Validate a schedule item."""
    # Validate entity_id (required for new items, optional for updates)
    if ITEM_ENTITY_ID in item:
        entity_id = item[ITEM_ENTITY_ID]
        if not entity_id or not isinstance(entity_id, str):
            raise vol.Invalid(f"Invalid entity_id: {entity_id}")
    
    # Validate service_start (required for new items)
    if ITEM_SERVICE_START in item:
        service_start = item[ITEM_SERVICE_START]
        if not isinstance(service_start, dict):
            raise vol.Invalid(f"service_start must be a dict with 'name' and 'value': {service_start}")
        if "name" not in service_start or "value" not in service_start:
            raise vol.Invalid(f"service_start must have 'name' and 'value' keys: {service_start}")
        if not isinstance(service_start["name"], str) or not service_start["name"]:
            raise vol.Invalid(f"service_start.name must be a non-empty string: {service_start.get('name')}")
        if not isinstance(service_start["value"], dict):
            raise vol.Invalid(f"service_start.value must be a dict: {service_start.get('value')}")
    
    # Validate service_end (required for new items)
    if ITEM_SERVICE_END in item:
        service_end = item[ITEM_SERVICE_END]
        if not isinstance(service_end, dict):
            raise vol.Invalid(f"service_end must be a dict with 'name' and 'value': {service_end}")
        if "name" not in service_end or "value" not in service_end:
            raise vol.Invalid(f"service_end must have 'name' and 'value' keys: {service_end}")
        if not isinstance(service_end["name"], str) or not service_end["name"]:
            raise vol.Invalid(f"service_end.name must be a non-empty string: {service_end.get('name')}")
        if not isinstance(service_end["value"], dict):
            raise vol.Invalid(f"service_end.value must be a dict: {service_end.get('value')}")
    
    # Validate time format
    if ITEM_TIME in item and not validate_time(item[ITEM_TIME]):
        raise vol.Invalid(f"Invalid time format: {item[ITEM_TIME]}")
    
    # Validate duration (must be positive integer, no hard limits)
    if ITEM_DURATION in item:
        duration = item[ITEM_DURATION]
        if not isinstance(duration, int) or duration <= 0:
            raise vol.Invalid(f"Invalid duration: {duration} (must be a positive integer)")
    
    # Validate weekdays
    if ITEM_WEEKDAYS in item:
        weekdays = item[ITEM_WEEKDAYS]
        if not weekdays:
            raise vol.Invalid("Weekdays cannot be empty")
        if not all(day in WEEKDAYS for day in weekdays):
            raise vol.Invalid(f"Invalid weekdays: {weekdays}")
    
    return True


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for scheduler."""
    _LOGGER.info("Setting up scheduler services...")

    async def handle_set_items(call: ServiceCall) -> None:
        """Handle set_items service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            items = call.data[ATTR_ITEMS]
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Validate items and set default duration if not provided
            entry = coordinator.entry
            default_duration = entry.options.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION) if entry else DEFAULT_DURATION
            
            for item in items:
                # Set default duration if not provided
                if ITEM_DURATION not in item:
                    item[ITEM_DURATION] = default_duration
                    _LOGGER.debug("Set default duration %d for item", default_duration)
                validate_item(item)
            
            # Generate IDs for items without one
            for item in items:
                if ITEM_ID not in item:
                    item[ITEM_ID] = str(uuid.uuid4())
            
            # Max items limit removed - no restriction on number of slots
            
            # Update options
            new_options = {**entry.options, "items": items}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Updated items for %s: %d items", entry_id, len(items))
        except Exception as e:
            _LOGGER.error("Error in handle_set_items: %s", e, exc_info=True)

    async def handle_add_item(call: ServiceCall) -> None:
        """Handle add_item service call."""
        _LOGGER.info("handle_add_item called with data: %s", call.data)
        try:
            # Extract and validate data manually (more flexible than schema)
            entry_id = call.data.get(ATTR_ENTRY_ID)
            if not entry_id:
                _LOGGER.error("Missing entry_id in service call")
                return
            
            entity_id = call.data.get(ITEM_ENTITY_ID)
            if not entity_id:
                _LOGGER.error("Missing entity_id in service call")
                return
            
            # Validate entity exists
            entity_state = hass.states.get(entity_id)
            if not entity_state:
                _LOGGER.error(
                    "Entity %s not found. Please check:\n"
                    "1. Entity ID is correct (e.g., 'climate.ac', 'switch.boiler')\n"
                    "2. Entity is registered in Home Assistant\n"
                    "3. For test entities, create them via Settings → Devices & Services → Helpers\n"
                    "4. For climate entities, you can use 'generic_thermostat' platform in configuration.yaml",
                    entity_id
                )
                return
            
            time_str = call.data.get(ITEM_TIME)
            if not time_str:
                _LOGGER.error("Missing time in service call")
                return
            
            # Get coordinator first (needed for default duration)
            if DOMAIN not in hass.data:
                _LOGGER.error("Domain %s not in hass.data", DOMAIN)
                return
            
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found in hass.data[%s]", entry_id, DOMAIN)
                _LOGGER.debug("Available entries: %s", list(hass.data.get(DOMAIN, {}).keys()))
                return
            
            # Get duration from call data (optional - if not provided, slot will run indefinitely)
            duration = None
            duration_raw = call.data.get(ITEM_DURATION)
            if duration_raw is not None and duration_raw != '':
                try:
                    duration = int(duration_raw)
                    if duration <= 0:
                        _LOGGER.error("Invalid duration: %d (must be a positive integer)", duration)
                        return
                except (ValueError, TypeError):
                    _LOGGER.error("Invalid duration: %s (must be an integer)", duration_raw)
                    return
            # If duration is None or empty, slot will run indefinitely (no service_end)
            
            # Convert weekdays to list of ints
            weekdays_raw = call.data.get(ITEM_WEEKDAYS)
            if not weekdays_raw:
                _LOGGER.error("Missing weekdays in service call")
                return
            
            try:
                weekdays = [int(d) for d in weekdays_raw] if isinstance(weekdays_raw, list) else [int(weekdays_raw)]
            except (ValueError, TypeError) as e:
                _LOGGER.error("Invalid weekdays: %s", weekdays_raw)
                return
            
            if not weekdays or not all(d in WEEKDAYS for d in weekdays):
                _LOGGER.error("Invalid weekdays values: %s (must be 0-6)", weekdays)
                return
            
            enabled = call.data.get(ITEM_ENABLED, True)
            
            # Get service_start (required) and service_end (optional)
            service_start = call.data.get(ITEM_SERVICE_START)
            if not service_start:
                _LOGGER.error("Missing service_start in service call")
                return
            
            service_end = call.data.get(ITEM_SERVICE_END)  # Optional - if not provided, slot will only turn on
            
            _LOGGER.info("Parsed data: entry_id=%s, entity_id=%s, time=%s, duration=%s, weekdays=%s, enabled=%s, service_start=%s, service_end=%s",
                        entry_id, entity_id, time_str, duration if duration is not None else 'None (indefinite)', weekdays, enabled, service_start, service_end)
            
            # Create new item
            new_item = {
                ITEM_ID: str(uuid.uuid4()),
                ITEM_ENTITY_ID: entity_id,
                ITEM_ENABLED: enabled,
                ITEM_TIME: time_str,
                ITEM_WEEKDAYS: weekdays,
                ITEM_SERVICE_START: service_start,
            }
            # Add duration only if provided
            if duration is not None:
                new_item[ITEM_DURATION] = duration
            # Add service_end only if provided
            if service_end:
                new_item[ITEM_SERVICE_END] = service_end
            # Add title if provided
            title = call.data.get("title")
            if title:
                new_item["title"] = title
            # Add temporary flag if provided (for button-created slots that shouldn't be visible in UI)
            temporary_flag = call.data.get("temporary", False)
            _LOGGER.info("Received temporary flag: %s (type: %s)", temporary_flag, type(temporary_flag))
            if temporary_flag:
                new_item["temporary"] = True
                _LOGGER.info("Created temporary slot (hidden from UI): %s", new_item.get(ITEM_ID))
            _LOGGER.info("New item created with keys: %s", list(new_item.keys()))
            if "temporary" in new_item:
                _LOGGER.info("New item temporary value: %s", new_item["temporary"])
            
            # Validate item
            try:
                validate_item(new_item)
            except vol.Invalid as e:
                _LOGGER.error("Invalid item data: %s", e)
                return  # Don't raise, just return
            
            # Get current items
            entry = coordinator.entry
            items = list(entry.options.get("items", []))
            
            # Max items limit removed - no restriction on number of slots per entity
            
            # Add item
            items.append(new_item)
            
            # Update options
            new_options = {**entry.options, "items": items}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Added item %s to %s", new_item[ITEM_ID], entry_id)
            
        except Exception as e:
            _LOGGER.error("Error in handle_add_item: %s", e, exc_info=True)
            # Don't re-raise - just log the error to prevent HA crash

    async def handle_update_item(call: ServiceCall) -> None:
        """Handle update_item service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            item_id = call.data[ATTR_ITEM_ID]
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Get current items
            entry = coordinator.entry
            items = list(entry.options.get("items", []))
            
            # Find item
            item_index = None
            for i, item in enumerate(items):
                if item.get(ITEM_ID) == item_id:
                    item_index = i
                    break
            
            if item_index is None:
                _LOGGER.error("Item %s not found in %s", item_id, entry_id)
                return
            
            # Update item
            updated_item = items[item_index].copy()
            old_item = items[item_index].copy()
            
            if ITEM_ENTITY_ID in call.data:
                updated_item[ITEM_ENTITY_ID] = call.data[ITEM_ENTITY_ID]
            if ITEM_ENABLED in call.data:
                updated_item[ITEM_ENABLED] = call.data[ITEM_ENABLED]
            if ITEM_TIME in call.data:
                updated_item[ITEM_TIME] = call.data[ITEM_TIME]
            if ITEM_DURATION in call.data:
                updated_item[ITEM_DURATION] = call.data[ITEM_DURATION]
            if ITEM_WEEKDAYS in call.data:
                updated_item[ITEM_WEEKDAYS] = call.data[ITEM_WEEKDAYS]
            if ITEM_SERVICE_START in call.data:
                updated_item[ITEM_SERVICE_START] = call.data[ITEM_SERVICE_START]
            if ITEM_SERVICE_END in call.data:
                updated_item[ITEM_SERVICE_END] = call.data[ITEM_SERVICE_END]
            if "title" in call.data:
                title = call.data["title"]
                if title:
                    updated_item["title"] = title
                elif "title" in updated_item:
                    # Remove title if empty string provided
                    del updated_item["title"]
            
            # Validate
            validate_item(updated_item)
            
            # Log changes for debugging
            changes = []
            if ITEM_ENTITY_ID in call.data and old_item.get(ITEM_ENTITY_ID) != updated_item[ITEM_ENTITY_ID]:
                changes.append(f"entity_id: {old_item.get(ITEM_ENTITY_ID)} → {updated_item[ITEM_ENTITY_ID]}")
            if ITEM_TIME in call.data and old_item.get(ITEM_TIME) != updated_item[ITEM_TIME]:
                changes.append(f"time: {old_item.get(ITEM_TIME)} → {updated_item[ITEM_TIME]}")
            if ITEM_DURATION in call.data and old_item.get(ITEM_DURATION) != updated_item[ITEM_DURATION]:
                changes.append(f"duration: {old_item.get(ITEM_DURATION)} → {updated_item[ITEM_DURATION]}")
            if ITEM_WEEKDAYS in call.data and old_item.get(ITEM_WEEKDAYS) != updated_item[ITEM_WEEKDAYS]:
                changes.append(f"weekdays: {old_item.get(ITEM_WEEKDAYS)} → {updated_item[ITEM_WEEKDAYS]}")
            if ITEM_ENABLED in call.data and old_item.get(ITEM_ENABLED) != updated_item[ITEM_ENABLED]:
                changes.append(f"enabled: {old_item.get(ITEM_ENABLED)} → {updated_item[ITEM_ENABLED]}")
            if ITEM_SERVICE_START in call.data:
                old_service_start = old_item.get(ITEM_SERVICE_START, {})
                new_service_start = updated_item.get(ITEM_SERVICE_START, {})
                old_hvac = old_service_start.get("value", {}).get("hvac_mode") if isinstance(old_service_start, dict) else None
                new_hvac = new_service_start.get("value", {}).get("hvac_mode") if isinstance(new_service_start, dict) else None
                if old_hvac != new_hvac:
                    changes.append(f"service_start.hvac_mode: {old_hvac} → {new_hvac}")
            if ITEM_SERVICE_END in call.data:
                old_service_end = old_item.get(ITEM_SERVICE_END, {})
                new_service_end = updated_item.get(ITEM_SERVICE_END, {})
                old_hvac = old_service_end.get("value", {}).get("hvac_mode") if isinstance(old_service_end, dict) else None
                new_hvac = new_service_end.get("value", {}).get("hvac_mode") if isinstance(new_service_end, dict) else None
                if old_hvac != new_hvac:
                    changes.append(f"service_end.hvac_mode: {old_hvac} → {new_hvac}")
            
            if changes:
                _LOGGER.info("Updating item %s: %s", item_id, ", ".join(changes))
            else:
                _LOGGER.debug("No changes detected for item %s", item_id)
            
            # Replace item
            items[item_index] = updated_item
            
            # Update options
            new_options = {**entry.options, "items": items}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Updated item %s in %s", item_id, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_update_item: %s", e, exc_info=True)

    async def handle_delete_item(call: ServiceCall) -> None:
        """Handle delete_item service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            item_id = call.data[ATTR_ITEM_ID]
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Get current items
            entry = coordinator.entry
            items = list(entry.options.get("items", []))
            
            # Filter out item
            new_items = [item for item in items if item.get(ITEM_ID) != item_id]
            
            if len(new_items) == len(items):
                _LOGGER.warning("Item %s not found in %s", item_id, entry_id)
                return
            
            # Update options
            new_options = {**entry.options, "items": new_items}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Deleted item %s from %s", item_id, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_delete_item: %s", e, exc_info=True)

    async def handle_set_enabled(call: ServiceCall) -> None:
        """Handle set_enabled service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            enabled = call.data[ATTR_ENABLED]
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Update options
            entry = coordinator.entry
            new_options = {**entry.options, "enabled": enabled}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Set enabled=%s for %s", enabled, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_set_enabled: %s", e, exc_info=True)

    async def handle_toggle_enabled(call: ServiceCall) -> None:
        """Handle toggle_enabled service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Toggle enabled
            entry = coordinator.entry
            current_enabled = entry.options.get("enabled", True)
            new_enabled = not current_enabled
            
            new_options = {**entry.options, "enabled": new_enabled}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Soft update: reload coordinator without full entry reload
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Toggled enabled to %s for %s", new_enabled, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_toggle_enabled: %s", e, exc_info=True)

    async def handle_set_active_button(call: ServiceCall) -> None:
        """Handle set_active_button service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            entity_id = call.data.get(ATTR_ENTITY_ID)
            button_id = call.data.get("button_id")
            timer_end_raw = call.data.get("timer_end")  # Unix timestamp in milliseconds
            duration = call.data.get(ATTR_DURATION)  # Duration in minutes
            
            if not entity_id or not button_id:
                _LOGGER.error("Missing entity_id or button_id in set_active_button call")
                return
            
            # Convert timer_end to int if it's a string
            try:
                timer_end = int(timer_end_raw) if timer_end_raw else None
            except (ValueError, TypeError):
                _LOGGER.error("Invalid timer_end: %s (must be a number)", timer_end_raw)
                return
            
            if not timer_end:
                _LOGGER.error("Missing timer_end in set_active_button call")
                return
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Get current active buttons
            entry = coordinator.entry
            active_buttons = dict(entry.options.get(STORAGE_ACTIVE_BUTTONS, {}))
            
            # Check max runtime limit for this entity
            entity_max_runtime = entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
            max_runtime_minutes = entity_max_runtime.get(entity_id, 0)
            
            if max_runtime_minutes > 0:
                # Calculate max allowed timer_end
                import time
                now_ms = int(time.time() * 1000)
                max_allowed_timer_end = now_ms + (max_runtime_minutes * 60 * 1000)
                
                # If requested timer_end exceeds max, cap it
                if timer_end > max_allowed_timer_end:
                    _LOGGER.warning(
                        "Timer end for %s exceeds max runtime of %d minutes. Capping to %d minutes.",
                        entity_id,
                        max_runtime_minutes,
                        max_runtime_minutes
                    )
                    timer_end = max_allowed_timer_end
                    duration = max_runtime_minutes  # Update duration to match capped time
            
            # Set active button for this entity
            active_buttons[entity_id] = {
                "button_id": button_id,
                "timer_end": timer_end,
                "duration": duration
            }
            
            # Update options
            new_options = {**entry.options, STORAGE_ACTIVE_BUTTONS: active_buttons}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Trigger sensor update
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Set active button %s for entity %s (entry %s)", button_id, entity_id, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_set_active_button: %s", e, exc_info=True)

    async def handle_clear_active_button(call: ServiceCall) -> None:
        """Handle clear_active_button service call."""
        try:
            entry_id = call.data[ATTR_ENTRY_ID]
            entity_id = call.data.get(ATTR_ENTITY_ID)
            
            if not entity_id:
                _LOGGER.error("Missing entity_id in clear_active_button call")
                return
            
            # Get coordinator
            coordinator = hass.data[DOMAIN].get(entry_id)
            if not coordinator:
                _LOGGER.error("Entry %s not found", entry_id)
                return
            
            # Get current active buttons
            entry = coordinator.entry
            active_buttons = dict(entry.options.get(STORAGE_ACTIVE_BUTTONS, {}))
            
            # Remove active button for this entity
            if entity_id in active_buttons:
                del active_buttons[entity_id]
            
            # Update options
            new_options = {**entry.options, STORAGE_ACTIVE_BUTTONS: active_buttons}
            hass.config_entries.async_update_entry(entry, options=new_options)
            
            # Trigger sensor update
            try:
                await coordinator.async_reload()
            except Exception as e:
                _LOGGER.error("Error reloading coordinator: %s", e)
            
            _LOGGER.info("Cleared active button for entity %s (entry %s)", entity_id, entry_id)
        except Exception as e:
            _LOGGER.error("Error in handle_clear_active_button: %s", e, exc_info=True)

    # Service schemas for active buttons
    SERVICE_SET_ACTIVE_BUTTON_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(ATTR_ENTITY_ID): cv.string,
            vol.Required("button_id"): cv.string,
            vol.Required("timer_end"): vol.Any(cv.positive_int, cv.string),  # Unix timestamp in ms
            vol.Required(ATTR_DURATION): cv.positive_int,  # Duration in minutes
        }
    )
    
    SERVICE_CLEAR_ACTIVE_BUTTON_SCHEMA = vol.Schema(
        {
            vol.Required(ATTR_ENTRY_ID): cv.string,
            vol.Required(ATTR_ENTITY_ID): cv.string,
        }
    )

    # Register services with error handling
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_ITEMS,
            handle_set_items,
            schema=SERVICE_SET_ITEMS_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_SET_ITEMS)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_SET_ITEMS, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_ADD_ITEM,
            handle_add_item,
            schema=SERVICE_ADD_ITEM_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_ADD_ITEM)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_ADD_ITEM, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_ITEM,
            handle_update_item,
            schema=SERVICE_UPDATE_ITEM_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_UPDATE_ITEM)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_UPDATE_ITEM, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_ITEM,
            handle_delete_item,
            schema=SERVICE_DELETE_ITEM_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_DELETE_ITEM)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_DELETE_ITEM, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_ENABLED,
            handle_set_enabled,
            schema=SERVICE_SET_ENABLED_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_SET_ENABLED)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_SET_ENABLED, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_TOGGLE_ENABLED,
            handle_toggle_enabled,
            schema=SERVICE_TOGGLE_ENABLED_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_TOGGLE_ENABLED)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_TOGGLE_ENABLED, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_ACTIVE_BUTTON,
            handle_set_active_button,
            schema=SERVICE_SET_ACTIVE_BUTTON_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_SET_ACTIVE_BUTTON)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_SET_ACTIVE_BUTTON, e)
    
    try:
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_ACTIVE_BUTTON,
            handle_clear_active_button,
            schema=SERVICE_CLEAR_ACTIVE_BUTTON_SCHEMA,
        )
        _LOGGER.debug("Registered service: %s.%s", DOMAIN, SERVICE_CLEAR_ACTIVE_BUTTON)
    except Exception as e:
        _LOGGER.error("Failed to register service %s.%s: %s", DOMAIN, SERVICE_CLEAR_ACTIVE_BUTTON, e)
    
    _LOGGER.info("scheduler services registration completed")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload services."""
    _LOGGER.debug("Unloading services")
    
    try:
        hass.services.async_remove(DOMAIN, SERVICE_SET_ITEMS)
        hass.services.async_remove(DOMAIN, SERVICE_ADD_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_SET_ENABLED)
        hass.services.async_remove(DOMAIN, SERVICE_TOGGLE_ENABLED)
        hass.services.async_remove(DOMAIN, SERVICE_SET_ACTIVE_BUTTON)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_ACTIVE_BUTTON)
    except Exception as e:
        _LOGGER.error("Error unloading services: %s", e)
