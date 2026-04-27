# Drive Uploader

A macOS app for uploading any file or folder to Google Drive — built for large video files on external drives. Includes optional Export Watch (auto-queue Resolve renders) and Email Notification (send client a share link on completion).

---

## What it does

- **Upload any file or folder** from anywhere on your Mac — external drives, NAS, local storage
- **Resumable uploads** — session URI saved after every chunk; picks up exactly where it left off after a crash or network drop
- **Pause / Resume** — ⏸ pauses mid-upload and saves position; ▶ resumes instantly from the same byte
- **Real-time progress** — per-file progress bars with data rate and ETA
- **ZIP or Keep Structure** mode for folder uploads
- **Multiple named Google Drive accounts** — switch between personal and client accounts
- **Export Watch** *(optional)* — monitor a folder and auto-queue video files when an export finishes
- **Email Notification** *(optional)* — send client a Gmail share link after each upload, with an editable template

---

## Optional Features

### Export Watch

Toggle on to watch a local folder (e.g. your DaVinci Resolve export destination). When a new video file appears and its size has been stable for 10 seconds (confirming the render is complete), it's automatically added to the upload queue.

Supported formats: `.mp4 .mov .mxf .r3d .braw .mkv .avi .prores .dng`

### Email Notification

Toggle on to email a shareable Drive link to a recipient after each upload completes. Uses Gmail SMTP with an App Password — credentials stored in macOS Keychain, never written to disk.

**Template variables:** `{filename}` `{link}` `{date}` `{sender_name}`

Click **Template…** in the panel to edit the subject and body. Click **Setup** to configure your Gmail sender identity.

---

## Requirements

- macOS 12+
- Homebrew Python 3.14 (`brew install python@3.14 && brew install python-tk@3.14`)
- A Google Cloud project with the Drive API enabled ([setup guide below](#google-drive-setup))
- *(Email Notification only)* A Gmail account with an [App Password](https://myaccount.google.com/apppasswords) generated

---

## Installation

### 1. Clone the repo
```bash
git clone https://github.com/CanadianWiteout/drive-uploader.git
cd drive-uploader
```

### 2. Install dependencies
```bash
/opt/homebrew/bin/python3.14 -m pip install --break-system-packages -r requirements.txt
```

### 3. Run or build
```bash
# Run directly
/opt/homebrew/bin/python3.14 main.py

# Or build a standalone .app
bash build.sh
cp -r "dist/Drive Uploader.app" /Applications/
```

On first launch, click **Manage** next to Google Account to add a Drive account.

---

## Google Drive Setup

You need a `credentials.json` file from Google Cloud Console for each Google account you want to connect. The file is uploaded through the app when adding an account — it never needs to be placed in the project folder.

### Step 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown → **New Project**
3. Name it (e.g. "Kootenay Color Tools") and click **Create**

### Step 2 — Enable the Drive API

1. Go to **APIs & Services → Library**
2. Search **Google Drive API** → click **Enable**

### Step 3 — Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** → **Create**
3. Fill in App name, support email, and developer contact email
4. On the **Test users** screen, add your Google account email
   > Required — OAuth will be blocked without it
5. Save and continue through remaining screens

### Step 4 — Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Set type to **Desktop app**, give it a name, click **Create**
4. Download the JSON file

### Step 5 — Add the account in the app

1. Open Drive Uploader → click **Manage** next to Google Account
2. Click **+ Add Account**
3. Enter an optional nickname, click **Browse…**, select your `credentials.json`
4. Click **Connect Google Account** — a browser window opens for sign-in
5. Approve and close — credentials saved to macOS Keychain

> **Cost:** The Drive API is free for personal use. No billing required.

> **Tip:** Repeat from Step 1 for additional Google accounts (e.g. personal + client). Each needs its own Cloud project and credentials file.

---

## Gmail App Password (Email Notification only)

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Enable **2-Step Verification** if not already on
3. Search **App passwords** → create one for "Mail"
4. Copy the 16-character password → paste it into **Setup** in the Email Notification panel

---

## Usage

1. Add a Google Drive account via **Manage → + Add Account**
2. Click **Pick** to choose your upload destination folder
3. *(Optional)* Toggle **Export Watch** on → set your export folder → status dot confirms watching
4. *(Optional)* Toggle **Email Notification** on → enter recipient → click **Setup** for sender config
5. Click **+ Add Files…** or **Add Folder…** to queue uploads manually
6. Uploads start automatically — progress bars show rate and ETA per file
7. Click **⏸** on any row to pause mid-upload; click **▶** to resume from the same byte
8. If the app is closed mid-upload, reopen and click **Resume** — continues from where it left off

---

## Upload Modes

| Mode | What it does |
|---|---|
| **Keep Structure** | Recreates the folder tree inside Drive |
| **ZIP** | Packages the folder into a single `.zip` (stored, no compression — fast for video) |

---

## Building

```bash
bash build.sh
cp -r "dist/Drive Uploader.app" /Applications/

# Or double-click Rebuild.app for a one-click rebuild
```

---

## File Structure

```
drive-uploader/
├── main.py              # GUI app — all UI, upload workers, watch/email panels
├── drive.py             # Google Drive API + resumable upload engine
├── drive_accounts.py    # Multi-account credential manager
├── state.py             # Upload session persistence (~/.drive-uploader-state.json)
├── config.py            # Persistent settings (~/.drive-uploader-config.json)
├── mailer.py            # Gmail SMTP helper
├── sender_profile.py    # Sender identity + Keychain password storage
├── requirements.txt
├── build.sh             # Standalone .app build script
└── Rebuild.app          # Double-click to rebuild Drive Uploader.app
```

---

## Part of the Kootenay Color Toolset

- [kc-project-creator](https://github.com/CanadianWiteout/kc-project-creator) — Project folder structure creator
