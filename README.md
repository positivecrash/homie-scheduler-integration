# Homie Scheduler Integration

Home Assistant custom integration for schedule management (switch/input_boolean/climate). **For the Lovelace UI you need** [**Homie Scheduler Cards**](https://github.com/positivecrash/homie-scheduler-cards).

## Features

- Schedule management for switch/input_boolean/climate entities
- Multiple entities in one instance; time, duration (optional), weekdays
- Custom service_start/service_end per item; conflict protection; overlapping schedules
- Optional boiler max runtime (auto turn-off)

The integration schedules turn-on/turn-off by setting deferred callbacks in code via `async_call_later()`. It does not use automations or timer entities. After an HA restart, it rebuilds the schedule from its config and reschedules those callbacks.

## Installation

1. Copy the folder `custom_components/homie_scheduler` into your Home Assistant config directory so you have `config/custom_components/homie_scheduler/` with all integration files inside.
2. Restart Home Assistant.
3. Go to **Settings** → **Devices & Services** → **Add Integration** → search for **Homie Scheduler** and add it.

## Requirements

- **[Homie Scheduler Cards](https://github.com/positivecrash/homie-scheduler-cards)** for the Lovelace UI (install from the repository)
- Home Assistant 2025.9 or newer

## Services and entities

### Entities

| Entity | Entity ID | Type | Description |
|--------|-----------|------|-------------|
| **Scheduler Info** | `sensor.homie_schedule_scheduler_info` | sensor | Bridge sensor used by Lovelace cards. Attributes: `entry_id`, `items`, `entity_next_runs`, `entity_next_transitions`, `active_buttons`, `max_runtime_turn_off_times`, `entity_max_runtime`. Per-entity data for all cards. |
| **Schedule Enabled** | `switch.homie_schedule_schedule_enabled` | switch | Toggle to enable or disable the scheduler. |

### Services

| Service | Description | Main fields |
|---------|-------------|-------------|
| **set_items** | Replace all schedule items at once. | `entry_id`, `items` |
| **add_item** | Add a new schedule slot. | `entry_id`, `entity_id`, `time`, `weekdays`, `duration`, `service_start`, `service_end`, `enabled` |
| **update_item** | Update an existing slot. | `entry_id`, `item_id`, plus optional `entity_id`, `time`, `weekdays`, `duration`, `enabled`, `service_start`, `service_end` |
| **delete_item** | Remove a schedule slot. | `entry_id`, `item_id` |
| **set_enabled** | Enable or disable the scheduler. | `entry_id`, `enabled` |
| **toggle_enabled** | Toggle scheduler on/off. | `entry_id` |
| **set_active_button** | Mark a "RUN FOR" button as active (used by button card). | `entry_id`, `entity_id`, `button_id`, `timer_end`, `duration` |
| **clear_active_button** | Clear active button when entity turns off. | `entry_id`, `entity_id` |

All services are in domain `homie_scheduler`. Cards use these services internally; manual calls are rarely needed.

**How to find `entry_id`:** Open **Developer Tools** → **States**, find `sensor.homie_schedule_scheduler_info`, look at the attribute `entry_id` (e.g. `a1b2c3d4e5f6g7h8...`).

### Options and config updates

The integration does **not** register an `OptionsUpdateListener` (`entry.add_update_listener`). When you save options in the integration config screen (Options Flow), the flow calls `async_schedule_reload()`, so the integration reloads and the new options take effect automatically. When a **service** updates options (e.g. `set_items`, `set_active_button`, `update_item`), it calls `coordinator.async_reload()` so the coordinator picks up the new values.

## License

MIT – see [LICENSE](LICENSE).
