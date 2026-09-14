# Chat2FTP

**Paste a chat message, upload the files it names.**

You ask an AI assistant to change a few files. It hands you a zip and a line like:

```
Upload: `index.php`, `inc/config.php`, `public/css/app.css`, `CHANGES.txt`
```

Chat2FTP unzips the build, works out where each of those files lives inside it, and
pushes exactly those files to your server over FTP, FTPS or SFTP. Nothing else is
touched. No dragging files around in an FTP client, no re-uploading the whole tree, no
missed file at 2am.

Windows-first, but it's plain Python + Tkinter so it runs on macOS and Linux too.

---

## How it works

1. **Zip folder** — where your downloaded build zips land, usually `Downloads`.
2. **Load Build** — picks the highest-numbered zip matching your pattern
   (`mysite-v*.zip` takes `v12` over `v11`, and `v100` over both) and unzips it into
   the **work folder**, keeping the zip's own structure. No extra subfolder is
   invented:

   | | |
   |---|---|
   | zip contains | `build/public/css/app.css` |
   | work folder | `C:\chat2ftp\mysite` |
   | result | `C:\chat2ftp\mysite\build\public\css\app.css` |

3. **Paste** the chat line into the text box. Backtick-quoted paths are picked up;
   trailing chatter like *"Build reads v12."* is ignored. The box isn't saved — it
   belongs to one build and is stale as soon as the next one arrives.
4. **Resolve** — each listed path is matched against the files that came out of *this
   zip*, by whole path suffix. So `public/css/app.css` finds
   `build/public/css/app.css` whether or not the zip has a wrapper folder — no mapping
   rules to configure. Anything it can't find is flagged `MISSING` rather than silently
   skipped.
5. **Upload** — each file goes to `remote root` + its path, creating remote folders as
   needed. Leave **remote root** empty to upload straight into the login directory.

The file list shows exactly where every file will land, and the confirmation dialog
lists the remote paths, before anything is sent.

### How the remote path is decided

**The remote path always mirrors where the file actually sits locally.** It is never
taken from the pasted text alone. Two rules enforce that:

- A listed path only matches a file whose local path *ends with the whole listed path*,
  so the remote path is always a real tail of the local one. There is no
  match-by-filename fallback — a bare `index.php` will not be matched against some
  `app/modules/index.php` and then uploaded to your web root.
- If a listed path is a bare filename but the file lives in a subfolder locally, it is
  refused with `NEEDS BASE` rather than guessed at.

**Local base** (optional, per project) is the folder inside the zip that corresponds to
your remote root. Set it and the mapping becomes exact and obvious:

| | |
|---|---|
| local base | `build/public` |
| file in zip | `build/public/css/app.css` |
| uploads to | `<remote root>/css/app.css` |

Files outside that base are flagged `OUTSIDE BASE` and skipped — handy when a zip
carries both server files and things that must never reach the server.

### Safety nets

- **Root-level writes are blocked by default.** A file landing directly in the remote
  root — `index.php` and friends, the files that serve your whole site — is refused with
  `ROOT BLOCKED` unless the project explicitly ticks *Allow writes to the remote root*.
  With it ticked you still get a warning listing each file and where it came from,
  defaulting to "no".
