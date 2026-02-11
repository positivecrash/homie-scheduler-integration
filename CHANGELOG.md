# Changelog

## [1.0.8]

### Changed

- Entity state: `unavailable` and `unknown` are treated as off; supported domains (switch, climate, water_heater) with "off means off" (climate, water_heater) for correct on/off detection.
- When an entity is removed from the schedule (slot deleted), integration calls `turn_off` for that entity if the domain supports it.
- Recovery after slot end: `RECOVERY_AFTER_SLOT_END_SECONDS` (10 min) — if HA was down when a slot ended, we still turn off the entity if "now" is within this window after slot end.
- Max runtime after HA restart: use `state.last_changed` to compute remaining time; if remaining ≤ 0, turn off immediately.
- Latest activity (last run): updated from `state_changed` when entity turns off (started_at/ended_at from old_state/new_state.last_changed); history-based refresh only on HA load.

### Fixed

- In `_async_enforce_switch_state` (should be off, is on): use fresh `now = dt_util.now()` before comparisons to avoid using stale time.

## [1.0.7]

### Changed

- Max run time: single place — all turn-off times (slots, button, external) are capped by `_effective_turn_off_time()`;
- Small Fix: latest activity time 

## [1.0.6]

### Fixed

- "Runs will be off" countdown no longer resets when toggling schedule on/off; reload no longer stops or restarts max_runtime monitors

## [1.0.5]

### Changed

- Latest activity: single source of truth — `entity_last_run` in Store only; removed option `entities_for_last_run` from config entry
- On startup we subscribe to entities from Store keys (`entity_last_run`); status card adds new entities via service `register_entity_for_last_run`
- On HA restart: schedule and slot timers rebuilt from config; latest activity and active "run for" timers restored from Store/options

## [1.0.4]

### Changed

- Button turn-off timers run on the server (survive app close; restore after HA restart)
- Service `register_entity_for_last_run`: status card registers entity so Latest activity is recorded for external turn-on
- Latest activity: store and show duration with seconds (e.g. "4 min 30 s")
- Call `async_migrate_entry` in setup so entity renames (v2→v3) run

## [1.0.3]

### Changed

- Track state for entities in `active_buttons` (e.g. recirculation-only) so "Latest activity" is recorded
- When adding a state listener for an entity that is already ON, set `last_run_start` so last run is saved when it turns off

## [1.0.2]

### Changed

- Reduced log verbosity: removed routine DEBUG/INFO from reschedule loop, slot calculations, and transition scheduling

## [1.0.1]

### Changed
- Simplified entity structure: `sensor.homie_schedule_next_run` and `sensor.homie_schedule_status` removed
- Improved max runtime setting for entities
- Overlap and cold start: one formula — `turn_off = min(slot_end, entity_start + max_runtime)`
- On overlap, `active_buttons.timer_end` and `duration` are updated so the status card shows the corrected "will be off" time
- Settings: removed `step=5` restriction on "Time to turn off" — any integer value (step=1) is now allowed
