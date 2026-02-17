"""Scheduler coordinator for Scheduler integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re
import time
from typing import Any

from homeassistant.components.recorder import history as recorder_history
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

STORAGE_VERSION = 1
STORAGE_KEY_LAST_RUNS_PREFIX = "homie_scheduler_last_runs"

from .const import (
    CONF_ENTITY_MAX_RUNTIME,
    RECOVERY_AFTER_SLOT_END_SECONDS,
    ITEM_DURATION,
    ITEM_ENTITY_ID,
    ITEM_ENABLED,
    ITEM_TIME,
    ITEM_WEEKDAYS,
    ITEM_SERVICE_START,
    ITEM_SERVICE_END,
    STORAGE_ACTIVE_BUTTONS,
)

_LOGGER = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r"^([0-1][0-9]|2[0-3]):([0-5][0-9])$")

# Domains supported by the integration (on/off from entity state)
_SUPPORTED_DOMAINS: frozenset[str] = frozenset({
    "switch", "input_boolean", "light", "fan", "cover", "climate", "water_heater",
})
# Subset: "off" = off, any other state = on (e.g. climate heat/cool, water_heater eco/electric)
_SUPPORTED_DOMAINS_OFF_MEANS_OFF: frozenset[str] = frozenset({"climate", "water_heater"})


def _entity_state_is_on(entity_id: str, state_obj: Any) -> bool:
    """Return True if entity is considered 'on'."""
    if state_obj is None or not getattr(state_obj, "state", None):
        return False
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    if domain not in _SUPPORTED_DOMAINS:
        return False
    state = str(state_obj.state).lower()
    if state in ("unavailable", "unknown"):
        return False
    if domain in _SUPPORTED_DOMAINS_OFF_MEANS_OFF:
        return state != "off"
    return state == STATE_ON.lower()


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
        
        # Per-entity timers (each entity has its own next transition and cancel callback)
        self._entity_next_transition: dict[str, datetime] = {}
        self._entity_cancel_timers: dict[str, CALLBACK_TYPE] = {}
        self._cancel_state_listeners: dict[str, CALLBACK_TYPE] = {}
        
        # Listeners for updates
        self._listeners: list[CALLBACK_TYPE] = []
        
        # Track last notification time to prevent spam
        self._last_notify_time: float = 0.0
        
        # Max runtime monitoring
        # Key: entity_id, Value: cancel function for auto-shutoff timer
        self._max_runtime_timers: dict[str, CALLBACK_TYPE] = {}
        
        # Expected turn-off time for max_runtime monitors (Unix timestamp in ms)
        # Key: entity_id, Value: turn_off_time_ms (when monitor will turn off the entity)
        self._max_runtime_turn_off_times: dict[str, int] = {}

        # Button-card turn-off timers (run for / recirculation): server-side so they fire after app close
        # Key: entity_id, Value: cancel callback for async_call_later
        self._button_turn_off_cancels: dict[str, CALLBACK_TYPE] = {}

        # Skip turn-on on first reschedule after startup (prevents boiler turning on after HA restart)
        self._cold_start: bool = True

        # Last run per entity (for status card): {entity_id: {started_at_iso, ended_at_iso, duration_minutes}}
        self._entity_last_run: dict[str, dict[str, Any]] = {}
        # Persist last runs so "Latest activity" survives HA restart
        self._last_runs_store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_LAST_RUNS_PREFIX}_{entry.entry_id}"
        )

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
        """Return next transition time (earliest across all entities, for bridge sensor)."""
        if not self._entity_next_transition:
            return None
        return min(self._entity_next_transition.values())

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
                duration = int(item.get(ITEM_DURATION, 30))
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
        self._do_notify_listeners()

    @callback
    def _do_notify_listeners(self) -> None:
        """Notify all listeners (no throttle). Use when bridge must update immediately (e.g. active_buttons)."""
        try:
            for listener in self._listeners:
                try:
                    listener()
                except Exception as e:
                    _LOGGER.error("Error in listener callback: %s", e)
        except Exception as e:
            _LOGGER.error("Error notifying listeners: %s", e)

    @callback
    def notify_listeners_immediate(self) -> None:
        """Force immediate notification (e.g. after set_active_button so UI gets new active_buttons)."""
        self._do_notify_listeners()

    async def _async_load_last_runs(self) -> None:
        """Load persisted last runs so Latest activity survives HA restart."""
        try:
            data = await self._last_runs_store.async_load()
            if data and isinstance(data, dict):
                self._entity_last_run.update(data)
        except Exception:
            pass

    async def _async_save_last_runs(self) -> None:
        """Persist last runs to store."""
        try:
            await self._last_runs_store.async_save(dict(self._entity_last_run))
        except Exception as e:
            _LOGGER.warning("Could not save last runs to store: %s", e)

    def _get_tracked_entity_ids_for_last_run(self) -> set[str]:
        """Entity IDs we track for Latest activity (items, entity_max_runtime, active_buttons, already in store)."""
        entity_ids = set()
        for item in self.items:
            eid = item.get(ITEM_ENTITY_ID)
            if eid:
                entity_ids.add(eid)
        entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {}) or {}
        for eid in entity_max_runtime:
            if eid and entity_max_runtime.get(eid, 0) > 0:
                entity_ids.add(eid)
        for eid in self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}) or {}:
            if eid:
                entity_ids.add(eid)
        for eid in self._entity_last_run:
            if eid:
                entity_ids.add(eid)
        return entity_ids

    def _last_run_from_states(self, entity_id: str, states: list) -> bool:
        """Find last on->off in states (chronological), set _entity_last_run[entity_id]. Return True if set."""
        if not states:
            return False
        for i in range(len(states) - 1, 0, -1):
            st_end = states[i]
            st_start = states[i - 1]
            if not _entity_state_is_on(entity_id, st_end) and _entity_state_is_on(entity_id, st_start):
                try:
                    ended_at = dt_util.as_utc(st_end.last_changed)
                    started_at = dt_util.as_utc(st_start.last_changed)
                    duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
                    duration_minutes = duration_seconds // 60
                    self._entity_last_run[entity_id] = {
                        "started_at": started_at.isoformat(),
                        "ended_at": ended_at.isoformat(),
                        "duration_minutes": duration_minutes,
                        "duration_seconds": duration_seconds,
                    }
                    return True
                except Exception:
                    pass
                break
        return False

    async def _async_refresh_last_runs_from_history(self) -> None:
        """Refresh last_run from recorder history once at integration start.
        For each tracked entity request history 7d then 30d; take last on->off (started_at, ended_at, duration).
        """
        from homeassistant.components.recorder import get_instance

        entity_ids = self._get_tracked_entity_ids_for_last_run()
        if not entity_ids:
            return
        now = dt_util.utcnow()
        instance = get_instance(self.hass)

        def _state_changes(eid: str, start_ts: datetime, end_ts: datetime):
            return recorder_history.state_changes_during_period(
                self.hass,
                start_ts,
                end_ts,
                eid,
                include_start_time_state=True,
                no_attributes=True,
            ).get(eid, [])

        updated = False
        for entity_id in entity_ids:
            try:
                start_7 = now - timedelta(days=7)
                states = await instance.async_add_executor_job(_state_changes, entity_id, start_7, now)
                if self._last_run_from_states(entity_id, states):
                    updated = True
                    continue
                start_30 = now - timedelta(days=30)
                states = await instance.async_add_executor_job(_state_changes, entity_id, start_30, now)
                if self._last_run_from_states(entity_id, states):
                    updated = True
            except Exception:
                pass
        if updated:
            self.notify_listeners_immediate()  # So bridge/card get latest activity right away (no throttle)
            await self._async_save_last_runs()

    async def async_start(self) -> None:
        """Start the scheduler."""
        try:
            await self._async_load_last_runs()
            # Get all unique entity_ids from items, entity_max_runtime, and active_buttons
            # (max_runtime: when turned on outside schedule; active_buttons: e.g. recirculation-only entities for last_run)
            entity_ids = set()
            for item in self.items:
                entity_id = item.get(ITEM_ENTITY_ID)
                if entity_id:
                    entity_ids.add(entity_id)
            entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
            for entity_id in entity_max_runtime:
                if entity_id and entity_max_runtime.get(entity_id, 0) > 0:
                    entity_ids.add(entity_id)
            for entity_id in self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}):
                if entity_id:
                    entity_ids.add(entity_id)
            for entity_id in self._entity_last_run:
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

            # Restore button turn-off timers (run for / recirculation) from active_buttons
            self._restore_button_turn_off_timers()
            
            # Check entities with max_runtime - if already ON, start monitoring
            # _start_max_runtime_monitor skips if entity has active slot (slot controls turn-off)
            def _start_monitors_for_on_entities() -> None:
                entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
                for entity_id, max_minutes in entity_max_runtime.items():
                    if max_minutes > 0:
                        state = self.hass.states.get(entity_id)
                        if _entity_state_is_on(entity_id, state):
                            self._start_max_runtime_monitor(entity_id)

            _start_monitors_for_on_entities()
            # Also run after 5s: restore_state may apply after integration startup
            async_call_later(self.hass, 5.0, lambda _: _start_monitors_for_on_entities())
            # Latest activity: one refresh from history at start only
            await self._async_refresh_last_runs_from_history()
        except Exception as e:
            _LOGGER.error("Error starting scheduler: %s", e, exc_info=True)
            # Don't re-raise - allow HA to continue

    async def async_stop(self) -> None:
        """Stop the scheduler."""
        # Cancel all per-entity timers
        for entity_id in list(self._entity_cancel_timers.keys()):
            cancel = self._entity_cancel_timers.pop(entity_id, None)
            if cancel:
                cancel()
        self._entity_next_transition.clear()
        
        # Cancel all max runtime timers
        for entity_id in list(self._max_runtime_timers.keys()):
            self._stop_max_runtime_monitor(entity_id)
        
        # Cancel all button turn-off timers
        for eid in list(self._button_turn_off_cancels.keys()):
            self._cancel_button_turn_off(eid)

        # Cancel all state listeners
        for cancel_listener in self._cancel_state_listeners.values():
            if cancel_listener:
                cancel_listener()
        self._cancel_state_listeners.clear()
        self._entity_states.clear()

    async def async_reload(self) -> None:
        """Reload scheduler after config change (options, items, entity_max_runtime, active_buttons)."""
        # Do not stop/restart max_runtime monitors — "Runs will be off" must not be touched by schedule toggle
        # (only slot that is active at current time controls turn-off; max_runtime is for manual/button run)

        # Restore button turn-off timers from active_buttons (so they survive app close)
        self._restore_button_turn_off_timers()
        
        await self._async_reschedule(skip_enforce=True)

    @callback
    def _handle_switch_state_change(self, event) -> None:
        """Handle switch state change."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        
        if new_state is None:
            return
        entity_id = new_state.entity_id
        old_was_on = _entity_state_is_on(entity_id, old_state) if old_state else False
        new_is_on = _entity_state_is_on(entity_id, new_state)

        # Entity turned on (including when old_state was None — e.g. after HA restart or first event)
        if new_is_on and not old_was_on:
            entity_state = self._entity_states.get(entity_id, {})
            if entity_state.get("blocked_until"):
                entity_state["blocked_until"] = None
            self._entity_states[entity_id] = entity_state
            self._start_max_runtime_monitor(entity_id)
        
        if not new_is_on:
            self._stop_max_runtime_monitor(entity_id)
            self.hass.async_create_task(self._async_clear_active_button_on_turn_off(entity_id))
            # Latest activity: update from state_changed (same event source as Logbook). Only this entity_id is tracked;
            # if an external button toggles another entity (e.g. input_boolean), add that entity to scheduler config.
            if old_state and old_was_on:
                try:
                    started_at = dt_util.as_utc(old_state.last_changed)
                    ended_at = dt_util.as_utc(new_state.last_changed)
                    duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
                    duration_minutes = duration_seconds // 60
                    self._entity_last_run[entity_id] = {
                        "started_at": started_at.isoformat(),
                        "ended_at": ended_at.isoformat(),
                        "duration_minutes": duration_minutes,
                        "duration_seconds": duration_seconds,
                    }
                    self.notify_listeners_immediate()
                    self.hass.async_create_task(self._async_save_last_runs())
                except Exception:
                    pass
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
            except Exception:
                pass
            if entity_id in self._entity_states:
                self._entity_states[entity_id]["scheduler_controlled_on"] = False

    async def _async_reschedule(self, skip_enforce: bool = False) -> None:
        """Recalculate and reschedule next transition.
        
        skip_enforce: If True, only recalc next transition and set timer; do not call service_start/service_end.
                      Use when config changed (user toggled slots) — boiler on/off only on timer fire.
        """
        try:
            # Cancel all per-entity timers (will be re-set per entity below)
            for entity_id in list(self._entity_cancel_timers.keys()):
                cancel = self._entity_cancel_timers.pop(entity_id, None)
                if cancel:
                    cancel()
            self._entity_next_transition.clear()
            
            if not self.is_enabled:
                self._notify_listeners()
                return
            
            now = dt_util.now()
            
            # Read fresh items from entry (not cached)
            current_items = self.items  # This reads from entry.options.get("items", [])
            
            # Group items by entity_id
            items_by_entity: dict[str, list[dict[str, Any]]] = {}
            for item in current_items:
                entity_id = item.get(ITEM_ENTITY_ID)
                if not entity_id:
                    continue
                if entity_id not in items_by_entity:
                    items_by_entity[entity_id] = []
                items_by_entity[entity_id].append(item)
            
            # Update state listeners: items + entity_max_runtime + active_buttons (last_run for recirculation-only entities)
            entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
            max_runtime_entity_ids = {eid for eid, mins in entity_max_runtime.items() if eid and mins > 0}
            active_buttons_entity_ids = set(self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}).keys())
            last_run_entity_ids = set(self._entity_last_run.keys())
            current_entity_ids = set(items_by_entity.keys()) | max_runtime_entity_ids | active_buttons_entity_ids | last_run_entity_ids
            existing_entity_ids = set(self._cancel_state_listeners.keys())
            
            # Add listeners for new entities
            for entity_id in current_entity_ids - existing_entity_ids:
                self._cancel_state_listeners[entity_id] = async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._handle_switch_state_change,
                )
                self._entity_states[entity_id] = self._entity_states.get(entity_id) or {
                    "scheduler_controlled_on": False,
                }
            
            # Remove listeners for entities no longer in config; turn off if currently on (we controlled by schedule)
            for entity_id in existing_entity_ids - current_entity_ids:
                state = self.hass.states.get(entity_id)
                if state and _entity_state_is_on(entity_id, state):
                    domain = entity_id.split(".")[0] if "." in entity_id else "switch"
                    if self.hass.services.has_service(domain, "turn_off"):
                        self.hass.async_create_task(
                            self.hass.services.async_call(domain, "turn_off", {"entity_id": entity_id}, blocking=True)
                        )
                if entity_id in self._cancel_state_listeners:
                    self._cancel_state_listeners[entity_id]()
                    del self._cancel_state_listeners[entity_id]
                if entity_id in self._entity_states:
                    del self._entity_states[entity_id]
            
            # Process each entity independently: enforce state and set per-entity timer
            for entity_id, entity_items in items_by_entity.items():
                # Calculate current state for this entity
                active_items = self._get_active_items_for_entity(entity_items, now)
                should_be_on = len(active_items) > 0
                
                if not skip_enforce:
                    await self._async_enforce_switch_state(entity_id, should_be_on, entity_items)
                
                # Schedule this entity's next transition (per-entity timer)
                next_transition = self._calculate_next_transition_for_entity(entity_items, now, active_items)
                if next_transition:
                    delay = (next_transition - now).total_seconds()
                    self._entity_next_transition[entity_id] = next_transition
                    
                    if delay <= 0:
                        self.hass.async_create_task(self._async_handle_entity_transition(entity_id))
                    elif delay < 1.0:
                        self._entity_cancel_timers[entity_id] = async_call_later(
                            self.hass, 1.0, self._make_entity_transition_callback(entity_id)
                        )
                    else:
                        self._entity_cancel_timers[entity_id] = async_call_later(
                            self.hass, delay, self._make_entity_transition_callback(entity_id)
                        )
            
            # Notify listeners (entities)
            self._notify_listeners()

            # After first reschedule (startup), allow turn-on on next transition
            self._cold_start = False
            
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
            
            domain = entity_id.split(".")[0] if "." in entity_id else "switch"
            is_on = _entity_state_is_on(entity_id, switch_state)

            entity_state = self._entity_states.get(entity_id, {})
            scheduler_controlled = entity_state.get("scheduler_controlled_on", False)

            # If schedule wants it ON, but we are within a "blocked until" window,
            # do not re-enable until that time passes — unless we're at the START of a new slot (timer just fired).
            if should_be_on:
                blocked_until = entity_state.get("blocked_until")
                if blocked_until:
                    now = dt_util.now()
                    if now < blocked_until:
                        # Check if we're at the start of a new slot (within 60s of any active item start)
                        at_new_slot_start = False
                        for item in items:
                            if not self._is_item_active(item, now):
                                continue
                            time_str = item.get(ITEM_TIME)
                            duration_min = int(item.get(ITEM_DURATION, 30))
                            weekdays = item.get(ITEM_WEEKDAYS, [])
                            if not time_str or not weekdays:
                                continue
                            match = TIME_PATTERN.match(time_str)
                            if not match:
                                continue
                            start_dt = now.replace(
                                hour=int(match.group(1)),
                                minute=int(match.group(2)),
                                second=0,
                                microsecond=0,
                            )
                            if now.weekday() not in weekdays:
                                continue
                            duration_min = int(item.get(ITEM_DURATION, 30))
                            if (now - start_dt).total_seconds() < 60 and (now - start_dt).total_seconds() >= 0:
                                at_new_slot_start = True
                                break
                        if at_new_slot_start:
                            entity_state["blocked_until"] = None
                            self._entity_states[entity_id] = entity_state
                        else:
                            return
                    else:
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
                    # On cold start (after HA restart), do not turn on - wait for next transition
                    if self._cold_start:
                        return
                    # Need to turn on — entity was OFF before slot (cold start)
                    # Logic: turn off at slot_end; cap by max_runtime if slot_duration > max_runtime
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
                        
                        # Mark that scheduler is turning on (set before call so state_changed sees it immediately)
                        entity_state["scheduler_controlled_on"] = True
                        self._entity_states[entity_id] = entity_state
                        await self.hass.services.async_call(
                            service_domain,
                            service_method,
                            service_value,
                            blocking=True,
                        )
                        # Store slot end time in _max_runtime_turn_off_times; cap by max_runtime (single place)
                        now = dt_util.now()
                        active_items_for_entity = self._get_active_items_for_entity(items, now)
                        if active_items_for_entity:
                            slot_ends = [self._calculate_item_end(item, now) for item in active_items_for_entity]
                            slot_ends = [t for t in slot_ends if t is not None]
                            if slot_ends:
                                latest_end = max(slot_ends)
                                actual_end = self._effective_turn_off_time(entity_id, latest_end, entity_start=now)
                                turn_off_time_ms = int(actual_end.timestamp() * 1000)
                                self._max_runtime_turn_off_times[entity_id] = turn_off_time_ms
                                # Notify listeners so bridge sensor updates
                                self._notify_listeners()
                    except Exception as e:
                        _LOGGER.error("Failed to call service_start for %s: %s", entity_id, e)
                else:
                    # Already on, schedule active — overlap: turn_off = min(slot_end, entity_start + max_runtime)
                    # Use max(slot_ends) — stay on until last slot ends (same as cold start)
                    now = dt_util.now()
                    active_items_for_entity = self._get_active_items_for_entity(items, now)
                    slot_ends = [self._calculate_item_end(item, now) for item in active_items_for_entity]
                    slot_ends = [t for t in slot_ends if t is not None]
                    slot_end = max(slot_ends) if slot_ends else None
                    actual_end = self._effective_turn_off_time(entity_id, slot_end) if slot_end else None
                    if actual_end is not None and actual_end <= now:
                        actual_end = None

                    if actual_end is None:
                        # elapsed >= max_runtime or remaining <= 0 — turn off now
                        if not scheduler_controlled:
                            entity_state["scheduler_controlled_on"] = True
                            self._entity_states[entity_id] = entity_state
                        if not service_end:
                            service_end = {"name": f"{domain}.turn_off", "value": {"entity_id": entity_id}}
                        if service_end:
                            try:
                                sn = service_end["name"]
                                sd, sm = (sn.split(".", 1) if "." in sn else (domain, sn))
                                sv = dict(service_end["value"])
                                if "entity_id" not in sv:
                                    sv["entity_id"] = entity_id
                                await self.hass.services.async_call(sd, sm, sv, blocking=True)
                                entity_state["scheduler_controlled_on"] = False
                                self._entity_states[entity_id] = entity_state
                                if entity_id in self._max_runtime_turn_off_times:
                                    del self._max_runtime_turn_off_times[entity_id]
                                self._notify_listeners()
                            except Exception as e:
                                _LOGGER.error("Failed to turn off %s: %s", entity_id, e)
                    else:
                        if not scheduler_controlled:
                            entity_state["scheduler_controlled_on"] = True
                            self._entity_states[entity_id] = entity_state
                        turn_off_time_ms = int(actual_end.timestamp() * 1000)
                        self._max_runtime_turn_off_times[entity_id] = turn_off_time_ms
                        # Status card prioritizes active_buttons.timer_end — update it so "will be off" shows extended time
                        active_buttons = dict(self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}))
                        if entity_id in active_buttons:
                            remaining_min = int((actual_end - now).total_seconds() // 60)
                            active_buttons[entity_id] = {
                                **active_buttons[entity_id],
                                "timer_end": turn_off_time_ms,
                                "duration": remaining_min,
                            }
                            new_options = {**self.entry.options, STORAGE_ACTIVE_BUTTONS: active_buttons}
                            self.hass.config_entries.async_update_entry(self.entry, options=new_options)
                        self._notify_listeners()
                
            elif not should_be_on and is_on:
                # Need to turn off - check if we control it (or recover: just past slot end)
                now = dt_util.now()
                # Recovery: after HA restart/reload all timers are lost. If the slot already ended while we were down,
                # we're "just past slot end". Within RECOVERY_AFTER_SLOT_END (e.g. 10 min) we still turn off the entity.
                # If the server was off longer than 10 min after the slot end, we don't turn off (entity stays on).
                just_past_slot_end = not scheduler_controlled and self._is_just_after_slot_end(
                    items, now, RECOVERY_AFTER_SLOT_END_SECONDS
                )
                if scheduler_controlled or just_past_slot_end:
                    if not service_end:
                        service_end = {"name": f"{domain}.turn_off", "value": {"entity_id": entity_id}}
                    if service_end:
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
                            
                            # Clear slot end time from _max_runtime_turn_off_times
                            if entity_id in self._max_runtime_turn_off_times:
                                del self._max_runtime_turn_off_times[entity_id]
                                self._notify_listeners()
                        except Exception as e:
                            _LOGGER.error("Failed to call service_end for %s: %s", entity_id, e)
                    else:
                        # No service_end and not switch - just mark as not controlled
                        entity_state["scheduler_controlled_on"] = False
                        self._entity_states[entity_id] = entity_state
                        
                        # Clear slot end time from _max_runtime_turn_off_times
                        if entity_id in self._max_runtime_turn_off_times:
                            del self._max_runtime_turn_off_times[entity_id]
                            self._notify_listeners()
                else:
                    pass  # Not controlled by scheduler - don't turn off
        except Exception as e:
            _LOGGER.error("Error enforcing switch state for %s: %s", entity_id, e, exc_info=True)
            # Don't re-raise - allow scheduler to continue

    def _make_entity_transition_callback(self, entity_id: str):
        """Return an async callback for async_call_later that handles this entity's transition."""
        async def _cb(fire_time: datetime) -> None:
            await self._async_handle_entity_transition(entity_id)
        return _cb

    async def _async_handle_entity_transition(self, entity_id: str) -> None:
        """Handle scheduled transition for a single entity (its timer fired)."""
        now = dt_util.now()
        items_for_entity = [i for i in self.items if i.get(ITEM_ENTITY_ID) == entity_id]
        if not items_for_entity:
            return
        active_items = self._get_active_items_for_entity(items_for_entity, now)
        should_be_on = len(active_items) > 0
        await self._async_enforce_switch_state(entity_id, should_be_on, items_for_entity)
        await self._async_reschedule_entity(entity_id, skip_enforce=True)

    async def _async_reschedule_entity(self, entity_id: str, skip_enforce: bool = False) -> None:
        """Reschedule only this entity's timer (and optionally enforce its state)."""
        try:
            if not self.is_enabled:
                return
            items_for_entity = [i for i in self.items if i.get(ITEM_ENTITY_ID) == entity_id]
            if not items_for_entity:
                cancel = self._entity_cancel_timers.pop(entity_id, None)
                if cancel:
                    cancel()
                self._entity_next_transition.pop(entity_id, None)
                self._notify_listeners()
                return
            now = dt_util.now()
            active_items = self._get_active_items_for_entity(items_for_entity, now)
            should_be_on = len(active_items) > 0
            if not skip_enforce:
                await self._async_enforce_switch_state(entity_id, should_be_on, items_for_entity)
            next_transition = self._calculate_next_transition_for_entity(items_for_entity, now, active_items)
            cancel = self._entity_cancel_timers.pop(entity_id, None)
            if cancel:
                cancel()
            self._entity_next_transition.pop(entity_id, None)
            if next_transition:
                delay = (next_transition - now).total_seconds()
                self._entity_next_transition[entity_id] = next_transition
                if delay <= 0:
                    self.hass.async_create_task(self._async_handle_entity_transition(entity_id))
                elif delay < 1.0:
                    self._entity_cancel_timers[entity_id] = async_call_later(
                        self.hass, 1.0, self._make_entity_transition_callback(entity_id)
                    )
                else:
                    self._entity_cancel_timers[entity_id] = async_call_later(
                        self.hass, delay, self._make_entity_transition_callback(entity_id)
                    )
            self._notify_listeners()
        except Exception as e:
            _LOGGER.error("Error rescheduling entity %s: %s", entity_id, e, exc_info=True)

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
        duration = int(item.get(ITEM_DURATION, 30))
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
    
    def _effective_turn_off_time(
        self, entity_id: str, proposed_end: datetime, entity_start: datetime | None = None
    ) -> datetime:
        """Single place: cap proposed_end by max_runtime from integration options.
        Regardless of how the entity was turned on (slot, button, external), run never exceeds max_runtime.
        entity_start: when the run started; if None, read from current switch state (only works if entity is on).
        """
        entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
        max_minutes = entity_max_runtime.get(entity_id, 0)
        if max_minutes <= 0:
            return proposed_end
        if entity_start is None:
            switch_state = self.hass.states.get(entity_id)
            if not switch_state or not _entity_state_is_on(entity_id, switch_state) or not getattr(switch_state, "last_changed", None):
                return proposed_end
            entity_start = dt_util.as_utc(switch_state.last_changed)
        return min(proposed_end, entity_start + timedelta(minutes=max_minutes))

    def _calculate_next_transition_for_entity(
        self, items: list[dict[str, Any]], now: datetime, active_items: list[dict[str, Any]]
    ) -> datetime | None:
        """Calculate the next transition time (start or end) for a specific entity.
        
        For active slots: use slot end capped by max_runtime (slot controls, max caps).
        For entity on without active slot (manual/button): use max_runtime turn-off.
        """
        candidates = []
        
        # Get entity_id from items
        entity_id = items[0].get(ITEM_ENTITY_ID) if items else None
        
        # Add end times for currently active items — cap by max_runtime (single place)
        switch_state = self.hass.states.get(entity_id) if entity_id else None
        is_on = entity_id and switch_state and _entity_state_is_on(entity_id, switch_state)
        run_start = dt_util.as_utc(switch_state.last_changed) if (is_on and switch_state and getattr(switch_state, "last_changed", None)) else now
        for item in active_items:
            next_end = self._calculate_item_end(item, now)
            if next_end and entity_id:
                capped = self._effective_turn_off_time(entity_id, next_end, entity_start=run_start)
                candidates.append(capped)
        
        # Add max_runtime turn-off only when entity is on but NO active slot (manual/button turn-on)
        if entity_id and not active_items:
            entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
            max_minutes = entity_max_runtime.get(entity_id, 0)
            if max_minutes > 0:
                switch_state = self.hass.states.get(entity_id)
                if _entity_state_is_on(entity_id, switch_state) and switch_state and getattr(switch_state, "last_changed", None):
                    last_changed_dt = dt_util.as_utc(switch_state.last_changed)
                    max_runtime_turn_off = last_changed_dt + timedelta(minutes=max_minutes)
                    if max_runtime_turn_off > now:
                        candidates.append(max_runtime_turn_off)
        
        # Add all possible start times for this entity
        for item in items:
            if not item.get(ITEM_ENABLED, True):
                continue
            
            next_start = self._calculate_next_start(item, now)
            if next_start:
                candidates.append(next_start)
        
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
                return candidate_dt
        
        return None

    def _calculate_item_end(
        self, item: dict[str, Any], now: datetime
    ) -> datetime | None:
        """Calculate end time for currently active item."""
        time_str = item.get(ITEM_TIME)
        duration = int(item.get(ITEM_DURATION, 30))
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

    def _is_just_after_slot_end(
        self, items: list[dict[str, Any]], now: datetime, window_seconds: int | None = None
    ) -> bool:
        """Return True if now is within window_seconds after any slot's end (for recovery when timer was lost)."""
        if window_seconds is None:
            window_seconds = RECOVERY_AFTER_SLOT_END_SECONDS
        for item in items:
            if not item.get(ITEM_ENABLED, True):
                continue
            time_str = item.get(ITEM_TIME)
            duration = int(item.get(ITEM_DURATION, 30))
            weekdays = item.get(ITEM_WEEKDAYS, [])
            if not time_str or not weekdays:
                continue
            match = TIME_PATTERN.match(time_str)
            if not match:
                continue
            hour = int(match.group(1))
            minute = int(match.group(2))
            # Today
            if now.weekday() in weekdays:
                start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                end_dt = start_dt + timedelta(minutes=duration)
                if end_dt < now and (now - end_dt).total_seconds() <= window_seconds:
                    return True
            # Yesterday (slot ended in the night)
            yesterday = now.date() - timedelta(days=1)
            if yesterday.weekday() in weekdays:
                start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=1)
                end_dt = start_dt + timedelta(minutes=duration)
                if end_dt < now and (now - end_dt).total_seconds() <= window_seconds:
                    return True
        return False

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
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Entity %s not found in hass.states", entity_id)
            return False
        domain = entity_id.split(".")[0] if "." in entity_id else "switch"
        if not self.hass.services.has_service(domain, "turn_off"):
            _LOGGER.warning(
                "Entity %s (domain=%s): turn_off service not available",
                entity_id, domain,
            )
            return False
        return True

    def _entity_has_active_slot(self, entity_id: str) -> bool:
        """Return True if there is an active schedule slot for this entity (slot controls turn-off)."""
        now = dt_util.now()
        items_for_entity = [
            i for i in self.items
            if i.get(ITEM_ENTITY_ID) == entity_id and i.get(ITEM_ENABLED, True)
        ]
        return len(self._get_active_items_for_entity(items_for_entity, now)) > 0

    def _start_max_runtime_monitor(self, entity_id: str) -> None:
        """Start monitoring max runtime for entity.

        Skips if entity has active slot — slot controls turn-off, don't overwrite.
        """
        # Get max_runtime from options
        entity_max_runtime = self.entry.options.get(CONF_ENTITY_MAX_RUNTIME, {})
        max_minutes = entity_max_runtime.get(entity_id)
        if not max_minutes or max_minutes <= 0:
            return  # No max runtime configured
        if self._entity_has_active_slot(entity_id):
            return

        # Validate entity
        if not self._validate_entity(entity_id):
            _LOGGER.warning("Entity validation failed for %s, skipping monitor", entity_id)
            return

        # Cancel existing timer if any
        self._stop_max_runtime_monitor(entity_id)

        # After restart we don't know when the entity was turned on — use state.last_changed so max_runtime is respected
        switch_state = self.hass.states.get(entity_id)
        run_start = None
        if switch_state and getattr(switch_state, "last_changed", None):
            run_start = dt_util.as_utc(switch_state.last_changed)
        now = dt_util.utcnow()
        elapsed_minutes = int((now - run_start).total_seconds() / 60) if run_start else 0
        remaining_minutes = max(0, max_minutes - elapsed_minutes)

        if remaining_minutes <= 0:
            domain = entity_id.split(".")[0] if "." in entity_id else "switch"
            self.hass.async_create_task(
                self.hass.services.async_call(domain, "turn_off", {"entity_id": entity_id}, blocking=True)
            )
            return

        async def auto_shutoff(now):
            """Turn off entity after max runtime."""
            state = self.hass.states.get(entity_id)
            if _entity_state_is_on(entity_id, state):
                _LOGGER.warning("Max runtime reached for %s (%d min) - auto shutting off", entity_id, max_minutes)
                domain = entity_id.split(".")[0] if "." in entity_id else "switch"
                try:
                    await self.hass.services.async_call(domain, "turn_off", {"entity_id": entity_id}, blocking=True)
                except Exception as e:
                    _LOGGER.error("Failed to auto-shutoff %s: %s", entity_id, e)

            # Clean up timer reference
            if entity_id in self._max_runtime_timers:
                del self._max_runtime_timers[entity_id]

        delay = timedelta(minutes=remaining_minutes)
        self._max_runtime_timers[entity_id] = async_call_later(
            self.hass,
            delay,
            auto_shutoff
        )
        turn_off_time_ms = int((time.time() + (remaining_minutes * 60)) * 1000)
        self._max_runtime_turn_off_times[entity_id] = turn_off_time_ms

        # Notify bridge sensor immediately so cards can update
        self.notify_listeners_immediate()

    def _stop_max_runtime_monitor(self, entity_id: str) -> None:
        """Stop monitoring max runtime for entity.
        
        Args:
            entity_id: Entity ID to stop monitoring.
        """
        if entity_id in self._max_runtime_timers:
            self._max_runtime_timers[entity_id]()
            del self._max_runtime_timers[entity_id]
        if entity_id in self._max_runtime_turn_off_times:
            del self._max_runtime_turn_off_times[entity_id]
            self.notify_listeners_immediate()

    async def _async_clear_active_button_on_turn_off(self, entity_id: str) -> None:
        """Clear active_buttons when entity turns off so 'will be off' resets on next turn-on."""
        state = self.hass.states.get(entity_id)
        if state and _entity_state_is_on(entity_id, state):
            return  # Entity turned back on before this ran — don't wipe new active_buttons
        active_buttons = dict(self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}))
        if entity_id not in active_buttons:
            return
        del active_buttons[entity_id]
        new_options = {**self.entry.options, STORAGE_ACTIVE_BUTTONS: active_buttons}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self.notify_listeners_immediate()

    def _cancel_button_turn_off(self, entity_id: str) -> None:
        """Cancel server-side button turn-off timer for entity."""
        if entity_id in self._button_turn_off_cancels:
            self._button_turn_off_cancels[entity_id]()
            del self._button_turn_off_cancels[entity_id]

    def _schedule_button_turn_off(self, entity_id: str, timer_end_ms: int) -> None:
        """Schedule server-side turn_off at timer_end (Unix ms). Survives app close."""
        import time as _time
        self._cancel_button_turn_off(entity_id)
        now_ms = int(_time.time() * 1000)
        delay_sec = max(0.0, (timer_end_ms - now_ms) / 1000.0)
        if delay_sec <= 0:
            return

        @callback
        def _fire(_dt: datetime) -> None:
            self._button_turn_off_cancels.pop(entity_id, None)

            async def _do() -> None:
                try:
                    domain = entity_id.split(".", 1)[0]
                    await self.hass.services.async_call(
                        domain,
                        SERVICE_TURN_OFF,
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                except Exception as e:
                    _LOGGER.warning("Button turn-off failed for %s: %s", entity_id, e)
                active_buttons = dict(self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}))
                if entity_id in active_buttons:
                    del active_buttons[entity_id]
                    new_opts = {**self.entry.options, STORAGE_ACTIVE_BUTTONS: active_buttons}
                    self.hass.config_entries.async_update_entry(self.entry, options=new_opts)
                self.notify_listeners_immediate()

            self.hass.async_create_task(_do())

        self._button_turn_off_cancels[entity_id] = async_call_later(
            self.hass,
            delay_sec,
            _fire,
        )

    def _restore_button_turn_off_timers(self) -> None:
        """Cancel all button timers and re-schedule from active_buttons (startup/reload)."""
        import time as _time
        for eid in list(self._button_turn_off_cancels.keys()):
            self._cancel_button_turn_off(eid)
        now_ms = int(_time.time() * 1000)
        active_buttons = self.entry.options.get(STORAGE_ACTIVE_BUTTONS, {}) or {}
        for eid, data in active_buttons.items():
            if not eid or not isinstance(data, dict):
                continue
            timer_end = data.get("timer_end")
            if timer_end is None:
                continue
            try:
                te_ms = int(timer_end)
            except (TypeError, ValueError):
                continue
            if te_ms > now_ms:
                self._schedule_button_turn_off(eid, te_ms)