- **Backup before overwrite** (on by default). Each remote file is downloaded before it
  is replaced, into `<work folder>\_chat2ftp_backup\<timestamp>\`, mirroring the remote
  paths. If a build goes wrong, the previous version is sitting on your disk.
- **Never-upload list** per project, for files holding live keys.

---

## Telling your AI how to hand you builds

Chat2FTP works with whatever a chat assistant already produces, but two small habits
make it seamless. Paste something like this into your project instructions or at the
start of a session:

> I deploy with Chat2FTP. When you give me changed files:
> 1. Put them in a zip named `myproject-v<number>.zip`, with the number going up each build.
> 2. Inside the zip, keep each file at its path relative to the web root. A wrapper folder is fine.
> 3. End your message with a single line listing what changed, in backticks:
>    `Upload: `index.php`, `inc/config.php`, `public/css/app.css``

That's all it needs. The version number drives "Load Build" (highest wins, so
re-downloading an old zip can't overwrite new work), and the `Upload:` line drives what
actually gets sent.

**If you run several projects**, give each one its own zip name prefix — `myproject-v*.zip`,
`shop-v*.zip` — so each project's pattern only ever matches its own builds. Then make one
project per site in the app (below). If you only have one project, ignore all this: the
defaults do the simple thing.

---

## Projects

Each project keeps its own zip folder, work folder, filename pattern, server login,
remote root and never-upload list. Switch with the dropdown at the top; **New**,
**Duplicate**, **Rename** and **Delete** sit next to it. Switching projects clears the
loaded build, so you can't accidentally push one site's files to another.

Useful when you maintain several sites, or ship a web app and a desktop build out of the
same conversation.

---

## Never-upload list

Per project, a comma-separated list of paths that are refused even if they appear in the
zip and in the pasted line — for server-side files holding live keys, like
`inc/secrets.php` or an API credentials file. They show as `BLOCKED` and are skipped.
Worth setting once per project: it means a stray line in a chat message can't overwrite
your production keys.

---

## Install and run

```
pip install paramiko      # SFTP only; FTP and FTPS need nothing extra
python chat2ftp.py
```

Python 3.8+. Tkinter ships with the standard Windows and macOS installers; on Debian or
Ubuntu use `sudo apt install python3-tk`. On Windows you can also just double-click
`run_chat2ftp.bat`.

---

## Build a Windows exe

```
build_exe.bat
```

Installs PyInstaller and produces `dist\Chat2FTP.exe` — one file, no console window, no
Python needed on the target machine. Drop a `chat2ftp.ico` next to the spec before
building if you want a custom icon.

By hand:

```
py -m pip install pyinstaller paramiko
py -m PyInstaller --noconfirm --clean chat2ftp.spec
```

### The exe builds but does nothing when I run it

A windowed exe has nowhere to print an error, so a crash looks like nothing happening.
In order:

1. **Check the size** the build script prints. A real build is tens of megabytes; a few
   bytes means something overwrote the exe after PyInstaller made it. Open it in
   Notepad — if you see readable text, that's what happened. The usual culprit is a
   stray `>` in a batch file, which cmd treats as "redirect output into this filename"
   rather than as an arrow.
2. **Check `chat2ftp-crash.log`** next to the exe. Any unhandled error is written there
   and also shown in a dialog.
3. **Run `run_chat2ftp.bat`**, which starts the app straight from Python. If that works
   and the exe doesn't, the problem is the build or antivirus, not the app.
4. **Check your antivirus.** Unsigned single-file PyInstaller exes are sometimes
   quarantined on sight, silently. Look in Defender's protection history. UPX
   compression is off in the spec for this reason — turning it on makes false positives
   more likely.

---

## Settings

Everything on screen is written to `chat2ftp.ini` as you change it — there's no save
button. The file sits next to the script (or next to the exe); if that folder isn't
writable it falls back to `%APPDATA%\Chat2FTP\chat2ftp.ini`. The path in use is shown in
the status bar, and **Open Settings Folder** takes you there.

```ini
[general]
last_project = mysite

[project:mysite]
zip_dir = C:\Users\You\Downloads
work_dir = C:\chat2ftp\mysite
pattern = mysite-v*.zip
protocol = FTP
host = ftp.example.com
port = 21
user = myuser
password = secret
save_password = true
remote_root =
blocklist = inc/secrets.php
```

See `chat2ftp.example.ini` for a two-project example.

---

## Security

Passwords are stored in the ini in **plain text** when *Remember password* is ticked (it
is by default). `chat2ftp.ini` is in `.gitignore` — keep it that way, and untick the box
if the machine isn't yours. FTP sends credentials unencrypted; prefer SFTP or FTPS where
your host supports it.

---

## Releases

The repository holds source only. Built exes are attached to
[Releases](../../releases) — `dist/` and `build/` are gitignored so binaries never
end up in the git history.

To publish one: build it, then create a release tagged `v1.2` on GitHub and attach
`dist\Chat2FTP.exe` as an asset. Users who don't want to install Python download the exe
from there; everyone else clones and runs `chat2ftp.py`.

---

## Licence

MIT. See `LICENSE`.
