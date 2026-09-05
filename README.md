# Home Assistant Add-ons

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-OS%20%7C%20Supervised-41BDF5.svg)](https://www.home-assistant.io/)
[![Add-ons](https://img.shields.io/badge/add--ons-2-brightgreen.svg)](#the-add-ons)

Two add-ons I run on a Raspberry Pi 4, published because neither existed when I
went looking for them.

---

## 🚀 Install the repository

You add the **repository** once. Every add-on inside it then appears in your
store, including any I add later.

**1.** In Home Assistant, go to **Settings → Add-ons → Add-on Store**.

**2.** Click the **⋮** menu in the top right, then **Repositories**.

**3.** Paste this address and click **Add**:

```
https://github.com/edrianolima/home-assistant-addons
```

**4.** Close the dialog. Scroll down and you will find both add-ons under
**Home Assistant Add-ons**.

**5.** Click the one you want, then **Install**. Read its page first: both need
something set up beforehand.

> [!TIP]
> Nothing is installed by adding the repository. It only tells Home Assistant
> where to look. You choose what to install, and you can remove the repository
> at any time.

---

## The add-ons

### 🖥️ [Raspberry Pi Case Control](rpi_case_control/)

![aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![armv7](https://img.shields.io/badge/armv7-yes-green.svg)
![amd64](https://img.shields.io/badge/amd64-yes-green.svg)

Turns the fan, RGB LEDs and little screen of a Raspberry Pi tower case into
Home Assistant controls.

- The **fan** speeds up on its own as the CPU heats up, or you set it by hand
- The **LEDs** get a full colour picker and a brightness slider
- The **screen** shows system stats, a clock, or any text you type into
  Home Assistant

Built for the **GeeekPi Mini Tower**, and every pin is a setting, so other cases
with the same parts work too.

**You need:** an MQTT broker (the Mosquitto add-on is enough).

<img src="rpi_case_control/docs/dashboard.png" width="380" alt="The add-on's controls on a Home Assistant dashboard">

---

### 🕹️ [RomM](romm/)

![aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![amd64](https://img.shields.io/badge/amd64-yes-green.svg)

[RomM](https://github.com/rommapp/romm) as an add-on: your retro game
collection, with box art and details fetched automatically, playable straight
in the browser.

- Scans your ROMs and finds **covers, screenshots and manuals** for them
- **Play in the browser**, no emulator to install, on any device
- Accounts for the family, with **separate saves** per person

The emulator runs on the machine of whoever is playing, so hosting it barely
touches the Pi.

**You need:** the MariaDB add-on. RomM does not work with SQLite.

---

## Getting help

Something not working? Each add-on page has a **Troubleshooting** section that
covers what actually goes wrong, with the exact error messages.

Still stuck, or found a bug? [Open an issue](https://github.com/edrianolima/home-assistant-addons/issues)
and include the add-on's log from its **Log** tab.

---

## License

MIT. Use it, change it, ship it. See [LICENSE](LICENSE).
