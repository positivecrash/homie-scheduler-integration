# Homie Scheduler Integration

Home Assistant custom integration for schedule management (switch/input_boolean/climate). **For the Lovelace UI you need** [**Homie Scheduler Cards**](https://github.com/positivecrash/homie-scheduler-cards) — install them from HACS or the repo.

## Installation

### Via HACS (recommended)

1. Open **HACS** → **Integrations** → **Explore & Download Repositories**
2. Search for **Homie Scheduler** and install
3. Restart Home Assistant
4. **Settings** → **Devices & Services** → **Add Integration** → **Homie Scheduler**

### Manual

```bash
git clone https://github.com/positivecrash/homie-scheduler-integration
cd homie-scheduler-integration
bash install.sh /path/to/homeassistant/config
```

Restart HA, then add the integration via **Settings** → **Devices & Services** → **Add Integration** → **Homie Scheduler**.

## Requirements

- **[Homie Scheduler Cards](https://github.com/positivecrash/homie-scheduler-cards)** for the Lovelace UI (install from HACS or the repository)
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

The integration does **not** register an `OptionsUpdateListener` (`entry.add_update_listener`). Changes to options (e.g. `entity_max_runtime`, schedule items, enabled flag) are applied only when:

- A **service** updates options and then calls `coordinator.async_reload()` (e.g. `set_items`, `set_active_button`, `clear_active_button`, `update_item`, etc.), or  
- You **reload the integration** manually (Devices & Services → Homie Schedule → Reload) or restart Home Assistant.

So after editing options in the integration config screen, you must **reload the integration** (or restart HA) for the new options to take effect. This is intentional: it avoids an automatic full reload on every options save and keeps behavior predictable.

## Publishing to GitHub

1. On GitHub create an **empty** repository (e.g. `homie-scheduler-integration`); do not add README or .gitignore.
2. In the project folder (if this folder is inside another git repo, use a separate copy or a new clone so this project is the only content):

```bash
cd /path/to/homie-scheduler-integration
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/homie-scheduler-integration.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username (e.g. `positivecrash`). Update `manifest.json` and links in this README if the repo URL differs. If the repo already has git and remote, just push.

## Releasing

Releases are used for versioning and for HACS to offer updates.

1. **Commit** your changes, then **tag** and **push**:

```bash
git add .
git commit -m "Release v1.0.0"
git tag v1.0.0
git push origin main
git push origin v1.0.0
```

2. On GitHub: **Releases** → **Create a new release** → choose tag `v1.0.0`, set title (e.g. `v1.0.0`) and optional release notes → **Publish release**.

Use [semantic versioning](https://semver.org/) (e.g. `v1.0.1` for fixes, `v1.1.0` for new features).

## License

MIT – see [LICENSE](LICENSE).
