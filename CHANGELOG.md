# Changelog

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
