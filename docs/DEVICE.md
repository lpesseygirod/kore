# KORE: from a new Raspberry Pi to Pepe running

Written 2026-11-08 for BUILD-PLAN phase 7. Follow the steps in order; each one ends with a check. Where a step
**costs money** it says so; everything else is free. If a check doesn't look like the text here, stop and ask
Claude Code (paste what you see).

> Status today: everything below works except the very last piece of code, the box's own voice loop
> (`kore.home --pi`: the speakerphone, the ElevenLabs voice and the button, sensor and light together). It is being
> built now. Until it's there, `kore-home` keeps restarting (harmless) while the web page, the night updates and the
> self-test already work.

Names used here: the box is called **kore-abuelos** and your user on it is **lea**. If you choose other names,
use yours wherever these appear.

---

## 1. What to buy  (costs money: about €250–330)

The list, with why and rough prices, is in [HARDWARE.md](HARDWARE.md) ("Buy before phase 7"). In short: Raspberry
Pi 5 (8 GB), the official 27 W power supply, active cooler, the official RTC battery, an M.2 SSD board with a
256 GB NVMe SSD, a case that fits both, the LD2410C presence sensor, a large arcade button, a 12-LED WS2812 ring
(with wires), jumper wires (female-female), and the USB speakerphone you already have.

Also useful (costs money, about €15): **a USB adapter for NVMe SSDs**, so you can write the system straight onto
the SSD from your Mac (step 2). Without it, use the microSD card first and ask Claude Code for the steps to move to
the SSD afterwards.

Free: Raspberry Pi Imager (on your Mac), Tailscale's personal plan, everything in this repo.

## 2. Write the system onto the SSD  (free)

1. On your Mac, download **Raspberry Pi Imager** from raspberrypi.com/software and open it.
2. If you don't have an SSH key yet, open Terminal on the Mac and type `ssh-keygen -t ed25519`, then press Enter
   three times. (It makes a key pair in your home folder; nothing is sent anywhere.)
