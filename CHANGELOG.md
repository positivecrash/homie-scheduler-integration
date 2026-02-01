# Changelog

## [1.0.1]

### Changed

- Simplified entity structure: `sensor.homie_schedule_next_run` and `sensor.homie_schedule_status` removed
- Improved max runtime setting for entities
- Overlap and cold start: one formula — `turn_off = min(slot_end, entity_start + max_runtime)`
- On overlap, `active_buttons.timer_end` and `duration` are updated so the status card shows the corrected "will be off" time
- Settings: removed `step=5` restriction on "Time to turn off" — any integer value (step=1) is now allowed
