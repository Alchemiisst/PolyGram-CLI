# ✦ PolyGram

### Every account. One terminal.

> **Important:** PolyGram is for accounts that you own and control. User-account automation may violate Telegram's Terms of Service. Use PolyGram humanely and never use it for bulk messaging, spam, harassment, or unauthorized access.

[![Version](https://img.shields.io/badge/version-1.1.4-00bcd4?style=for-the-badge)](https://github.com/Alchemiisst/Polygram-CLI)
[![Platform](https://img.shields.io/badge/platform-Termux%20%7C%20Android-111827?style=for-the-badge)](https://termux.dev/)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-ANCL%20v1.1-e879f9?style=for-the-badge)](LICENSE)
[![Repository](https://img.shields.io/badge/GitHub-Alchemiisst%2FPolygram--CLI-181717?style=for-the-badge&logo=github)](https://github.com/Alchemiisst/Polygram-CLI)

> **A calm, colorful terminal for a complicated digital life.**
>
> Add an account. Keep it encrypted. Relay a login code. Move your whole vault when you need to.

<div align="center">

<img src="assets/polygram-cli-banner.png" alt="PolyGram CLI" width="100%">

### 🟢 Your accounts. 🔐 Your vault. ⚡ Your control.

</div>

---

## Table of contents

- [What PolyGram does](#what-polygram-does)
- [Security model](#security-model)
- [Requirements](#requirements)
- [Quick installation](#quick-installation)
- [Start PolyGram](#start-polygram)
- [First-run setup](#first-run-setup)
- [Command reference](#command-reference)
- [Export and import](#export-and-import)
- [Termux optional features](#termux-optional-features)
- [Project files](#project-files)
- [Vault layout](#vault-layout)
- [Troubleshooting](#troubleshooting)
- [Development and testing](#development-and-testing)
- [Limitations](#limitations)
- [License](#license)

---

## ✨ Why PolyGram feels different

| 🧩 | Built for real life |
|---|---|
| 📱 | Designed for a small Termux screen and a phone-first workflow |
| 🧠 | Simple commands hide the complicated Telegram session work |
| 🛡️ | Security is visible: lock state, audit trail, quarantine, and safe import |
| 🧳 | Export the whole vault as one portable encrypted bundle |
| 🧼 | No clutter, no dashboard, no subscription — just one focused terminal |

<div align="center">

### 🚀 One command. Unlimited accounts. Zero chaos.

`git clone` → `./install.sh` → `PolyGram` → **ready** ✅

</div>

## What PolyGram does

| Feature | Description |
|---|---|
| **Add accounts** | Number → Telegram OTP → optional 2FA → encrypted local session |
| **Login relay** | Watches the Telegram login-notification chat for a new-device code |
| **Encrypted vault** | Stores unlimited account sessions locally with AES-256-GCM |
| **Whole-vault export** | Creates one `.pgs` bundle and displays a one-time key |
| **Safe import** | Gate → decrypt → validate → confirm, with quarantine on tampering |
| **Devices** | Inspect and terminate Telegram authorizations |
| **Security tools** | Audit log, quarantine list, session rotation, code invalidation |
| **Erase** | Deletes local sessions and vault key material while keeping API credentials and backups |

PolyGram is a **single-file Python application**. The repository includes a small launcher so you can type `PolyGram` instead of `python3 polygram.py`.

---

## Security model

- Vault key: PBKDF2-HMAC-SHA256 with **400,000 iterations**.
- Sessions: AES-256-GCM with a random nonce per encrypted session.
- Export bundles: separate random export key, PBKDF2-HMAC-SHA256 with **300,000 iterations**, and AES-GCM authenticated metadata.
- Tamper detection: manifest-bound authentication plus a 12-character SHA-256 fingerprint.
- Wrong or corrupted imports are quarantined rather than loaded.
- Vault auto-locks after 5 minutes of inactivity.
- Secret files are written with restrictive permissions where supported.
- The export key is shown once and is never saved by PolyGram.
- There is no passphrase recovery. If the vault passphrase is forgotten, use `erase` and create a new vault.

**Keep the export file and export key in separate places.** Possession of a Telegram session can provide account access.

---

## Requirements

- Termux on Android
- Python **3.10 or newer**
- `telethon >= 1.34`
- `cryptography >= 41`

Optional:

- Termux:API for notifications, SMS reading, and clipboard integration
- Termux shared storage for Android Download-folder exports

---

## Quick installation

### 1. Install Termux packages

Use Termux from a trusted source such as F-Droid or the official Termux project. Then run:

```bash
pkg update && pkg upgrade -y
pkg install -y python git
```

### 2. Clone the GitHub repository

Clone the official repository:

```bash
git clone https://github.com/Alchemiisst/Polygram-CLI.git
cd Polygram-CLI
```

### 3. Install Python dependencies

```bash
python3 -m pip install --user -r requirements.txt
```

If Termux rejects `--user`, use the environment-supported command:

```bash
python3 -m pip install -r requirements.txt
```

### 4. Install the `PolyGram` command

Run the included installer from inside the cloned repository:

```bash
chmod +x install.sh polygram polygram.py
./install.sh
```

The installer creates this command:

```text
~/.local/bin/PolyGram
```

It points to the cloned repository and starts `polygram.py` automatically.

### 5. Add the command directory to PATH

If the installer prints a PATH command, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

To keep it after restarting Termux:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 6. Verify the installation

```bash
PolyGram --version
PolyGram --selftest
```

You should see:

```text
Polygram v1.1.4 — ✦ EVERY ACCOUNT · ONE TERMINAL ✦
```

After that, simply type:

```bash
PolyGram
```

No `python3` and no `.py` extension are required.

---

## Start PolyGram

After installation:

```bash
PolyGram
```

You can also run the application directly from the repository:

```bash
python3 polygram.py
```

The command-line name is intentionally capitalized as `PolyGram`. Android/Linux filesystems are usually case-sensitive, so use the same capitalization.

---

## First-run setup

On the first launch PolyGram asks for:

1. Your Telegram `api_id`.
2. Your Telegram `api_hash`.
3. A vault master passphrase of at least 12 characters.
4. Optional Termux:API preferences.

Create Telegram API credentials at:

```text
https://my.telegram.org
```

The API credentials belong to your Telegram developer application. They are not the same thing as your Telegram account password.

---

## Command reference

### Interactive application

```bash
PolyGram
```

### Version

```bash
PolyGram --version
```

### Offline self-test

```bash
PolyGram --selftest
```

### Add an account

Interactive:

```bash
PolyGram add
```

Non-interactive login-code flow:

```bash
POLYGRAM_PASS='your-vault-passphrase' PolyGram add \\
  --phone +15550000001 \\
  --code 12345 \\
  --2fa 'your-telegram-2fa-password' \\
  --label 'Work account'
```

Avoid placing secrets in shell history when possible.

### List and manage accounts

```bash
PolyGram accounts
```

### Login relay

```bash
PolyGram login
PolyGram login --account acct-0001
PolyGram login --account +15550000001
```

### Export the vault

```bash
PolyGram export
```

Specify a destination:

```bash
PolyGram export --out /path/to/backup.pgs
```

PolyGram displays the export key. Save it immediately in a password manager. The key is not written to a file.

### Import a vault

Interactive key entry:

```bash
PolyGram import --file /path/to/backup.pgs
```

The key may be pasted with spaces, line breaks, or different capitalization.

Clipboard key mode:

```bash
PolyGram import --file /path/to/backup.pgs --clipboard-key
```

Optional online verification:

```bash
PolyGram import --file /path/to/backup.pgs --verify
```

### Devices

```bash
PolyGram devices
PolyGram devices --account acct-0001
```

### Security menu

```bash
PolyGram security
```

### Settings

```bash
PolyGram settings
```

### Rotate a session

```bash
PolyGram rotate --account acct-0001
```

Rotation creates a new Telegram authorization key and invalidates older exported session bundles for that account.

### Report an unwanted login code

```bash
PolyGram report --account acct-0001
```

### Erase the local vault

Interactive:

```bash
PolyGram erase
```

Non-interactive:

```bash
PolyGram erase --yes
```

Erase keeps `config.json` and the `exports/` directory but removes local vault key material, sessions, account metadata, quarantine files, and audit history.

---

## Export and import

The export process creates a `.pgs` file containing the complete vault:

```text
accounts + encrypted session data + authenticated manifest
```

The export key is displayed only during the export ceremony. PolyGram does not provide a key-file option by design.

Recommended storage arrangement:

```text
Location 1: polygram-YYYYMMDD-...pgs
Location 2: export key in your password manager
```

During import, PolyGram performs:

```text
GATE → STRUCTURE → KEY PARSE → DECRYPT → READ → VALIDATE → CONFIRM
```

A malformed key is treated as an input mistake. A structurally invalid, tampered, or repeatedly unauthenticated file is quarantined.

When restoring into a non-empty vault, PolyGram creates a previous-vault backup before replacement. Merge mode allows duplicate accounts to be skipped, replaced, or copied as new accounts.

---

## Termux optional features

### Termux:API

```bash
pkg install -y termux-api
```

PolyGram can optionally use:

- `termux-notify`
- `termux-sms-list`
- `termux-clipboard-set`
- `termux-clipboard-get`
- `termux-open-url`

You must grant Android permissions where required.

### Shared storage

```bash
termux-setup-storage
```

When available, exports can be placed in the Android Files app's Download folder.

---

## Put this project on GitHub

The official repository is:

**[github.com/Alchemiisst/Polygram-CLI](https://github.com/Alchemiisst/Polygram-CLI)**

If you are adding the files through GitHub's web interface:

1. Open the repository.
2. Select **Add file → Upload files**.
3. Upload the files listed below into the repository root.
4. Keep the filenames exactly as shown. Linux and Termux are case-sensitive.
5. Commit to `main` with a clear message such as `Add PolyGram v1.1.4 application`.
6. Open the repository's **Code** menu and copy the HTTPS clone URL.

For a normal Termux installation, users then run:

```bash
git clone https://github.com/Alchemiisst/Polygram-CLI.git
cd Polygram-CLI
chmod +x install.sh polygram polygram.py
./install.sh
PolyGram
```

## Project files

Upload these files and directories through GitHub file navigation:

```text
PolyGram/
├── assets/
│   └── polygram-cli-banner.png  # README hero banner
├── polygram.py       # Complete single-file application
├── polygram          # Executable local launcher
├── install.sh        # Installs the PolyGram command into ~/.local/bin
├── requirements.txt  # Python dependencies
├── README.md         # Documentation and installation guide
├── LICENSE           # Project license
└── .gitignore        # Prevents secrets/backups from being committed
```

Do **not** upload these local runtime files:

```text
.polygram/
*.pgs
*.session
config.json
vault.meta
vault.sentinel
accounts.json
audit.log
```

---

## Vault layout

By default:

```text
~/.polygram/
├── config.json
├── vault.meta
├── vault.sentinel
├── accounts.json
├── audit.log
├── sessions/
├── exports/
├── quarantine/
├── last-restore-backup/
└── tmp/
```

Change the location with:

```bash
export POLYGRAM_HOME="$HOME/my-polygram-vault"
```

Do not place the vault inside a public Git repository.

---

## Troubleshooting

### `PolyGram: command not found`

Run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

If that fixes it permanently, add the same line to `~/.bashrc`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

Also confirm installation:

```bash
ls -l ~/.local/bin/PolyGram
```

### `cryptography` is missing

```bash
python3 -m pip install -r requirements.txt
```

On Termux, this may also be useful:

```bash
pkg install -y python-cryptography
```

### `Telethon` is missing

```bash
python3 -m pip install -U telethon
```

### I forgot my vault passphrase

There is no recovery path by design. Use:

```bash
PolyGram erase
```

This does not log out of Telegram remotely. It only removes local PolyGram vault data.

### Login codes do not arrive

The code may appear in the Telegram chat named `Telegram` on a device where the account is already logged in. Keep PolyGram open, or use the `s` option to request SMS when Telegram makes that option available.

### The self-test warns that Telethon is missing

The offline cryptographic self-test can still validate vault and bundle behavior, but install Telethon before using account, login, devices, rotate, or relay features.

---

## Development and testing

From the repository root:

```bash
python3 -m py_compile polygram.py
python3 polygram.py --selftest
```

Before committing changes:

1. Run the self-test.
2. Test first-run setup in a temporary `POLYGRAM_HOME`.
3. Test export and import with a test account/session fixture.
4. Confirm no secret or `.pgs` file is tracked by Git.
5. Test both `python3 polygram.py` and `PolyGram`.
6. Check that the version in the header and README is current.

Never commit:

- Telegram session files
- vault files
- export bundles
- passphrases
- export keys
- API credentials

---

## Limitations

- PolyGram cannot read another device's private Telegram chat directly through the official app; the stored session must receive the login-notification message.
- Some numbers cannot receive SMS because of Telegram restrictions.
- SMS auto-capture requires Termux:API and Android SMS permission.
- Device identification uses a `Polygram-CLI` device-model heuristic.
- Online verification during import may be slow for many accounts.
- The application does not recover forgotten vault passphrases.

---

## 📜 License

PolyGram is released under the **Alchemist Non-Commercial License (ANCL v1.1)**.

You may use, study, modify, and share the project for lawful, non-commercial purposes, provided that you:

- keep the license and copyright notice;
- credit **Alchemist** as the original author;
- link back to the official repository;
- do not sell, paywall, monetize, or commercially bundle the project; and
- release public derivatives under the same license unless written permission is granted.

Commercial use requires prior written permission from Alchemist.

Read the complete license here: [`LICENSE`](LICENSE)

> This is a custom project license, not an OSI-approved open-source license. It is designed to be reusable across Alchemist projects. For legal questions, consult a qualified lawyer.
