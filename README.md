# Homie Scheduler Integration

Home Assistant custom integration for schedule management. **For the Lovelace UI you need** [**Homie Scheduler Cards**](https://github.com/positivecrash/homie-scheduler-cards).

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=positivecrash&repository=homie-scheduler-integration&category=integration)

## Features

- Schedule management for switch, input_boolean, light, fan, cover, and climate entities
- Multiple entities in one instance; time, duration (optional), weekdays
- Custom service_start/service_end per item; conflict protection; overlapping schedules
- Optional boiler max runtime (auto turn-off)

The integration schedules turn-on/turn-off by setting deferred callbacks in code via `async_call_later()`. It does not use automations or timer entities. After an HA restart, it rebuilds the schedule from its config and reschedules those callbacks.

## Manual installation

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
| **Scheduler Info** | `sensor.homie_scheduler_info` | sensor | Bridge sensor used by Lovelace cards. Attributes: `entry_id`, `items`, `entity_next_runs` (next slot start + duration), `entity_next_transitions` (next event start or end, fallback), `entity_last_runs`, `active_buttons`, `max_runtime_turn_off_times`, `entity_max_runtime`. Per-entity data for all cards. |
| **Schedule Enabled** | `switch.homie_scheduler_enabled` | switch | Toggle to enable or disable the scheduler. |

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
| **register_entity_for_last_run** | Register an entity for “latest activity” tracking (used by status card). | `entry_id`, `entity_id` |

All services are in domain `homie_scheduler`. Cards use these services internally; manual calls are rarely needed.

**Weekdays:** In `add_item` and `update_item`, `weekdays` is a list of integers **0–6**: **0 = Monday**, 1 = Tuesday, …, 6 = Sunday (ISO weekday). Example: `[0, 1, 2, 3, 4]` = weekdays only.

**How to find `entry_id`:** Open **Developer Tools** → **States**, find the sensor named **Scheduler Info** (entity ID like `sensor.<your_device_slug>_info`), and read the `entry_id` attribute.

### How automation and timers work

The integration **does not create** automations or timer entities in Home Assistant. Everything runs inside the integration. It uses internal timers (`async_call_later`). When a slot starts, it calls `service_start` (e.g. `switch.turn_on`). When the slot ends or the duration is over, it calls `service_end` (e.g. `switch.turn_off`). The schedule is stored in the integration config. After an HA restart it reads the config and sets all timers again. Max runtime (auto turn-off) and “run for X minutes” from the button card work the same way: a timer in code that turns the entity off when the time is up.

**Entity turned on from outside** (manual, physical button, another automation): the integration does not cancel that. It only listens to state changes on the **Home Assistant server**. If **max runtime** is set for that entity, it starts a timer and turns the entity off after that many minutes. This works **even if the app or browser is closed**, because the integration runs on the server. The status card can show “will be off in X” . If there is also an active schedule slot, the entity is turned off at the slot end (or at max runtime, whichever is earlier). When the entity is turned off (by anyone), the integration clears the “active button” state so the UI updates.

### Options and config updates

The integration does **not** register an `OptionsUpdateListener` (`entry.add_update_listener`). When you save options in the integration config screen (Options Flow), the flow calls `async_schedule_reload()`, so the integration reloads and the new options take effect automatically. When a **service** updates options (e.g. `set_items`, `set_active_button`, `update_item`), it calls `coordinator.async_reload()` so the coordinator picks up the new values.

## License

MIT – see [LICENSE](LICENSE).
