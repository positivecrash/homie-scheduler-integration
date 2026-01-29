"""Scheduler coordinator for Scheduler integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
import homeassistant.util.dt as dt_util

from .const import (
    CONF_ENTITY_MAX_RUNTIME,
    ITEM_DURATION,
    ITEM_ENTITY_ID,
    ITEM_ENABLED,
    ITEM_TIME,
    ITEM_WEEKDAYS,
    ITEM_SERVICE_START,
    ITEM_SERVICE_END,
)

_LOGGER = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r"^([0-1][0-9]|2[0-3]):([0-5][0-9])$")


class SchedulerCoordinator:
    """Coordinator for scheduler logic.
    
    Manages multiple entities, each item can control a different switch entity.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.hass = hass
        self.entry = entry
        
        # State tracking per entity
        # Key: entity_id, Value: dict with state info
        self._entity_states: dict[str, dict[str, Any]] = {}
        
        # Global next transition (earliest across all entities)
        self._next_transition: datetime | None = None
        self._cancel_timer: CALLBACK_TYPE | None = None
        self._cancel_state_listeners: dict[str, CALLBACK_TYPE] = {}
        
        # Listeners for updates
        self._listeners: list[CALLBACK_TYPE] = []
        
        # Track last notification time to prevent spam
        self._last_notify_time: float = 0.0
        
        # Max runtime monitoring
        # Key: entity_id, Value: cancel function for auto-shutoff timer
        self._max_runtime_timers: dict[str, CALLBACK_TYPE] = {}

    @property
    def is_enabled(self) -> bool:
        """Return if scheduler is enabled."""
        return self.entry.options.get("enabled", True)

    @property
    def items(self) -> list[dict[str, Any]]:
        """Return schedule items."""
        return self.entry.options.get("items", [])

    @property
    def next_transition(self) -> datetime | None:
        """Return next transition time."""
        return self._next_transition

    def get_next_start(self, entity_id: str | None = None) -> tuple[datetime | None, int | None]:
        """Get next start time and duration for enabled items.
        
        Args:
            entity_id: If provided, only return next start for this entity.
                      If None, return earliest across all entities.
        
        Returns:
            Tuple of (next_start_datetime, duration_minutes) or (None, None)
        """
        if not self.is_enabled:
            return (None, None)
        
        now = dt_util.now()
        candidates = []
        
        # Find all next start times for enabled items
        for item in self.items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            # Filter by entity_id if specified
            item_entity = item.get(ITEM_ENTITY_ID)
            if entity_id and item_entity != entity_id:
                continue
            
            if not item_entity:
                continue  # Skip items without entity_id
            
            next_start = self._calculate_next_start(item, now)
            if next_start:
                duration = item.get(ITEM_DURATION, 30)
                candidates.append((next_start, duration, item_entity))
        
        if not candidates:
            return (None, None)
        
        # Return earliest start time with its duration
        next_start, duration, _ = min(candidates, key=lambda x: x[0])
        return (next_start, duration)

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Add a listener for state changes."""
        self._listeners.append(listener)
        
        @callback
        def remove_listener() -> None:
            self._listeners.remove(listener)
        
        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify all listeners of state change."""
        # Throttle notifications to prevent infinite loops (max 1 per second)
        import time
        now = time.time()
        if now - self._last_notify_time < 1.0:
            return  # Skip if notified less than 1 second ago
        self._last_notify_time = now
        
        try:
            for listener in self._listeners:
                try:
                    listener()
                except Exception as e:
                    _LOGGER.error("Error in listener callback: %s", e)
        except Exception as e:
            _LOGGER.error("Error notifying listeners: %s", e)

    async def async_start(self) -> None:
        """Start the scheduler."""
        _LOGGER.debug("Starting scheduler for %s", self.entry.entry_id)
        
        try:
            # Get all unique entity_ids from items
            entity_ids = set()
            for item in self.items:
                entity_id = item.get(ITEM_ENTITY_ID)
                if entity_id:
                    entity_ids.add(entity_id)
            
            # Listen to switch state changes for all entities
            if entity_ids:
                self._cancel_state_listeners = {}
                for entity_id in entity_ids:
                    try:
                        self._cancel_state_listeners[entity_id] = async_track_state_change_event(
                            self.hass,
                            [entity_id],
                            self._handle_switch_state_change,
                        )
                        # Initialize state tracking
                        self._entity_states[entity_id] = {
                            "scheduler_controlled_on": False,
                        }
                    except Exception as e:
                        _LOGGER.error("Error setting up listener for %s: %s", entity_id, e)
            
            # Initial schedule calculation
            await self._async_reschedule()
            
            # Check entities with max_runtime - if already ON, start monitoring
            entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
            for entity_id, max_minutes in entity_max_runtime.items():
                if max_minutes > 0:
                    state = self.hass.states.get(entity_id)
                    if state and state.state == STATE_ON:
                        _LOGGER.info(
                            "Entity %s is already ON at startup - starting max runtime monitor (%d min)",
                            entity_id,
                            max_minutes
                        )
                        self._start_max_runtime_monitor(entity_id)
        except Exception as e:
            _LOGGER.error("Error starting scheduler: %s", e, exc_info=True)
            # Don't re-raise - allow HA to continue

    async def async_stop(self) -> None:
        """Stop the scheduler."""
        _LOGGER.debug("Stopping scheduler for %s", self.entry.entry_id)
        
        # Cancel timer
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        
        # Cancel all max runtime timers
        for entity_id in list(self._max_runtime_timers.keys()):
            self._stop_max_runtime_monitor(entity_id)
        
        # Cancel all state listeners
        for cancel_listener in self._cancel_state_listeners.values():
            if cancel_listener:
                cancel_listener()
        self._cancel_state_listeners.clear()
        self._entity_states.clear()

    async def async_reload(self) -> None:
        """Reload scheduler after config change."""
        _LOGGER.debug("Reloading scheduler for %s", self.entry.entry_id)
        
        # Stop all existing max runtime monitors
        for entity_id in list(self._max_runtime_timers.keys()):
            self._stop_max_runtime_monitor(entity_id)
        
        # Restart monitors for entities that are currently ON
        entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
        for entity_id, max_minutes in entity_max_runtime.items():
            if max_minutes > 0:
                state = self.hass.states.get(entity_id)
                if state and state.state == STATE_ON:
                    _LOGGER.info(
                        "Reloading max runtime monitor for %s (%d min)",
                        entity_id,
                        max_minutes
                    )
                    self._start_max_runtime_monitor(entity_id)
        
        await self._async_reschedule()

    @callback
    def _handle_switch_state_change(self, event) -> None:
        """Handle switch state change."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if new_state is None or old_state is None:
            return
        
        entity_id = new_state.entity_id
        
        # Check if entity was turned on
        if new_state.state == STATE_ON and old_state.state != STATE_ON:
            # Clear any "blocked until" window when user/scheduler turns it on
            entity_state = self._entity_states.get(entity_id)
            if entity_state and entity_state.get("blocked_until"):
                entity_state["blocked_until"] = None
                self._entity_states[entity_id] = entity_state

            # Entity turned on - start max runtime monitor
            self._start_max_runtime_monitor(entity_id)
        
        # If switch was turned off (manually or by us), reset controlled flag if it's off
        if new_state.state != STATE_ON:
            # Entity turned off - stop max runtime monitor
            self._stop_max_runtime_monitor(entity_id)

            # If it was turned off while a schedule slot is currently active,
            # block re-enabling until the slot ends (prevents "stuck ON" and
            # enforces max_runtime priority over schedule).
            try:
                now = dt_util.now()
                items_for_entity = [
                    item for item in self.items
                    if item.get(ITEM_ENTITY_ID) == entity_id and item.get(ITEM_ENABLED, True)
                ]
                active_items = [item for item in items_for_entity if self._is_item_active(item, now)]
                if active_items:
                    end_times = [self._calculate_item_end(item, now) for item in active_items]
                    end_times = [dt for dt in end_times if dt is not None]
                    if end_times:
                        blocked_until = max(end_times)
                        entity_state = self._entity_states.get(entity_id, {"scheduler_controlled_on": False})
                        entity_state["blocked_until"] = blocked_until
                        self._entity_states[entity_id] = entity_state
                        _LOGGER.info(
                            "Entity %s turned off during active slot; blocking re-enable until %s",
                            entity_id,
                            blocked_until.isoformat(),
                        )
            except Exception as e:
                _LOGGER.debug("Failed to set blocked_until for %s: %s", entity_id, e)
            
            if entity_id in self._entity_states and self._entity_states[entity_id].get("scheduler_controlled_on"):
                _LOGGER.debug("Switch %s turned off, resetting controlled flag", entity_id)
            if entity_id in self._entity_states:
                self._entity_states[entity_id]["scheduler_controlled_on"] = False

    async def _async_reschedule(self) -> None:
        """Recalculate and reschedule next transition."""
        try:
            # Cancel existing timer
            if self._cancel_timer:
                self._cancel_timer()
                self._cancel_timer = None
            
            if not self.is_enabled:
                _LOGGER.debug("Scheduler disabled, no rescheduling")
                self._next_transition = None
                self._notify_listeners()
                return
            
            now = dt_util.now()
            
            # Read fresh items from entry (not cached)
            current_items = self.items  # This reads from entry.options.get("items", [])
            _LOGGER.debug(
                "Rescheduling with %d items: %s",
                len(current_items),
                [(item.get(ITEM_ENTITY_ID), item.get(ITEM_TIME), item.get(ITEM_WEEKDAYS), item.get(ITEM_ENABLED)) for item in current_items]
            )
            
            # Group items by entity_id
            items_by_entity: dict[str, list[dict[str, Any]]] = {}
            for item in current_items:
                entity_id = item.get(ITEM_ENTITY_ID)
                if not entity_id:
                    continue
                if entity_id not in items_by_entity:
                    items_by_entity[entity_id] = []
                items_by_entity[entity_id].append(item)
            
            # Update state listeners for new entities
            current_entity_ids = set(items_by_entity.keys())
            existing_entity_ids = set(self._cancel_state_listeners.keys())
            
            # Add listeners for new entities
            for entity_id in current_entity_ids - existing_entity_ids:
                self._cancel_state_listeners[entity_id] = async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._handle_switch_state_change,
                )
                self._entity_states[entity_id] = {
                    "scheduler_controlled_on": False,
                }
            
            # Remove listeners for entities no longer in use
            for entity_id in existing_entity_ids - current_entity_ids:
                if entity_id in self._cancel_state_listeners:
                    self._cancel_state_listeners[entity_id]()
                    del self._cancel_state_listeners[entity_id]
                if entity_id in self._entity_states:
                    del self._entity_states[entity_id]
            
            # Process each entity independently
            all_transitions = []
            for entity_id, entity_items in items_by_entity.items():
                # Calculate current state for this entity
                active_items = self._get_active_items_for_entity(entity_items, now)
                should_be_on = len(active_items) > 0
                
                if active_items:
                    _LOGGER.info("Entity %s has %d active items: %s", entity_id, len(active_items), 
                                [(item.get(ITEM_TIME), item.get(ITEM_DURATION)) for item in active_items])
                
                # Enforce switch state for this entity using service_start/service_end from items
                await self._async_enforce_switch_state(entity_id, should_be_on, entity_items)
                
                # Calculate next transition for this entity
                next_transition = self._calculate_next_transition_for_entity(entity_items, now, active_items)
                if next_transition:
                    _LOGGER.info("Entity %s next transition at %s (in %.1f seconds)", 
                               entity_id, next_transition.isoformat(), (next_transition - now).total_seconds())
                    all_transitions.append((next_transition, entity_id))
            
            # Find earliest transition across all entities
            if all_transitions:
                self._next_transition, _ = min(all_transitions, key=lambda x: x[0])
                delay = (self._next_transition - now).total_seconds()
                _LOGGER.debug(
                    "Next transition at %s (in %.1f seconds)",
                    self._next_transition.isoformat(),
                    delay,
                )
                
                # If delay is negative or very small (< 1 second), trigger immediately
                # This handles cases where transition time has already passed or is very soon
                if delay <= 0:
                    _LOGGER.debug("Transition time has passed, triggering immediately")
                    # Trigger immediately in next event loop iteration
                    self.hass.async_create_task(self._async_handle_transition(now))
                elif delay < 1.0:
                    _LOGGER.debug("Transition is very soon (%.2f seconds), scheduling for 1 second", delay)
                    # Schedule for at least 1 second to ensure proper execution
                    self._cancel_timer = async_call_later(
                        self.hass,
                        1.0,
                        self._async_handle_transition,
                    )
                else:
                    self._cancel_timer = async_call_later(
                        self.hass,
                        delay,
                        self._async_handle_transition,
                    )
            else:
                self._next_transition = None
                _LOGGER.debug("No next transition scheduled")
            
            # Notify listeners (entities)
            self._notify_listeners()
            
        except Exception as e:
            _LOGGER.error("Error in _async_reschedule: %s", e, exc_info=True)
            # Don't re-raise - allow HA to continue

    async def _async_enforce_switch_state(self, entity_id: str, should_be_on: bool, items: list[dict[str, Any]]) -> None:
        """Enforce the switch state based on schedule for a specific entity using service_start/service_end."""
        try:
            switch_state = self.hass.states.get(entity_id)
            if not switch_state:
                _LOGGER.warning(
                    "Entity %s not found. Please check:\n"
                    "1. Entity ID is correct\n"
                    "2. Entity is registered in Home Assistant\n"
                    "3. For test entities, create them via Settings → Devices & Services → Helpers",
                    entity_id
                )
                return
            
            # Determine domain from entity_id for state checking
            domain = entity_id.split(".")[0] if "." in entity_id else "switch"
            
            # For climate, check if mode is not "off", for others check STATE_ON
            if domain == "climate":
                is_on = switch_state.state != "off"
            else:
                is_on = switch_state.state == STATE_ON
            
            entity_state = self._entity_states.get(entity_id, {})
            scheduler_controlled = entity_state.get("scheduler_controlled_on", False)

            # If schedule wants it ON, but we are within a "blocked until" window,
            # do not re-enable until that time passes (manual off / max_runtime off wins).
            if should_be_on:
                blocked_until = entity_state.get("blocked_until")
                if blocked_until:
                    now = dt_util.now()
                    if now < blocked_until:
                        _LOGGER.debug(
                            "Not turning on %s (blocked_until=%s, now=%s)",
                            entity_id,
                            blocked_until.isoformat(),
                            now.isoformat(),
                        )
                        return
                    # Block expired, clear it
                    entity_state["blocked_until"] = None
                    self._entity_states[entity_id] = entity_state
            else:
                # When schedule is not active, clear any leftover block
                if entity_state.get("blocked_until"):
                    entity_state["blocked_until"] = None
                    self._entity_states[entity_id] = entity_state
            
            # Get service_start and service_end from any item (all items for same entity should have same services)
            service_start = None
            service_end = None
            if items:
                for item in items:
                    if ITEM_SERVICE_START in item:
                        service_start = item.get(ITEM_SERVICE_START)
                        service_end = item.get(ITEM_SERVICE_END)
                        break
            
            if not service_start:
                _LOGGER.warning("Missing service_start for %s, skipping", entity_id)
                return
            
            if should_be_on:
                if not is_on:
                    # Need to turn on - use service_start
                    _LOGGER.info("Calling service_start for %s (schedule active, turning on)", entity_id)
                    try:
                        # Parse service name (format: "domain.service_name")
                        service_name = service_start["name"]
                        if "." in service_name:
                            service_domain, service_method = service_name.split(".", 1)
                        else:
                            # Fallback: use domain from entity_id
                            service_domain = domain
                            service_method = service_name
                        
                        # Merge entity_id into service value if not present
                        service_value = dict(service_start["value"])
                        if "entity_id" not in service_value:
                            service_value["entity_id"] = entity_id
                        
                        await self.hass.services.async_call(
                            service_domain,
                            service_method,
                            service_value,
                            blocking=True,
                        )
                        entity_state["scheduler_controlled_on"] = True
                        self._entity_states[entity_id] = entity_state
                    except Exception as e:
                        _LOGGER.error("Failed to call service_start for %s: %s", entity_id, e)
                else:
                    # Already on, but schedule is active - mark as scheduler controlled
                    # This ensures service_end will be called when slot ends
                    if not scheduler_controlled:
                        _LOGGER.info("Marking %s as scheduler controlled (already on, schedule active)", entity_id)
                        entity_state["scheduler_controlled_on"] = True
                        self._entity_states[entity_id] = entity_state
                        _LOGGER.info("Entity %s state updated: scheduler_controlled_on=True", entity_id)
                    else:
                        _LOGGER.debug("%s already marked as scheduler controlled", entity_id)
                
            elif not should_be_on and is_on:
                # Need to turn off - check if we control it
                _LOGGER.info("Entity %s should be off (schedule inactive), is_on=%s, scheduler_controlled=%s", 
                           entity_id, is_on, scheduler_controlled)
                if scheduler_controlled:
                    # We control it - use service_end if provided
                    if service_end:
                        _LOGGER.info("Calling service_end for %s (schedule inactive, was controlled)", entity_id)
                        try:
                            # Parse service name (format: "domain.service_name")
                            service_name = service_end["name"]
                            if "." in service_name:
                                service_domain, service_method = service_name.split(".", 1)
                            else:
                                # Fallback: use domain from entity_id
                                service_domain = domain
                                service_method = service_name
                            
                            # Merge entity_id into service value if not present
                            service_value = dict(service_end["value"])
                            if "entity_id" not in service_value:
                                service_value["entity_id"] = entity_id
                            
                            await self.hass.services.async_call(
                                service_domain,
                                service_method,
                                service_value,
                                blocking=True,
                            )
                            entity_state["scheduler_controlled_on"] = False
                            self._entity_states[entity_id] = entity_state
                            _LOGGER.info("Successfully called service_end for %s", entity_id)
                        except Exception as e:
                            _LOGGER.error("Failed to call service_end for %s: %s", entity_id, e)
                    else:
                        # No service_end provided - just mark as not controlled, but don't turn off
                        _LOGGER.debug("No service_end for %s, marking as not controlled", entity_id)
                        entity_state["scheduler_controlled_on"] = False
                        self._entity_states[entity_id] = entity_state
                else:
                    # Not controlled by scheduler - don't turn off
                    _LOGGER.debug("%s is on but not scheduler controlled, skipping turn off", entity_id)
        except Exception as e:
            _LOGGER.error("Error enforcing switch state for %s: %s", entity_id, e, exc_info=True)
            # Don't re-raise - allow scheduler to continue

    async def _async_handle_transition(self, _now: datetime) -> None:
        """Handle scheduled transition."""
        _LOGGER.debug("Transition event triggered, rescheduling")
        await self._async_reschedule()

    def _get_active_items(self, now: datetime) -> list[dict[str, Any]]:
        """Get currently active schedule items across all entities."""
        active = []
        
        for item in self.items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            if self._is_item_active(item, now):
                active.append(item)
        
        return active
    
    def _get_active_items_for_entity(self, items: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
        """Get currently active schedule items for a specific entity."""
        active = []
        
        for item in items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            if self._is_item_active(item, now):
                active.append(item)
        
        return active

    def _is_item_active(self, item: dict[str, Any], now: datetime) -> bool:
        """Check if a schedule item is currently active."""
        time_str = item.get(ITEM_TIME)
        duration = item.get(ITEM_DURATION, 30)
        weekdays = item.get(ITEM_WEEKDAYS, [])
        
        if not time_str or not weekdays:
            return False
        
        # Parse time
        match = TIME_PATTERN.match(time_str)
        if not match:
            return False
        
        hour = int(match.group(1))
        minute = int(match.group(2))
        
        # Check today
        if now.weekday() in weekdays:
            start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            end_dt = start_dt + timedelta(minutes=duration)
            
            if start_dt <= now < end_dt:
                return True
        
        # Check if started yesterday and extends to today (cross-midnight)
        yesterday = now.date() - timedelta(days=1)
        yesterday_weekday = yesterday.weekday()
        
        if yesterday_weekday in weekdays:
            start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=1)
            end_dt = start_dt + timedelta(minutes=duration)
            
            if start_dt <= now < end_dt:
                return True
        
        return False

    def _calculate_next_transition(
        self, now: datetime, active_items: list[dict[str, Any]]
    ) -> datetime | None:
        """Calculate the next transition time (start or end) across all entities."""
        candidates = []
        
        # Add all possible start times
        for item in self.items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            next_start = self._calculate_next_start(item, now)
            if next_start:
                candidates.append(next_start)
        
        # Add end times for currently active items
        for item in active_items:
            next_end = self._calculate_item_end(item, now)
            if next_end:
                candidates.append(next_end)
        
        # Return earliest
        if candidates:
            return min(candidates)
        
        return None
    
    def _calculate_next_transition_for_entity(
        self, items: list[dict[str, Any]], now: datetime, active_items: list[dict[str, Any]]
    ) -> datetime | None:
        """Calculate the next transition time (start or end) for a specific entity."""
        candidates = []
        
        # Add all possible start times for this entity
        for item in items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            next_start = self._calculate_next_start(item, now)
            if next_start:
                candidates.append(next_start)
        
        # Add end times for currently active items
        for item in active_items:
            next_end = self._calculate_item_end(item, now)
            if next_end:
                candidates.append(next_end)
        
        # Return earliest
        if candidates:
            return min(candidates)
        
        return None

    def _calculate_next_start(
        self, item: dict[str, Any], now: datetime
    ) -> datetime | None:
        """Calculate next start time for an item."""
        time_str = item.get(ITEM_TIME)
        weekdays = item.get(ITEM_WEEKDAYS, [])
        
        if not time_str or not weekdays:
            return None
        
        # Parse time
        match = TIME_PATTERN.match(time_str)
        if not match:
            return None
        
        hour = int(match.group(1))
        minute = int(match.group(2))
        
        # Try next 8 days (today + 7 more days)
        for day_offset in range(8):
            candidate_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
            
            # Skip if in the past (including if it's exactly now, we want future)
            if candidate_dt <= now:
                continue
            
            # Check if weekday matches
            if candidate_dt.weekday() in weekdays:
                _LOGGER.debug(
                    "Next start for item %s: %s (now: %s, day_offset: %d, weekday: %d)",
                    item.get(ITEM_TIME),
                    candidate_dt.isoformat(),
                    now.isoformat(),
                    day_offset,
                    candidate_dt.weekday()
                )
                return candidate_dt
        
        return None

    def _calculate_item_end(
        self, item: dict[str, Any], now: datetime
    ) -> datetime | None:
        """Calculate end time for currently active item."""
        time_str = item.get(ITEM_TIME)
        duration = item.get(ITEM_DURATION, 30)
        weekdays = item.get(ITEM_WEEKDAYS, [])
        
        if not time_str or not weekdays:
            return None
        
        # Parse time
        match = TIME_PATTERN.match(time_str)
        if not match:
            return None
        
        hour = int(match.group(1))
        minute = int(match.group(2))
        
        # Check today
        if now.weekday() in weekdays:
            start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            end_dt = start_dt + timedelta(minutes=duration)
            
            if start_dt <= now < end_dt:
                return end_dt
        
        # Check yesterday (cross-midnight)
        yesterday = now.date() - timedelta(days=1)
        yesterday_weekday = yesterday.weekday()
        
        if yesterday_weekday in weekdays:
            start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=1)
            end_dt = start_dt + timedelta(minutes=duration)
            
            if start_dt <= now < end_dt:
                return end_dt
        
        return None

    def get_active_items_count(self, entity_id: str | None = None) -> int:
        """Get count of currently active items.
        
        Args:
            entity_id: If provided, only count items for this entity.
        """
        now = dt_util.now()
        if entity_id:
            # Get items for this entity
            items = [item for item in self.items if item.get(ITEM_ENTITY_ID) == entity_id]
            return len(self._get_active_items_for_entity(items, now))
        return len(self._get_active_items(now))

    def _validate_entity(self, entity_id: str) -> bool:
        """Validate that entity exists and is a switch.
        
        Args:
            entity_id: Entity ID to validate.
            
        Returns:
            True if entity is valid, False otherwise.
        """
        # Check if entity exists
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Entity %s not found", entity_id)
            return False
        
        # Check if it's a switch
        domain = entity_id.split('.')[0]
        if domain != "switch":
            _LOGGER.warning("Entity %s is not a switch (domain=%s)", entity_id, domain)
            return False
        
        # Check if turn_off service is available
        if not self.hass.services.has_service("switch", "turn_off"):
            _LOGGER.warning("Service switch.turn_off not available")
            return False
        
        return True

    def _start_max_runtime_monitor(self, entity_id: str) -> None:
        """Start monitoring max runtime for entity.
        
        Args:
            entity_id: Entity ID to monitor.
        """
        # Get max_runtime from options
        entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
        max_minutes = entity_max_runtime.get(entity_id)
        
        if not max_minutes or max_minutes <= 0:
            return  # No max runtime configured
        
        # Validate entity
        if not self._validate_entity(entity_id):
            return
        
        # Cancel existing timer if any
        self._stop_max_runtime_monitor(entity_id)
        
        _LOGGER.info(
            "Starting max runtime monitor for %s: will auto-shutoff in %d minutes",
            entity_id,
            max_minutes
        )
        
        # Schedule auto-shutoff
        async def auto_shutoff(now):
            """Turn off entity after max runtime."""
            state = self.hass.states.get(entity_id)
            if state and state.state == STATE_ON:
                _LOGGER.warning(
                    "Max runtime reached for %s (%d minutes) - auto shutting off",
                    entity_id,
                    max_minutes
                )
                try:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                except Exception as e:
                    _LOGGER.error("Failed to auto-shutoff %s: %s", entity_id, e)
            
            # Clean up timer reference
            if entity_id in self._max_runtime_timers:
                del self._max_runtime_timers[entity_id]
        
        # Schedule callback
        delay = timedelta(minutes=max_minutes)
        self._max_runtime_timers[entity_id] = async_call_later(
            self.hass,
            delay,
            auto_shutoff
        )

    def _stop_max_runtime_monitor(self, entity_id: str) -> None:
        """Stop monitoring max runtime for entity.
        
        Args:
            entity_id: Entity ID to stop monitoring.
        """
        if entity_id in self._max_runtime_timers:
            _LOGGER.debug("Stopping max runtime monitor for %s", entity_id)
            self._max_runtime_timers[entity_id]()
            del self._max_runtime_timers[entity_id]
