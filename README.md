# Uplift

A native macOS app for uploading files and folders to Google Drive — built for large video files on external drives. Includes Watch Folder (auto-queue DaVinci Resolve exports) and Email Notification (send a share link on completion).

---

## Features

- **Upload files or folders** from anywhere — external drives, NAS, local storage
- **Resumable uploads** — session URI saved after every chunk; survives crashes and network drops
- **Pause / Resume** — pauses mid-upload and resumes from the exact byte
- **Real-time progress** — per-file progress bars with transfer rate and ETA
- **ZIP or Keep Structure** — zip a folder into a single file, or recreate the folder tree in Drive
- **Multiple Google Drive accounts** — switch between personal and client accounts per job
- **Watch Folder** *(optional)* — monitor a folder and auto-upload when an export finishes
- **Email Notification** *(optional)* — send a Gmail share link to one or more recipients on completion

---

## Requirements

- macOS 12+
- Python 3.11+ with pip
- A Google Cloud project with the Drive API enabled ([setup guide below](#google-drive-setup))
- *(Email only)* A Gmail account with an [App Password](https://myaccount.google.com/apppasswords)

---

## Installation

### 1. Clone

```bash
git clone https://github.com/CanadianWiteout/drive-uploader.git
cd drive-uploader
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Run or build

```bash
# Run directly
python3 main.py

# Build a standalone .app (macOS only, requires Python 3.14)
bash build.sh
```

On first launch, go to **Settings → Accounts** to add a Google Drive account.

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

### Step 5 — Add the account in the app

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

1. Add a Google Drive account via **Settings → Accounts**
2. Select an upload destination folder with **Pick Drive folder…**
3. Drop files/folders onto the drop zone, or click **Browse…**
4. *(Optional)* Switch to the **Watch** tab to auto-queue exports from a folder
5. *(Optional)* Toggle **Email** on → enter recipient(s), comma-separated
6. Click **Add to Queue** — uploads start automatically
7. Click ⏸ on any job to pause; click ▶ to resume from the same byte

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
├── fonts/               # Proxima Nova + Lato
├── design/              # App icon
├── requirements.txt
└── CLAUDE.md            # Design system reference
```

---

## Part of the Kootenay Color Toolset

- [kc-project-creator](https://github.com/CanadianWiteout/kc-project-creator) — Project folder structure creator
