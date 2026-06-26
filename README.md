# Uplift

A native macOS app for uploading files and folders to Google Drive — built for large video files on external drives. Includes a Watch Folder mode (auto-queue DaVinci Resolve exports), flexible ZIP options, and Email Notification (send a share link on completion).

---

## Download

Grab the latest build from the [Releases page](https://github.com/kootenaycolor/uplift/releases/latest) — open the DMG, drag to Applications, launch.

No dependencies required. Python is bundled inside the app.

---

## Features

### Upload

- **Upload files or folders** from anywhere — external drives, NAS, local storage
- **Resumable uploads** — session URI saved after every chunk; survives crashes and network drops
- **Pause / Resume** — pauses mid-upload and resumes from the exact byte
- **Real-time progress** — per-file progress bars with transfer rate and ETA
- **Clear selection** — one-click button clears the current file/folder queue before starting
- **ZIP or Keep Structure** — choose how folder contents are packaged per upload job

### ZIP Options (Upload Files tab)

- **No zip** — upload files flat, or recreate the folder tree in Drive
- **Single zip (keep structure)** — one `.zip` preserving internal paths
- **Single zip (flatten)** — one `.zip` with all files at root level

### Watch Folder

Monitor a local folder and auto-upload whenever an export finishes.

**Include Subfolders** toggle unlocks the action chooser. Available actions depend on ZIP mode:

| ZIP on + Subfolders on | ZIP off + Subfolders on |
|---|---|
| Zip each subfolder separately | Mirror folder structure |
| Zip each subfolder, upload root files individually | Upload all files flat |
| One zip, keep folder structure | |
| One zip, flatten everything | |

- **Zip each subfolder separately** — each subfolder becomes its own `.zip`; root-level files are ignored
- **Zip each subfolder, upload root files individually** — subfolders zipped, loose root files uploaded as-is
- **One zip, keep structure** — entire batch in one `.zip` preserving paths
- **One zip, flatten** — entire batch in one `.zip`, all files at root

The action chooser is a styled dropdown matching the email composer — same teal border, rounded popup, hover highlight on each row.

### Email Notification

Send a Gmail share link on upload completion. Triggered per job or per folder.

**When to send** options (manual jobs):

- **Auto** — smart batching; sends one email per logical group
- **After all files finish** — one email when the full job completes
- **Send email per folder** — one email per uploaded folder
- **After every N files** — batches of N
- **After every file** — one email per file
- **At a scheduled time**
- **After queue is done**

**When to send** options (watch jobs) add watch-specific triggers:

- **Auto** — recommended; adapts to the watch batch pattern
- **Per subfolder batch** — fires when each subfolder batch is stable
- **When a drop finishes** — fires after the quiet-period settle
- **After every N files**
- **After every file**
- **At a scheduled time**
- **After queue is done**

**Presets** — save named timing configurations per mode. Watch presets and manual presets are stored separately so each mode only shows relevant options.

**Scope** — body template, recipient, and timing presets are stored independently and persist across relaunches.

### Accounts

- **Multiple Google Drive accounts** — add as many as you need, switch per job
- Credentials stored in macOS Keychain — nothing written to disk unencrypted

### Stability

- **Per-message crash protection** in the upload polling loop — one bad message can't kill the timer
- **Persistent crash log** at `~/.uplift-crash.log` — exceptions from all threads written with timestamps
- **Repo detection** — recognizes git repositories in folder uploads
- **Large folder guardrail** — warns before queuing folders with more than 1,000 files
- **Duplicate prevention** — deduplication guard in folder-structure uploads

### Quit Behavior

- **Tap Cmd+Q** — hides to the menu-bar tray, keeps running
- **Hold Cmd+Q (~1 s)** — fully quits the process
- **Tray → Quit Uplift** — always quits; shows warning if uploads or watch jobs are active
- **Cmd+W / title-bar ×** — hides to tray, keeps running

---

## Requirements

- macOS 12+
- **DMG install**: no additional dependencies — Python is bundled inside the app
- **Run from source**: Python 3.11+
- A Google Cloud project with the Drive API enabled ([setup guide below](#google-drive-setup))
- *(Email only)* A Gmail account with an [App Password](https://myaccount.google.com/apppasswords)

---

## Installation

### Option A — DMG (recommended)

1. Download from [Releases](https://github.com/kootenaycolor/uplift/releases/latest)
2. Open the DMG, drag **Uplift** to **Applications**
3. Launch from Applications or Spotlight

No dependencies. Python is bundled inside the app.

### Option B — Run from source

```bash
git clone https://github.com/kootenaycolor/uplift.git
cd uplift
pip3 install -r requirements.txt
python3 main.py
```

### Option C — Build your own .app

```bash
# Dev build (requires system Python 3.14)
bash build.sh

# Self-contained DMG with Python bundled (no Python needed on target machine)
bash build.sh --dmg
```

Requires Python 3.14 at `/Library/Frameworks/Python.framework/Versions/3.14` on the build machine only.

---

## Google Drive Setup

You need a `credentials.json` from Google Cloud Console for each account you want to connect.

### Step 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown → **New Project**
3. Name it and click **Create**

### Step 2 — Enable the Drive API

1. Go to **APIs & Services → Library**
2. Search **Google Drive API** → **Enable**

### Step 3 — Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** → **Create**
3. Fill in App name, support email, and developer contact
4. On the **Test users** screen, add your Google account email
5. Save and continue

### Step 4 — Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Set type to **Desktop app**, click **Create**
4. Download the JSON file

### Step 5 — Add the account in Uplift

1. Open Uplift → **Settings → Accounts → + Add Account**
2. Enter a nickname and select your `credentials.json`
3. Click **Connect** — a browser window opens for sign-in
4. Approve and close — credentials saved to macOS Keychain

> **Cost:** The Drive API is free for personal use. No billing required.

---

## Gmail App Password (Email only)

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Enable 2-Step Verification
3. Search **App passwords** → create one for "Mail"
4. Paste the 16-character password into **Email → Setup** in the app

---

## Usage

1. Add a Google Drive account via **Accounts** in the title bar
2. Select an upload destination folder with **Pick Drive folder…**
3. Drop files/folders onto the drop zone, or click **Browse…** / **Browse Folder…**
4. *(Optional)* Toggle **Zip** on and configure zip options
5. *(Optional)* Toggle **Email** on → open Email Settings → set recipient, body, and when to send
6. Click **Add to Queue** — uploads start automatically
7. Click ⏸ on any job to pause; click ▶ to resume from the same byte

For Watch Folder:

1. Switch to the **Watch** tab
2. Pick a local folder to monitor and a Drive destination
3. Configure the action chooser (what to do when files appear)
4. *(Optional)* Turn on **Email** and configure notification settings
5. Click **Start Watching**

---

## File Structure

```
uplift/
├── main.py              # UI and upload engine (PyQt6)
├── drive.py             # Google Drive API + resumable upload
├── drive_accounts.py    # Multi-account credential manager
├── state.py             # Upload session persistence (~/.uplift-state.json)
├── config.py            # App settings (~/.uplift-config.json)
├── mailer.py            # Gmail SMTP helper
├── sender_profile.py    # Sender identity + Keychain storage
├── build.sh             # Builds Uplift.app (macOS, requires Python 3.14)
├── theme_editor.py      # Visual theme editor (dev tool)
├── fonts/               # Bundled fonts
├── design/              # App icon source files
├── requirements.txt
└── CLAUDE.md            # Design system reference
```

---

## Part of the Kootenay Color Toolset

- [kc-project-creator](https://github.com/kootenaycolor/kc-project-creator) — Project folder structure creator