3. Put the SSD in the USB adapter and plug it into the Mac.
4. In Imager choose: **Device** Raspberry Pi 5; **Operating system** Raspberry Pi OS (other) → **Raspberry Pi OS
   Lite (64-bit)**; **Storage** the SSD (check the size says about 256 GB, so you don't erase the wrong disk).
5. When it asks about settings, choose **Edit settings**:
   - General: hostname `kore-abuelos`; username `lea` and a password you keep in your password manager;
     Wi-Fi: your home network's name and password, country ES; time zone Europe/Madrid, keyboard es.
   - Services: **Enable SSH**, "Allow public-key authentication only", and press "Run SSH-KEYGEN" or paste the
     contents of `~/.ssh/id_ed25519.pub`.
6. Save, Yes, Yes. It takes a few minutes.

**Check:** Imager says "Write successful".

## 3. Put it together  (free)

Unplug the power while you do any of this.

1. Fit the active cooler, the SSD board with the SSD, and the RTC battery (it plugs into the small two-pin socket
   marked BAT, near the power socket). Put it in the case.
2. Wire the three parts with the jumper wires. Pin numbers count on the 40-pin header (the two rows of pins
   along one edge): pin 1 is at the end **farthest from the USB ports**, on the inner row; pin 2 is beside it on
   the outer row (the board's edge); odd numbers continue along the inner row, even ones along the outer row. A
   printed pin map (search "Raspberry Pi 5 GPIO pinout") helps. (The full list, if you ever need to change one,
   is in `kore/hardware/pins.py`.)

   | Part | Its wire | Pi pin |
   | --- | --- | --- |
   | Button (either leg) | 1 | pin 11 |
   | Button (other leg) | 2 | pin 9 (ground) |
   | Presence sensor LD2410C | VCC | pin 2 (5 V) |
   | | GND | pin 6 (ground) |
   | | OUT | pin 13 |
   | LED ring | 5V / VCC | pin 4 (5 V) |
   | | GND | pin 20 (ground) |
   | | DIN (data in) | pin 19 |

   The LD2410C has more pins (RX, TX): leave them free.
3. Plug the speakerphone into one of the blue USB ports. Then the power supply.

**Check:** after about a minute the Pi's green light flickers and settles. From your Mac's Terminal:
`ping kore-abuelos.local` answers (press Ctrl-C to stop).

## 4. Reach it from your Mac, and from anywhere: Tailscale  (free personal plan)

1. In Terminal: `ssh lea@kore-abuelos.local`. The first time, type `yes`. You are now typing on the box.
2. Install Tailscale on the box (its official installer; Tailscale's licence is BSD):
   `curl -fsSL https://tailscale.com/install.sh | sh`
3. `sudo tailscale up` prints a web address. Open it on your Mac and log in (create a free Tailscale account
   if you don't have one: the Personal plan, no card needed).
4. Install Tailscale on your Mac too (Mac App Store, free) and log in with the same account.
5. In the Tailscale web page (login.tailscale.com → Machines), on kore-abuelos press "…" → **Disable key expiry**.
   Otherwise the box drops off after six months.

**Check:** from your Mac, `ssh lea@kore-abuelos` (without `.local`) works, even with the Mac on another network.
From now on this is how you (and Claude Code, through you) reach the box, wherever it is.

## 5. Install Pepe  (free)

On your Mac, in the KORE folder (Terminal, `cd` to the folder Claude Code works in):

    deploy/send_release.sh --first kore-abuelos

It sends the latest committed version (never `.env`) and prints two lines. Log in to the box
(`ssh lea@kore-abuelos`) and paste them. They look like:

    mkdir -p ~/kore-setup && tar -xzf ~/kore-20261108-210000-abc1234.tar.gz -C ~/kore-setup
    sudo bash ~/kore-setup/deploy/pi/install.sh ~/kore-20261108-210000-abc1234.tar.gz

If you bought the **official** Raspberry Pi RTC battery, add ` --rtc-charge` at the end of the second line, so
the Pi keeps it charged. Never with an ordinary coin cell.

It takes 5–10 minutes (it downloads Python and Pepe's libraries). It ends with "Almost done".

**Check:** `ls -l /opt/kore/current` shows an arrow to `/opt/kore/releases/<version>`.
Then restart the box once (it turned on the light ring's connection): `sudo reboot`, wait a minute, log in again.

## 6. Type the keys  (the accounts themselves may cost money: see BUILD-PLAN section 9)

On the box, one key at a time. Each asks for the value and **does not show it** while you paste:

    sudo kore-set-key ANTHROPIC_API_KEY
    sudo kore-set-key ELEVENLABS_API_KEY
    sudo kore-set-key TELEGRAM_BOT_TOKEN
    sudo kore-set-key TWILIO_ACCOUNT_SID
    sudo kore-set-key TWILIO_AUTH_TOKEN
    sudo kore-set-key TWILIO_FROM_NUMBER
    sudo kore-set-key KORE_HEARTBEAT_URL

The first two are needed for Pepe to talk; the others for help messages and the heartbeat (the same ones as in
your Mac's `.env`). They are stored in `/etc/kore/kore.env`, readable only by the system (root). Never paste a key
into a chat, an email or Claude Code. To change a key later, run the same line again.

Then start Pepe and the web page:

    sudo systemctl start kore-home kore-web

## 7. Check that it works

1. `sudo kore-selftest` prints one line per check and ends `KORE self-test: OK`. A `[warn]` is fine (for
   example a key for later); a `[fail]` says what's wrong.
2. `systemctl status kore-home kore-web` shows **active (running)** for both.
3. The web page: on your Mac, open `http://kore-abuelos:8080` (over Tailscale) or `http://kore-abuelos.local:8080`
   (on the same Wi-Fi). Get your login code on the box with
   `sudo -u kore /opt/kore/current/.venv/bin/python -m kore.web --db /var/lib/kore/kore.db invite`.
4. Say "Pepe" (or press the button briefly) and ask the time. Hold the button for 2 seconds: that is **help**, so
   only do it with the family member warned, or before the help contacts are set up.
5. Walk into the room after it was empty for a few minutes: the morning greeting uses the sensor.
6. Switch the Wi-Fi router off, set a test reminder two minutes ahead, and check Pepe still says it.
7. Leave it running 48 hours (BUILD-PLAN phase 7). What happened: `journalctl -u kore-home --since today`.

## 8. The plug test: pull the plug 10 times  (BUILD-PLAN phase 7, "done when")

Goal: after 10 sudden power cuts in the middle of use, the dose log is intact and the clock is still right.

1. Set up a test medicine due in a few minutes, and confirm a dose by voice ("me la he tomado") so there is
   something in the log.
2. Before the first pull, write down the number of logged doses:
   `sudo -u kore sqlite3 -readonly /var/lib/kore/kore.db "select count(*) from doses"`
3. Ten times: start talking to Pepe (or confirm a dose), and **in the middle**, pull the power cable. Wait
   10 seconds. Plug it back. Wait 2 minutes. For three of the ten, switch the Wi-Fi router off first, so the
   clock has only the RTC battery.
4. After each one: `sudo kore-selftest` must say OK (the clock line says "from the RTC battery" when the
   router is off: that's right), `date` must show the right time, and the dose count must be the same or
   higher, never lower.
5. Write the ten results in a little table (try, what you were doing, self-test, time right, doses) and give it to
   Claude Code for the phase report.

## 9. New versions: they install themselves at night

When Claude Code has a new version and you've agreed, on your Mac:

    deploy/send_release.sh kore-abuelos

That night between 03:30 and 04:30 the box: checks the file arrived whole, prepares the new version next to the
old one, **tests it against the real data without changing anything**, keeps a copy of the database, switches,
restarts Pepe and the page, and tests again. If either test fails it goes back to the version it had, by itself.
The next morning: `ssh lea@kore-abuelos journalctl -u kore-update --since yesterday`.
To install straight away instead of at night: `ssh lea@kore-abuelos sudo kore-update --now`.

If the files in `deploy/pi/` changed in that version, run `install.sh` once more (step 5's second line, with the
new file name); it's safe to repeat.

## 10. Encrypting the disk  (optional for the trial; free)

Honest summary: a box at home that must restart on its own after a power cut, with nobody to type a password,
can't keep its data secret from someone who steals **the whole box**: the key has to be somewhere the box can
reach by itself. What encryption can do is protect the data when **the SSD alone** leaves the box (lost, sent for
repair, thrown away).

What already protects the data today: very little is stored (BUILD-PLAN: transcripts kept 7 days, only with their
OK); deleted rows are overwritten; the keys file is readable only by the system; the nightly backup is encrypted
with your certificate before it leaves the box; logs are kept 7 days.

The recommended simple path, if you want it before the trial: encrypt only Pepe's data folder `/var/lib/kore` (an
encrypted area of the SSD, Linux's standard LUKS), unlocked at boot with a key **stored inside the Pi 5 board
itself** (its one-time-programmable memory), not on the SSD. Then:

- the SSD on its own is unreadable: good for repairs, a lost disk, or throwing it away;
- the box still starts by itself after a power cut;
- a stolen box (board and SSD together) is not protected;
- if the board dies, the data on the SSD can't be read any more: the nightly backup is how you get it back;
- writing the key into the board is permanent (it can't be erased), so do it only on the box that goes to them.

Encrypting the whole system disk as well adds little here for much more complication (it needs the same automatic
key, and a broken start-up is harder to repair from afar). Nothing in `install.sh` encrypts anything; if you choose
the simple path, ask Claude Code for the steps and do them before the database has real data in it.

## If something goes wrong

- `sudo kore-selftest`: what's wrong, one line per check.
- `journalctl -u kore-home -n 100` (Pepe), `journalctl -u kore-web -n 100` (the page),
  `journalctl -u kore-update` (the night updates). Copy what you see to Claude Code, **after** checking it has no
  keys in it (it shouldn't: keys are never printed).
- Pepe restarts by himself after a crash (5 seconds), and the box restarts itself if the whole system freezes.
- Nothing answers at all: pull the plug, wait 10 seconds, plug it back. That is always safe.
