# Drive Uploader

A macOS app for uploading any file or folder to Google Drive with a true resumable upload engine — built for large video files that need to survive network interruptions.

---

## What it does

- **Upload any file or folder** from anywhere on your Mac (external drives, NAS, local storage)
- **Resumable uploads** — if your network drops mid-upload, it picks up exactly where it left off (session URI saved after every chunk)
- **Real-time progress** with data rate and ETA
- **ZIP or Keep Structure** mode for folder uploads
- **Multiple named Google Drive accounts** — switch between personal and client accounts
- **Hierarchical Drive folder picker** — browse your real Google Drive folder tree

---

## Features

- Dark macOS-style UI (CustomTkinter)
- Hierarchical Google Drive folder picker — browse Shared Drives and My Drive as collapsible trees
- 25 MB chunks with sub-chunk progress for smooth UI updates
- Exponential backoff retry on transient network errors
- Upload state persisted to disk — survives crashes and app restarts
- Per-account credentials stored securely in macOS Keychain

---

## Requirements

- macOS 12+
- Homebrew Python 3.14 (`brew install python@3.14`)
- A Google Cloud project with the Drive API enabled ([setup guide below](#google-drive-setup))

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

On first launch, open **Manage** next to Google Account to add a Drive account.

---

## Google Drive Setup

You need a `credentials.json` file from Google Cloud for each Google account you want to connect. These credentials identify the OAuth client — you upload the file through the app when adding an account.

### Step 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Give it a name (e.g. "Kootenay Color Tools") and click **Create**
4. Make sure the new project is selected in the dropdown

### Step 2 — Enable the Drive API

1. In the left sidebar go to **APIs & Services → Library**
2. Search for **Google Drive API** and click it
3. Click **Enable**

### Step 3 — Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** and click **Create**
3. Fill in the required fields:
   - **App name** — anything (e.g. "Drive Uploader")
   - **User support email** — your email
   - **Developer contact email** — your email
4. Click **Save and Continue** through the Scopes and Test Users screens
5. On the **Test users** screen, click **+ Add users** and add your Google account email
   > This is required — without it the OAuth flow will be blocked
6. Click **Save and Continue**, then **Back to Dashboard**

### Step 4 — Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Set **Application type** to **Desktop app**
4. Give it a name (e.g. "Drive Uploader Desktop") and click **Create**
5. Click **Download JSON** on the confirmation dialog (or use the download icon next to it in the credentials list)
6. Save the file — you'll upload it through the app

### Step 5 — Add the account in the app

1. Open Drive Uploader
2. Click **Manage** next to **Google Account**
3. Click **+ Add Account**
4. Enter an optional nickname, then click **Browse…** and select your downloaded `credentials.json`
5. Click **Connect Google Account** — a browser window opens for sign-in
6. Approve access and close the browser tab
7. The account is saved — credentials stored securely in macOS Keychain

> **Cost:** The Drive API is free for personal use. No billing required.

> **Tip:** Repeat from Step 1 with a different Google account if you need to connect multiple accounts (e.g. personal + client Drive). Each account needs its own Cloud project and credentials.json.

---

## Usage

1. Open the app and add a Google Drive account via **Manage** → **+ Add Account**
2. Click **Pick** to choose your upload destination folder in Drive
3. Click **+ Add Files…** or **Add Folder…** to queue uploads
4. Uploads start automatically — progress bars show per-file status with rate and ETA
5. If an upload is interrupted, reopen the app and click **Resume** — it continues from where it left off

---

## Upload Modes

| Mode | What it does |
|---|---|
| **Keep Structure** | Recreates the folder tree inside Drive |
| **ZIP** | Compresses the folder to a single `.zip` before uploading |

---

## Building

```bash
# Build the standalone .app
bash build.sh
cp -r "dist/Drive Uploader.app" /Applications/

# Or double-click Rebuild.app in this folder for a one-click rebuild
```

---

## File Structure

```
drive-uploader/
├── main.py              # GUI app
├── drive.py             # Google Drive API + resumable upload engine
├── drive_accounts.py    # Multi-account credential manager
├── state.py             # Upload session persistence
├── config.py            # Persistent settings (~/.drive-uploader-config.json)
├── requirements.txt
├── build.sh             # Standalone .app build script
└── Rebuild.app          # Double-click to rebuild Drive Uploader.app
```

---

## Part of the Kootenay Color Toolset

- [kc-project-creator](https://github.com/CanadianWiteout/kc-project-creator) — Project folder structure creator
- [export-watcher](https://github.com/CanadianWiteout/export-watcher) — Auto-upload Resolve exports + email client
