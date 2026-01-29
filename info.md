# Homie Scheduler

Custom integration for schedule management (switch/input_boolean/climate). Works with **Homie Scheduler Cards** for the Lovelace UI.

## Features

- Schedule management for switch/input_boolean/climate entities
- Multiple entities in one instance; time, duration (optional), weekdays
- Custom service_start/service_end per item; conflict protection; overlapping schedules
- Optional boiler max runtime (auto turn-off)

## Entities

- **sensor.homie_scheduler_scheduler_info** – bridge for cards
- **switch.homie_scheduler_schedule_enabled** – global scheduler on/off
- **sensor.homie_scheduler_status** – status + next run
- **sensor.homie_scheduler_next_run** – next transition timestamp

## Services

`homie_scheduler.set_items` / `add_item` / `update_item` / `delete_item` / `set_enabled` / `toggle_enabled` / `set_active_button` / `clear_active_button`

## Requirements

- **Homie Scheduler Cards** for the Lovelace UI (install from HACS or manually)
- Home Assistant 2025.9 or newer

## Icon

To display the custom icon in HA: add `brands/custom_integrations/homie_scheduler/icon.png` to [home-assistant/brands](https://github.com/home-assistant/brands) (see README).
