# Homie Scheduler Integration

Home Assistant custom integration for schedule management (switch/input_boolean/climate). Works together with **Homie Scheduler Cards** for the Lovelace UI.

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

- **Homie Scheduler Cards** (install from HACS or [repository](https://github.com/positivecrash/homie-scheduler-cards))
- Home Assistant 2025.9 or newer

## Documentation

- [Entities and services](https://github.com/positivecrash/homie-scheduler-integration#readme)
- [Homie Scheduler Cards](https://github.com/positivecrash/homie-scheduler-cards) – Lovelace cards for this integration

## Icon

To show the custom icon in HA: add the icon to [home-assistant/brands](https://github.com/home-assistant/brands) (see [info.md](info.md)).

## Publishing to GitHub (first time)

1. On GitHub create a new **empty** repository (e.g. `homie-scheduler-integration`); do not add README or .gitignore.
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
