"""Constants for the Homie Schedule integration."""
from typing import Final

DOMAIN: Final = "homie_scheduler"

# Config entry keys
CONF_SWITCH_ENTITY: Final = "switch_entity"
CONF_NAME: Final = "name"
CONF_ENTITY_MAX_RUNTIME: Final = "entity_max_runtime"  # Dict: {entity_id: max_minutes}
CONF_DEFAULT_DURATION: Final = "default_duration"  # Default slot duration (minutes) when not specified

# Defaults
DEFAULT_NAME: Final = "Homie Scheduler"
DEFAULT_DURATION: Final = 30  # Default slot duration in minutes for set_items when item has no duration

# Scheduler internal: window (seconds) to detect "turned on by scheduler" vs user/button/physical
# If switch turns ON within this window after scheduler called service_start, we skip max_runtime monitor (slot controls turn-off)
SCHEDULER_TURN_ON_WINDOW_SECONDS: Final = 10

# Schedule item keys
ITEM_ID: Final = "id"
ITEM_ENTITY_ID: Final = "entity_id"  # Entity this item controls
ITEM_ENABLED: Final = "enabled"
ITEM_TIME: Final = "time"
ITEM_DURATION: Final = "duration"
ITEM_WEEKDAYS: Final = "weekdays"
ITEM_SERVICE_START: Final = "service_start"  # Service to call when slot starts: {name: "service_name", value: {...}}
ITEM_SERVICE_END: Final = "service_end"  # Service to call when slot ends: {name: "service_name", value: {...}}

# Storage keys
STORAGE_ITEMS: Final = "items"
STORAGE_ENABLED: Final = "enabled"
STORAGE_ACTIVE_BUTTONS: Final = "active_buttons"  # Store active button states: {entity_id: {button_id, timer_end, duration}}

# Duration validation
# No hard limits - cards specify their own min/max via duration_range
# Integration only validates that duration is a positive integer

# Weekdays (0=Monday, 6=Sunday)
WEEKDAYS: Final = list(range(7))

# Service names
SERVICE_SET_ITEMS: Final = "set_items"
SERVICE_ADD_ITEM: Final = "add_item"
SERVICE_UPDATE_ITEM: Final = "update_item"
SERVICE_DELETE_ITEM: Final = "delete_item"
SERVICE_SET_ENABLED: Final = "set_enabled"
SERVICE_TOGGLE_ENABLED: Final = "toggle_enabled"
SERVICE_SET_ACTIVE_BUTTON: Final = "set_active_button"
SERVICE_CLEAR_ACTIVE_BUTTON: Final = "clear_active_button"

# Attributes
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_ITEMS: Final = "items"
ATTR_ITEM_ID: Final = "id"
ATTR_ENTITY_ID: Final = "entity_id"
ATTR_ENABLED: Final = "enabled"
ATTR_TIME: Final = "time"
ATTR_DURATION: Final = "duration"
ATTR_WEEKDAYS: Final = "weekdays"
ATTR_SERVICE_START: Final = "service_start"
ATTR_SERVICE_END: Final = "service_end"
ATTR_CONTROLLED_ENTITY: Final = "controlled_entity"
ATTR_NEXT_RUN: Final = "next_run"
ATTR_ACTIVE_ITEMS: Final = "active_items"

# Platforms
PLATFORMS: Final = ["switch", "sensor"]
