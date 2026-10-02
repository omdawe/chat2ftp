#!/usr/bin/env python3
"""
Chat2FTP - paste a chat message, upload the files it names.

Flow:
  1. Download the build zip from your AI chat into the ZIP FOLDER.
  2. "Load Build" -> finds the highest-numbered zip matching your filename pattern
     (mysite-v*.zip -> picks v12 over v11) and unzips it into the WORK FOLDER
     exactly as it sits in the zip. No extra subfolder.
       zip contains  build/public/css/app.css
       work folder   C:\\chat2ftp\\mysite
       result        C:\\chat2ftp\\mysite\\build\\public\\css\\app.css
  3. Paste the Upload line from the chat into the text box, e.g.
       Upload: `index.php`, `inc/config.php`, `public/css/app.css`
  4. "Resolve" -> matches each listed path against the files that came out of the zip,
     by path suffix, so a wrapper folder inside the zip does not matter.
  5. "Upload" -> sends each file to REMOTE ROOT + the listed path.
     Leave REMOTE ROOT empty to upload straight into the login directory:
       ftp://host/public/css/app.css

Each project keeps its own folders, pattern and server login. Pick a project from
the dropdown at the top; everything on screen is written to chat2ftp.ini as you
change it, under that project's own section.

The ini sits next to this script (or next to the exe). If that folder is not
writable it falls back to %APPDATA%\\Chat2FTP\\chat2ftp.ini. The path in use is
shown in the status bar.

Requires: paramiko (only for SFTP)   ->   pip install paramiko
FTP / FTPS use the standard library.

MIT licensed. https://github.com/YOURNAME/chat2ftp
"""

import os
import re
import sys
import time
import fnmatch
import zipfile
import ftplib
import posixpath
import threading
import traceback
import subprocess
import configparser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Chat2FTP"
APP_TAGLINE = "paste a chat message, upload the files it names"
APP_VERSION = "2.0"
INI_NAME = "chat2ftp.ini"

# A green accent for the two buttons that actually do something, and for the
# "Done." line in the log. tk.Button is used rather than ttk.Button because the
# native Windows ttk theme draws buttons from images and ignores a background.
GREEN = "#1e7e34"
GREEN_HOVER = "#19692c"
LOG_OK_BG = "#d7f3dd"
LOG_FAIL_BG = "#f8d7da"


def accent_button(parent, text, command):
    return tk.Button(parent, text=text, command=command,
                     bg=GREEN, fg="white",
                     activebackground=GREEN_HOVER, activeforeground="white",
                     disabledforeground="#bcd9c4",
                     relief="raised", bd=1, highlightthickness=0,
                     padx=12, pady=3, cursor="hand2")


GENERAL = "general"
PROJECT_PREFIX = "project:"
DEFAULT_PROJECT = "Default"

# Per project: paths that must NEVER be uploaded, however they are listed.
# Use it for server-side files holding live keys, e.g. "inc/secrets.php".
DEFAULT_BLOCKLIST = ""

# Every setting below is stored per project.
FIELDS = {
    "zip_dir":        os.path.join(os.path.expanduser("~"), "Downloads"),
    "work_dir":       os.path.join(os.path.expanduser("~"), "chat2ftp", "work"),
    "pattern":        "*.zip",
    "patterns":       "*.zip",
    "protocol":       "FTP",
    "host":           "",
    "port":           "21",
    "user":           "",
    "password":       "",
    "save_password":  "true",
    "local_base":     "",
    "remote_root":    "",
    "backup":         "true",
    "allow_root":     "false",
    "blocklist":      DEFAULT_BLOCKLIST,
}

# keys used by older builds -> current keys
RENAMED = {"downloads_dir": "zip_dir"}
OLD_SECTIONS = ("paths", "build", "server")

# several build patterns per project, kept on one ini line
PATTERN_SEP = "|"


def split_patterns(raw):
    out, seen = [], set()
    for item in (raw or "").split(PATTERN_SEP):
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def join_patterns(items):
    return PATTERN_SEP.join(items)


# ---------------------------------------------------------------- ini location

def app_dir():
    if getattr(sys, "frozen", False):          # PyInstaller exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".chat2ftp_write_test")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except Exception:
        return False


def pick_ini_path():
    """Next to the script/exe if we can write there, otherwise %APPDATA%\\Chat2FTP."""
    here = os.path.join(app_dir(), INI_NAME)
    if os.path.isfile(here) or writable(app_dir()):
        return here
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    fallback = os.path.join(appdata, APP_NAME)
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        pass
    return os.path.join(fallback, INI_NAME)


INI_FILE = pick_ini_path()


def as_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- settings store

class Store:
    """chat2ftp.ini with one section per project:

        [general]
        last_project = mysite

        [project:mysite]
        zip_dir = C:\\Users\\You\\Downloads
        ...
    """

    def __init__(self, path):
        self.path = path
        self.cp = configparser.ConfigParser(interpolation=None)
        try:
            self.cp.read(path, encoding="utf-8")
        except Exception:
            self.cp = configparser.ConfigParser(interpolation=None)
        self._migrate()

    # ---- structure

    def _sec(self, name):
        return PROJECT_PREFIX + name

    def _migrate(self):
        """Turn a pre-project ini into a single Default project."""
        if self.projects():
            return
        old = {}
        for sec in OLD_SECTIONS:
            if self.cp.has_section(sec):
                for k, v in self.cp.items(sec):
                    old[RENAMED.get(k, k)] = v
        for sec in OLD_SECTIONS:
            if self.cp.has_section(sec):
                self.cp.remove_section(sec)
        values = dict(FIELDS)
        values.update({k: v for k, v in old.items() if k in FIELDS})
        self.cp.add_section(self._sec(DEFAULT_PROJECT))
        for k, v in values.items():
            self.cp.set(self._sec(DEFAULT_PROJECT), k, str(v))
        self.set_current(DEFAULT_PROJECT)

    def projects(self):
        return sorted(s[len(PROJECT_PREFIX):] for s in self.cp.sections()
                      if s.startswith(PROJECT_PREFIX))

    def current(self):
        names = self.projects()
        if not names:
            self.create(DEFAULT_PROJECT)
            names = self.projects()
        if self.cp.has_option(GENERAL, "last_project"):
            name = self.cp.get(GENERAL, "last_project")
            if name in names:
                return name
        return names[0]

    def set_current(self, name):
        if not self.cp.has_section(GENERAL):
            self.cp.add_section(GENERAL)
        self.cp.set(GENERAL, "last_project", name)

    # ---- values

    def get(self, name):
        values = dict(FIELDS)
        sec = self._sec(name)
        if self.cp.has_section(sec):
            for k in FIELDS:
                if self.cp.has_option(sec, k):
                    values[k] = self.cp.get(sec, k)
            for old, new in RENAMED.items():
                if self.cp.has_option(sec, old) and not self.cp.has_option(sec, new):
                    values[new] = self.cp.get(sec, old)
        if not split_patterns(values.get("patterns")):
            values["patterns"] = values.get("pattern") or FIELDS["pattern"]
        return values

    def put(self, name, values):
        sec = self._sec(name)
        if not self.cp.has_section(sec):
            self.cp.add_section(sec)
        values = dict(values)
        if not as_bool(values.get("save_password", "true")):
            values["password"] = ""
        for k in FIELDS:
            v = str(values.get(k, FIELDS[k])).replace("\n", " ").replace("\r", " ")
            self.cp.set(sec, k, v)
        # clear out keys left behind by older versions (e.g. last_text)
        for opt in list(self.cp.options(sec)):
            if opt not in FIELDS:
                self.cp.remove_option(sec, opt)

    def create(self, name, copy_from=None):
        values = self.get(copy_from) if copy_from else dict(FIELDS)
        self.put(name, values)

    def rename(self, old, new):
        values = self.get(old)
        self.put(new, values)
        self.cp.remove_section(self._sec(old))

    def delete(self, name):
        self.cp.remove_section(self._sec(name))

    # ---- disk

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("; %s %s - %s\n" % (APP_NAME, APP_VERSION, APP_TAGLINE))
                self.cp.write(f)
            os.replace(tmp, self.path)
            return True, ""
        except Exception as e:
            return False, str(e)


# ---------------------------------------------------------------- parsing

PATH_IN_TICKS = re.compile(r"`([^`]+)`")
PATH_LOOSE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.[A-Za-z0-9]+")


def parse_paths(text):
    """Pull file paths out of a pasted line like:
       Upload: `a/b.php`, `c.json`, `CHANGES.txt`. Build reads v991."""
    hits = [h.strip() for h in PATH_IN_TICKS.findall(text)]
    if not hits:
        body = text
        m = re.search(r"upload\s*:?\s*(.*)", text, re.I | re.S)
        if m:
            body = m.group(1)
        hits = PATH_LOOSE.findall(body)

    out, seen = [], set()
    for h in hits:
        h = h.strip().strip("'\"").rstrip(".,;").replace("\\", "/").lstrip("./")
        if not h or "." not in os.path.basename(h):
            continue
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out


# ---------------------------------------------------------------- zip

NUM_RUN = re.compile(r"\d+")


def version_key(name):
    """Rank a filename by every number in it, highest wins.
       mysite-v991.zip -> (991,)   mysite-v1000.zip -> (1000,)"""
    return tuple(int(n) for n in NUM_RUN.findall(os.path.splitext(name)[0]))


def find_build(folder, pattern):
    """Highest-numbered file matching the wildcard pattern.
       Ties (or names with no numbers) fall back to newest modified time."""
    pattern = (pattern or "*.zip").strip()
    best, best_key = None, None
    try:
        names = os.listdir(folder)
    except Exception:
        return None
    for name in names:
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if not fnmatch.fnmatch(name.lower(), pattern.lower()):
            continue
        key = (version_key(name), os.path.getmtime(full))
        if best_key is None or key > best_key:
            best, best_key = full, key
    return best


def extract_zip(zip_path, work_dir):
    """Unzip straight into work_dir, keeping the zip's own folder structure.
       Existing files are overwritten; nothing else in work_dir is touched.
       Returns [(relative_path_in_zip, full_local_path)] for this zip only."""
    os.makedirs(work_dir, exist_ok=True)
    out = []
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work_dir)
        for info in z.infolist():
            if info.filename.endswith("/"):
                continue
            rel = info.filename.replace("\\", "/").lstrip("/")
            full = os.path.join(work_dir, *rel.split("/"))
            if os.path.isfile(full):
                out.append((rel, full))
    return out


def match_file(wanted, files):
    """Find the extracted file whose path ENDS WITH the whole wanted path.
       Returns (zip_relative_path, full_local_path, status).

       There is deliberately no match-by-filename fallback. Matching a bare
       "index.php" against some deeply nested index.php, then uploading it to
       whatever the chat line said, is how you overwrite a live site root."""
    w = wanted.lower().strip("/")
    cand = [(rel, full) for rel, full in files
            if rel.lower() == w or rel.lower().endswith("/" + w)]
    if not cand:
        return None, None, "MISSING"
    cand.sort(key=lambda rf: len(rf[0]))
    return cand[0][0], cand[0][1], "OK"


def zip_wrapper(files):
    """If every file in the zip sits under one common top folder, that folder is
       packaging, not structure. Returns it (without slash) or ""."""
    tops = set()
    for rel, _full in files:
        head = rel.split("/", 1)
        if len(head) == 1:
            return ""
        tops.add(head[0])
        if len(tops) > 1:
            return ""
    return tops.pop() if tops else ""


def below_wrapper(rel_in_zip, wrapper):
    if wrapper and rel_in_zip.lower().startswith(wrapper.lower() + "/"):
        return rel_in_zip[len(wrapper) + 1:]
    return rel_in_zip


def suggest_base(files):
    """Work out the folder inside the zip that corresponds to the remote root.

       Descends through packaging folders - ones that contain a single
       subfolder and no files of their own - and stops where the real
       structure starts. For a zip holding build/site/{public,admin,
       migrations}/... that yields "build/site"."""
    rels = [rel for rel, _full in files]
    if not rels:
        return ""
    parts = []
    while True:
        prefix = "/".join(parts)
        here = [r[len(prefix) + 1:] if prefix else r
                for r in rels
                if not prefix or r.lower().startswith(prefix.lower() + "/")]
        loose = [h for h in here if "/" not in h]
        subdirs = {h.split("/", 1)[0] for h in here if "/" in h}
        if loose or len(subdirs) != 1:
            return prefix
        parts.append(subdirs.pop())


def under_base(rel_in_zip, local_base):
    """Path of a file below the base folder, or None if it is not under it."""
    rel = rel_in_zip.replace("\\", "/").strip("/")
    base = (local_base or "").replace("\\", "/").strip("/")
    if not base:
        return rel
    if rel.lower().startswith(base.lower() + "/"):
        return rel[len(base) + 1:]
    return None


def glob_to_re(pattern):
    """Glob where * stops at a folder boundary, the way a shell behaves:
       public/*.php matches public/faq.php but NOT
       public/inc/money.php or admin/public/money.php.
       ** crosses folders when you want that."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.I)


def select_files(token, pairs):
    """Which files a listed token refers to. pairs is [(sub_path, full_local)],
       every sub_path already relative to the local base.

       A glob is anchored at the base, so it selects exactly the folder level
       you wrote. A plain name matches a whole path tail, and if that hits more
       than one file the caller refuses rather than guessing."""
    t = token.replace("\\", "/").strip("/")
    if "*" in t or "?" in t:
        rx = glob_to_re(t)
        return [p for p in pairs if rx.match(p[0])]
    tl = t.lower()
    return [p for p in pairs if p[0].lower() == tl or p[0].lower().endswith("/" + tl)]


def strip_base(rel_in_zip, local_base):
    """Path of a file relative to the local base folder, i.e. the part of the
       zip that corresponds to the remote root. Returns None if the file is
       not under the base at all."""
    rel = rel_in_zip.replace("\\", "/").strip("/")
    base = (local_base or "").replace("\\", "/").strip("/")
    if not base:
        return None
    if rel.lower() == base.lower():
        return None
    if rel.lower().startswith(base.lower() + "/"):
        return rel[len(base) + 1:]
    return None


def remote_path_for(remote_root, sub_path):
    """Empty root -> relative path, i.e. straight into the FTP login folder."""
    root = (remote_root or "").strip().replace("\\", "/").rstrip("/")
    sub = sub_path.replace("\\", "/").strip("/")
    return (root + "/" + sub) if root else sub


# ---------------------------------------------------------------- transfer

class Transfer:
    """Thin wrapper so SFTP and FTP look the same to the uploader."""

    def __init__(self, protocol, host, port, user, password, log):
        self.protocol = protocol.upper()
        self.log = log
        self.home = "."
        if self.protocol == "SFTP":
            try:
                import paramiko
            except ImportError:
                raise RuntimeError("paramiko is not installed. Run:  pip install paramiko")
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(hostname=host, port=int(port), username=user,
                                password=password, timeout=20, look_for_keys=False,
                                allow_agent=False)
            self.sftp = self.client.open_sftp()
            try:
                self.home = self.sftp.normalize(".")
            except Exception:
                pass
        else:
            self.ftp = ftplib.FTP_TLS() if self.protocol == "FTPS" else ftplib.FTP()
            self.ftp.connect(host, int(port), timeout=20)
            self.ftp.login(user, password)
            if self.protocol == "FTPS":
                self.ftp.prot_p()
            self.ftp.set_pasv(True)
            self.home = self.ftp.pwd()

    def upload(self, local_path, remote_path):
        """remote_path may be absolute (/a/b.php) or relative (a/b.php).
           Relative means: under the login directory."""
        remote_dir = posixpath.dirname(remote_path)
        if self.protocol == "SFTP":
            if remote_dir:
                self._sftp_makedirs(remote_dir)
            self.sftp.put(local_path, remote_path)
        else:
            self._ftp_cd(remote_dir)
            with open(local_path, "rb") as f:
                self.ftp.storbinary("STOR " + posixpath.basename(remote_path), f)

    def download(self, remote_path, local_path):
        """Pull the current remote file down. Returns False if it isn't there."""
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            if self.protocol == "SFTP":
                self.sftp.get(remote_path, local_path)
            else:
                self._ftp_cd(posixpath.dirname(remote_path))
                with open(local_path, "wb") as f:
                    self.ftp.retrbinary("RETR " + posixpath.basename(remote_path), f.write)
            return True
        except Exception:
            if os.path.isfile(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
            return False

    def _sftp_makedirs(self, path):
        cur = "/" if path.startswith("/") else "."
        for part in [p for p in path.split("/") if p]:
            cur = posixpath.join(cur, part)
            try:
                self.sftp.stat(cur)
            except IOError:
                self.sftp.mkdir(cur)
                self.log("  created remote folder %s" % cur)

    def _ftp_cd(self, path):
        self.ftp.cwd("/" if path.startswith("/") else self.home)
        for part in [p for p in path.split("/") if p]:
            try:
                self.ftp.cwd(part)
            except ftplib.error_perm:
                self.ftp.mkd(part)
                self.ftp.cwd(part)
                self.log("  created remote folder %s" % part)

    def close(self):
        try:
            if self.protocol == "SFTP":
                self.sftp.close()
                self.client.close()
            else:
                self.ftp.quit()
        except Exception:
            pass


# ---------------------------------------------------------------- gui

class Collapsible(ttk.Frame):
    """A titled section that folds away. Settings you set once and rarely look
       at live in these, out of the way of the panels you actually work in."""

    def __init__(self, master, title, opened=False, on_toggle=None):
        super().__init__(master)
        self.title_text = title
        self.opened = bool(opened)
        self.on_toggle = on_toggle
        self.header = ttk.Button(self, text="", command=self.toggle, style="Section.TButton")
        self.header.pack(fill="x")
        self.body = ttk.Frame(self)
        self._sync()

    def toggle(self):
        self.opened = not self.opened
        self._sync()
        if self.opened and self.on_toggle:
            self.on_toggle(self)

    def open(self):
        if not self.opened:
            self.toggle()

    def _sync(self):
        if self.opened:
            self.body.pack(fill="x", padx=10, pady=(4, 8))
            self.header.configure(text="\u25bc  %s" % self.title_text)
        else:
            self.body.forget()
            self.header.configure(text="\u25b6  %s" % self.title_text)


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.geometry("1000x860")
        self.minsize(820, 420)

        self.store = Store(INI_FILE)
        self.project = self.store.current()
        self.files = []          # (rel_in_zip, full_local) from the loaded zip only
        self.rows = []
        self.busy = False
        self._save_job = None
        self._loading = True

        self._build_ui()
        self.apply_values(self.store.get(self.project))
        self.show_base()
        self.refresh_projects()
        self._arm_autosave()
        self._loading = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._retitle()
        self.log("Settings file: %s" % INI_FILE)
        self.log("Project: %s" % self.project)

    # -------------------------------------------------------- ui build

    def _build_scroll_host(self):
        """Everything lives on a scrolling page, so a short window still
           reaches the sections at the bottom without being resized."""
        self.status = ttk.Label(self, text="", anchor="w", foreground="#444")
        self.status.pack(side="bottom", fill="x", padx=10, pady=(2, 6))

        host = ttk.Frame(self)
        host.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0,
                                takefocus=0)
        self.vsb = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.page = ttk.Frame(self.canvas)
        self._page_id = self.canvas.create_window((0, 0), window=self.page, anchor="nw")
        self.page.bind("<Configure>", self._page_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self._on_wheel)

    def _on_scroll_set(self, first, last):
        # hide the scrollbar when everything already fits
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.vsb.pack_forget()
            elif not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y", before=self.canvas)
        except Exception:
            pass
        self.vsb.set(first, last)

    def _page_resized(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, e):
        self.canvas.itemconfigure(self._page_id, width=e.width)

    def _on_wheel(self, event):
        # widgets with their own scrollbar keep the wheel for themselves
        w = getattr(event, "widget", None)
        for _ in range(12):
            if w is None:
                break
            if w in (getattr(self, "logbox", None), getattr(self, "tree", None),
                     getattr(self, "text", None)):
                return
            w = getattr(w, "master", None)
        num = getattr(event, "num", 0)
        delta = getattr(event, "delta", 0)
        step = -1 if (num == 4 or delta > 0) else 1
        try:
            self.canvas.yview_scroll(step, "units")
        except Exception:
            pass

    def scroll_into_view(self, widget):
        """Bring a widget into view after a section opens."""
        def _do():
            try:
                self._page_resized()
                top = widget.winfo_y()
                page_h = max(self.page.winfo_height(), 1)
                view_h = self.canvas.winfo_height()
                if top + widget.winfo_height() > self.canvas.canvasy(0) + view_h:
                    self.canvas.yview_moveto(max(0.0, min(1.0, top / float(page_h))))
            except Exception:
                pass
        self.after(50, _do)

    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}
        self.var = {}
        self._build_scroll_host()
        style = ttk.Style(self)
        try:
            style.configure("Section.TButton", anchor="w", padding=(8, 5))
        except Exception:
            pass

        # --- project
        box = ttk.LabelFrame(self.page, text="Project")
        box.pack(fill="x", padx=8, pady=(8, 4))
        bar = ttk.Frame(box)
        bar.pack(fill="x", padx=6, pady=6)
        self.project_var = tk.StringVar(value=self.project)
        self.project_cb = ttk.Combobox(bar, textvariable=self.project_var, width=34,
                                       state="readonly")
        self.project_cb.pack(side="left")
        self.project_cb.bind("<<ComboboxSelected>>", self.on_project_change)
        ttk.Button(bar, text="New", command=self.on_project_new).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Duplicate", command=self.on_project_duplicate).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Rename", command=self.on_project_rename).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Delete", command=self.on_project_delete).pack(side="left", padx=(4, 0))

        # --- build
        box = ttk.LabelFrame(self.page, text="Build")
        box.pack(fill="both", expand=False, padx=8, pady=4)

        bar = ttk.Frame(box)
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Label(bar, text="File pattern").pack(side="left")
        self.var["pattern"] = tk.StringVar()     # the one in use
        self.var["patterns"] = tk.StringVar()    # all of them, saved per project
        self.pattern_cb = ttk.Combobox(bar, textvariable=self.var["pattern"], width=28)
        self.pattern_cb.pack(side="left", padx=(6, 4))
        ttk.Button(bar, text="+", width=3, command=self.on_pattern_add).pack(side="left")
        ttk.Button(bar, text="\u2212", width=3, command=self.on_pattern_remove).pack(
            side="left", padx=(2, 12))
        ttk.Button(bar, text="Pick Zip...", command=self.on_pick_zip).pack(side="left")
        self.btn_load = accent_button(bar, "Load Build", self.on_load_build)
        self.btn_load.pack(side="left", padx=(6, 0))
        self.zip_label = ttk.Label(bar, text="no build loaded")
        self.zip_label.pack(side="left", padx=12)
        # the base decides every upload path, so it stays visible even while
        # the Server section is folded away
        self.base_label = ttk.Label(bar, text="", foreground="#666")
        self.base_label.pack(side="right", padx=8)

        ttk.Label(box, text="Paste the Upload line from chat:").pack(anchor="w", padx=6)
        self.text = tk.Text(box, height=5, wrap="word")
        self.text.pack(fill="x", padx=6, pady=(0, 6))

        bar = ttk.Frame(box)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(bar, text="Resolve", command=self.on_resolve).pack(side="left")
        self.btn_upload = accent_button(bar, "Upload", self.on_upload)
        self.btn_upload.pack(side="left", padx=(6, 0))

        # --- file list
        box = ttk.LabelFrame(self.page, text="Files")
        box.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("status", "local", "remote")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=8)
        self.tree.heading("status", text="Status")
        self.tree.heading("local", text="From (inside work folder)")
        self.tree.heading("remote", text="Uploads to")
        self.tree.column("status", width=110, anchor="w")
        self.tree.column("local", width=430, anchor="w")
        self.tree.column("remote", width=350, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.tree.configure(yscrollcommand=sb.set)

        # --- log
        box = ttk.LabelFrame(self.page, text="Log")
        box.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self.logbox = tk.Text(box, height=8, wrap="word", state="disabled")
        self.logbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.logbox.yview)
        sb.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.logbox.configure(yscrollcommand=sb.set)
        # tagging the trailing newline too makes the colour span the full width
        self.logbox.tag_configure("ok", background=LOG_OK_BG)
        self.logbox.tag_configure("fail", background=LOG_FAIL_BG)

        # --- folders (collapsed section, below the panels that matter)
        self.sec_folders = Collapsible(self.page, "Folders", opened=False,
                                       on_toggle=self.scroll_into_view)
        self.sec_folders.pack(fill="x", padx=8, pady=(2, 0))
        box = self.sec_folders.body
        self._folder_row(box, 0, "Zip folder (downloads)", "zip_dir")
        self._folder_row(box, 1, "Work folder (unzip target)", "work_dir")
        box.columnconfigure(1, weight=1)

        # --- server (collapsed section)
        self.sec_server = Collapsible(self.page, "Server", opened=False,
                                      on_toggle=self.scroll_into_view)
        self.sec_server.pack(fill="x", padx=8, pady=(2, 0))
        box = self.sec_server.body

        ttk.Label(box, text="Protocol").grid(row=0, column=0, sticky="w", **pad)
        self.var["protocol"] = tk.StringVar()
        cb = ttk.Combobox(box, textvariable=self.var["protocol"], width=8,
                          state="readonly", values=["SFTP", "FTP", "FTPS"])
        cb.grid(row=0, column=1, sticky="w", **pad)
        cb.bind("<<ComboboxSelected>>", self._on_protocol)

        ttk.Label(box, text="Host").grid(row=0, column=2, sticky="e", **pad)
        self.var["host"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["host"], width=38).grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(box, text="Port").grid(row=0, column=4, sticky="e", **pad)
        self.var["port"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["port"], width=6).grid(row=0, column=5, sticky="w", **pad)

        ttk.Label(box, text="User").grid(row=1, column=0, sticky="w", **pad)
        self.var["user"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["user"], width=22).grid(row=1, column=1, columnspan=2, sticky="we", **pad)

        ttk.Label(box, text="Password").grid(row=1, column=3, sticky="e", **pad)
        self.var["password"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["password"], width=22, show="*").grid(row=1, column=4, columnspan=2, sticky="we", **pad)

        self.var["save_password"] = tk.StringVar()
        ttk.Checkbutton(box, text="Remember password in ini", variable=self.var["save_password"],
                        onvalue="true", offvalue="false").grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(box, text="Local base").grid(row=3, column=0, sticky="w", **pad)
        self.var["local_base"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["local_base"]).grid(row=3, column=1, columnspan=5, sticky="we", **pad)
        ttk.Label(box, text="folder inside the zip that maps to the remote root - auto-detected on load  "
                            "(blank = use the paths from the pasted line)",
                  foreground="#666").grid(row=4, column=1, columnspan=5, sticky="w", padx=6)

        ttk.Label(box, text="Remote root").grid(row=5, column=0, sticky="w", **pad)
        self.var["remote_root"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["remote_root"]).grid(row=5, column=1, columnspan=5, sticky="we", **pad)
        ttk.Label(box, text="empty = upload straight into the login folder",
                  foreground="#666").grid(row=6, column=1, columnspan=5, sticky="w", padx=6)

        ttk.Label(box, text="Never upload").grid(row=7, column=0, sticky="w", **pad)
        self.var["blocklist"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.var["blocklist"]).grid(row=7, column=1, columnspan=5, sticky="we", **pad)

        self.var["backup"] = tk.StringVar()
        ttk.Checkbutton(box, text="Download each remote file before overwriting it (backup)",
                        variable=self.var["backup"], onvalue="true", offvalue="false").grid(
                        row=8, column=1, columnspan=5, sticky="w", **pad)

        self.var["allow_root"] = tk.StringVar()
        ttk.Checkbutton(box, text="Allow writes to the remote root itself (index.php and the like)",
                        variable=self.var["allow_root"], onvalue="true", offvalue="false").grid(
                        row=9, column=1, columnspan=5, sticky="w", **pad)

        bar = ttk.Frame(box)
        bar.grid(row=10, column=0, columnspan=6, sticky="w", **pad)
        ttk.Button(bar, text="Test Connection", command=self.on_test).pack(side="left")
        ttk.Button(bar, text="Open Settings Folder", command=self.on_open_ini).pack(side="left", padx=(6, 0))

        box.columnconfigure(3, weight=1)


        self.set_status("settings: %s" % INI_FILE)

    def _folder_row(self, parent, row, label, key):
        pad = {"padx": 6, "pady": 3}
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **pad)
        self.var[key] = tk.StringVar()
        ttk.Entry(parent, textvariable=self.var[key]).grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(parent, text="Browse...",
                   command=lambda k=key: self._browse(k)).grid(row=row, column=2, **pad)

    def _browse(self, key):
        d = filedialog.askdirectory(initialdir=self.var[key].get() or os.path.expanduser("~"))
        if d:
            self.var[key].set(os.path.normpath(d))

    def _on_protocol(self, _e=None):
        p = self.var["protocol"].get()
        cur = self.var["port"].get()
        if p == "SFTP" and cur in ("21", ""):
            self.var["port"].set("22")
        elif p in ("FTP", "FTPS") and cur in ("22", ""):
            self.var["port"].set("21")

    def _retitle(self):
        self.title("%s %s  -  %s" % (APP_NAME, APP_VERSION, self.project))

    # -------------------------------------------------------- values

    def collect(self):
        return {k: v.get() for k, v in self.var.items()}

    def apply_values(self, values):
        was = self._loading
        self._loading = True
        for k, v in self.var.items():
            v.set(values.get(k, FIELDS[k]))
        self.text.delete("1.0", "end")
        self._loading = was
        self.refresh_patterns()

    def refresh_patterns(self):
        items = split_patterns(self.var["patterns"].get())
        cur = self.var["pattern"].get().strip()
        if cur and cur.lower() not in [i.lower() for i in items]:
            items = items + [cur]          # shown, not saved until you add it
        self.pattern_cb.configure(values=items)

    def remember_pattern(self, pattern):
        """Keep a pattern in this project's list. Used by + and by a load that
           worked, so patterns you actually use end up in the dropdown."""
        pattern = (pattern or "").strip()
        if not pattern:
            return False
        items = split_patterns(self.var["patterns"].get())
        if pattern.lower() in [i.lower() for i in items]:
            return False
        items.append(pattern)
        self.var["patterns"].set(join_patterns(items))
        self.refresh_patterns()
        return True

    def on_pattern_add(self):
        pattern = self.var["pattern"].get().strip()
        if not pattern:
            self.say_info("Type a pattern first, e.g. myproject-v*.zip")
            return
        if self.remember_pattern(pattern):
            self.log("Added build pattern '%s' to project '%s'." % (pattern, self.project))
        else:
            self.log("Pattern '%s' is already in this project." % pattern)

    def on_pattern_remove(self):
        pattern = self.var["pattern"].get().strip()
        items = split_patterns(self.var["patterns"].get())
        keep = [i for i in items if i.lower() != pattern.lower()]
        if len(keep) == len(items):
            self.log("Pattern '%s' is not in this project's list." % pattern)
            return
        self.var["patterns"].set(join_patterns(keep))
        self.var["pattern"].set(keep[0] if keep else "")
        self.refresh_patterns()
        self.log("Removed build pattern '%s'." % pattern)

    def refresh_projects(self):
        names = self.store.projects()
        self.project_cb.configure(values=names)
        self.project_var.set(self.project)

    # -------------------------------------------------------- autosave

    def _arm_autosave(self):
        """Any change to any field writes the current project's section."""
        for v in self.var.values():
            v.trace_add("write", lambda *_a: self.queue_save())

    def queue_save(self):
        if self._loading:
            return
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(500, self.save_now)

    def save_now(self, quiet=True):
        self._save_job = None
        self.store.put(self.project, self.collect())
        self.store.set_current(self.project)
        ok, err = self.store.save()
        if ok:
            self.set_status("saved '%s' to %s" % (self.project, INI_FILE))
        else:
            self.set_status("CANNOT SAVE SETTINGS: %s" % err)
            if not quiet:
                self.say_error("Could not write settings:\n%s\n\n%s"
                                     % (INI_FILE, err))
        return ok

    def show_base(self):
        base = self.var["local_base"].get().strip()
        self.base_label.configure(text=("base: %s" % base) if base else "")

    def set_status(self, msg):
        try:
            self.status.configure(text=msg)
        except Exception:
            pass

    # -------------------------------------------------------- projects

    def _ask_name(self, title, initial=""):
        name = self.ask_text("%s\n\nProject name:" % title, initial)
        if name is None:
            return None
        name = name.strip()
        if not name:
            return None
        if ":" in name or "[" in name or "]" in name:
            self.say_error("Project names cannot contain : [ ]")
            return None
        return name

    def _switch_to(self, name):
        self.project = name
        self.apply_values(self.store.get(name))
        self.show_base()
        self.refresh_projects()
        self.store.set_current(name)
        self.store.save()
        self.files = []
        self.rows = []
        self.tree.delete(*self.tree.get_children())
        self.zip_label.configure(text="no build loaded")
        self._retitle()
        self.log("Project: %s" % name)

    def on_project_change(self, _e=None):
        chosen = self.project_var.get()
        if chosen == self.project:
            return
        self.save_now()                    # keep the project we are leaving
        self._switch_to(chosen)

    def on_project_new(self):
        name = self._ask_name("New project")
        if not name:
            return
        if name in self.store.projects():
            self.say_error("That project already exists.")
            return
        self.save_now()
        self.store.create(name)
        self._switch_to(name)

    def on_project_duplicate(self):
        name = self._ask_name("Duplicate project", self.project + " copy")
        if not name:
            return
        if name in self.store.projects():
            self.say_error("That project already exists.")
            return
        self.save_now()
        self.store.create(name, copy_from=self.project)
        self._switch_to(name)

    def on_project_rename(self):
        name = self._ask_name("Rename project", self.project)
        if not name or name == self.project:
            return
        if name in self.store.projects():
            self.say_error("That project already exists.")
            return
        self.save_now()
        self.store.rename(self.project, name)
        self._switch_to(name)

    def on_project_delete(self):
        if len(self.store.projects()) < 2:
            self.say_info("This is the only project.")
            return
        if not self.ask_yes_no("Delete project '%s'?" % self.project):
            return
        gone = self.project
        self.store.delete(gone)
        self.store.save()
        self.log("Deleted project %s" % gone)
        self._switch_to(self.store.projects()[0])

    # -------------------------------------------------------- helpers

    def log(self, msg, tag=None):
        stamp = time.strftime("%H:%M:%S")
        tags = (tag,) if tag else ()
        def _do():
            self.logbox.configure(state="normal")
            for i, line in enumerate((msg or "").split("\n")):
                # continuation lines line up under the first, not restamped
                self.logbox.insert("end",
                                   ("%s  " % stamp if i == 0 else " " * 10) + line + "\n",
                                   tags)
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
        self.after(0, _do)

    # Every dialog is given this window as its parent, so it opens centred over
    # the app instead of the middle of the screen.
    def _modal(self, message, detail=None, kind="info",
               buttons=("OK",), default=0, entry=None):
        """The one dialog this app uses - for telling you something, asking a
           yes/no, or taking a short bit of text.

           It is a Toplevel of our own rather than tkinter.messagebox, because
           the Windows native message box ignores its parent when deciding
           where to sit and lands in the middle of the screen. buttons[0] is
           the affirmative, buttons[-1] is cancel. Returns the pressed label,
           or the typed text when entry is given (None if cancelled)."""
        win = tk.Toplevel(self)
        win.title(APP_NAME)
        win.transient(self)
        win.resizable(False, False)
        out = {"value": buttons[-1] if entry is None else None}
        var = tk.StringVar(value=entry or "")

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 10))
        sign, colour = {
            "info":     ("\u2139", "#1a5fb4"),
            "error":    ("\u2715", "#c01c28"),
            "warn":     ("\u26a0", "#c64600"),
            "question": ("?",      "#1a5fb4"),
        }.get(kind, ("\u2139", "#1a5fb4"))
        tk.Label(body, text=sign, fg=colour,
                 font=("Segoe UI", 16, "bold")).pack(side="left", anchor="n", padx=(0, 12))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text=message, wraplength=440, justify="left").pack(anchor="w")

        if entry is not None:
            ent = ttk.Entry(right, textvariable=var, width=46)
            ent.pack(fill="x", pady=(10, 0))
            ent.selection_range(0, "end")
            ent.focus_set()

        if detail:
            lines = detail.split("\n")
            wrap = ttk.Frame(right)
            wrap.pack(fill="both", expand=True, pady=(10, 0))
            txt = tk.Text(wrap, height=min(12, max(2, len(lines))), width=56,
                          wrap="none", relief="flat", background="#f4f4f4",
                          borderwidth=0, highlightthickness=0)
            txt.insert("1.0", detail)
            txt.configure(state="disabled")
            txt.pack(side="left", fill="both", expand=True)
            if len(lines) > 12:
                sb = ttk.Scrollbar(wrap, orient="vertical", command=txt.yview)
                sb.pack(side="right", fill="y")
                txt.configure(yscrollcommand=sb.set)

        def finish(label):
            if entry is not None:
                out["value"] = var.get() if label == buttons[0] else None
            else:
                out["value"] = label
            win.destroy()

        row = ttk.Frame(win)
        row.pack(fill="x", padx=16, pady=(0, 14))
        for i in range(len(buttons) - 1, -1, -1):          # rightmost packed first
            label = buttons[i]
            # green marks the default only when the default is the affirmative;
            # on a warning the default is "No" and green would read as "go"
            if i == default == 0:
                b = accent_button(row, label, lambda l=label: finish(l))
            else:
                b = ttk.Button(row, text=label, command=lambda l=label: finish(l))
            b.pack(side="right", padx=(6, 0))
            if i == default and entry is None:
                b.focus_set()

        win.bind("<Return>", lambda _e: finish(buttons[default if entry is None else 0]))
        win.bind("<Escape>", lambda _e: finish(buttons[-1]))
        win.protocol("WM_DELETE_WINDOW", lambda: finish(buttons[-1]))
        self.place_over_window(win)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return out["value"]

    def place_over_window(self, win):
        """Centre a dialog on the app window, a little above the middle, and
           keep it on screen if the app is near an edge."""
        try:
            win.update_idletasks()
            self.update_idletasks()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            if pw <= 1 or ph <= 1:                 # window not mapped yet
                px, py = 0, 0
                pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 3)
            x = max(0, min(x, self.winfo_screenwidth() - w))
            y = max(0, min(y, self.winfo_screenheight() - h))
            win.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def say_error(self, msg, detail=None):
        self._modal(msg, detail, kind="error", buttons=("OK",))

    def say_info(self, msg, detail=None):
        self._modal(msg, detail, kind="info", buttons=("OK",))

    def ask_yes_no(self, msg, detail=None, danger=False):
        answer = self._modal(msg, detail,
                             kind="warn" if danger else "question",
                             buttons=("Yes", "No"), default=1 if danger else 0)
        return answer == "Yes"

    def ask_text(self, msg, initial=""):
        return self._modal(msg, kind="question", buttons=("OK", "Cancel"),
                           entry=initial)

    def blocked_set(self):
        raw = self.var["blocklist"].get()
        return {p.strip().strip("`'\"").replace("\\", "/").lstrip("./").lower()
                for p in re.split(r"[,;\n]", raw) if p.strip()}

    def target_label(self):
        s = self.collect()
        root = s["remote_root"].strip().strip("/")
        scheme = "sftp" if s["protocol"] == "SFTP" else "ftp"
        return "%s://%s/%s" % (scheme, s["host"], root + "/" if root else "")

    # -------------------------------------------------------- actions

    def on_open_ini(self):
        folder = os.path.dirname(INI_FILE)
        try:
            if os.name == "nt":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            self.say_info("Settings file:\n%s\n\n(%s)" % (INI_FILE, e))

    def on_test(self):
        if self.busy:
            return
        s = self.collect()
        if not s["host"] or not s["user"]:
            self.sec_server.open()
            self.say_error("Host and user are required.")
            return
        self.save_now(quiet=False)
        self.busy = True
        threading.Thread(target=self._test_worker, args=(s,), daemon=True).start()

    def _test_worker(self, s):
        conn = None
        try:
            self.log("Connecting %s to %s:%s ..." % (s["protocol"], s["host"], s["port"]))
            conn = Transfer(s["protocol"], s["host"], s["port"], s["user"], s["password"], self.log)
            self.log("Login folder: %s" % conn.home)
            self.log("Connection OK.")
        except Exception as e:
            self.log("Connection FAILED: %s" % e)
        finally:
            if conn:
                conn.close()
            self.busy = False

    def on_load_build(self):
        self.save_now(quiet=False)
        folder = self.var["zip_dir"].get()
        if not os.path.isdir(folder):
            self.sec_folders.open()
            self.say_error("Zip folder not found:\n%s" % folder)
            return
        pattern = self.var["pattern"].get().strip()
        z = find_build(folder, pattern)
        if not z:
            self.say_error("Nothing matching '%s' in:\n%s" % (pattern, folder))
            return
        if self.remember_pattern(pattern):
            self.log("Remembered build pattern '%s'." % pattern)
        self._load_zip(z)

    def on_pick_zip(self):
        z = filedialog.askopenfilename(
            initialdir=self.var["zip_dir"].get(),
            filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if z:
            self._load_zip(z)

    def _load_zip(self, zip_path):
        work = self.var["work_dir"].get().strip()
        if not work:
            self.sec_folders.open()
            self.say_error("Set a work folder first.")
            return
        try:
            self.files = extract_zip(zip_path, work)
        except Exception as e:
            self.say_error("Could not extract:\n%s" % e)
            return
        self.zip_label.configure(text=os.path.basename(zip_path))
        if not self.var["local_base"].get().strip():
            guess = suggest_base(self.files)
            self.var["local_base"].set(guess)
            self.log("Local base auto-detected as '%s' - every upload path is "
                     "relative to this." % guess)
        self.show_base()
        self.log("Unzipped %s  ->  %s  (%d files)"
                 % (os.path.basename(zip_path), work, len(self.files)))
        self.save_now()
        if self.text.get("1.0", "end").strip():
            self.on_resolve()

    def on_resolve(self):
        if not self.files:
            self.say_info("Load a build first.")
            return
        tokens = parse_paths(self.text.get("1.0", "end"))
        if not tokens:
            self.say_info("No file paths found in the pasted text.")
            return

        base = self.var["local_base"].get().strip()
        if not base:
            base = suggest_base(self.files)
            self.var["local_base"].set(base)
            self.log("Local base auto-detected as '%s'." % base)
        self.show_base()

        # Everything below the base, keyed by its path relative to the base.
        # THAT path is the upload destination - not anything from the pasted text.
        pairs = []
        for rel, full in self.files:
            sub = under_base(rel, base)
            if sub:
                pairs.append((sub, full))
        if not pairs:
            self.sec_server.open()
            self.say_error("Nothing in this zip sits under the local base '%s'.\n\n"
                           "Check the Local base field." % base)
            return

        root = self.var["remote_root"].get()
        blocked = self.blocked_set()
        allow_root = as_bool(self.var["allow_root"].get())

        self.rows = []
        for token in tokens:
            hits = select_files(token, pairs)

            if not hits:
                self.rows.append({"token": token, "sub": "", "local": None,
                                  "remote": "", "status": "MISSING"})
                continue

            if len(hits) > 1 and not ("*" in token or "?" in token):
                self.rows.append({"token": token, "sub": "", "local": None,
                                  "remote": "", "status": "AMBIGUOUS",
                                  "options": [h[0] for h in hits]})
                continue

            for sub, full in hits:
                remote = remote_path_for(root, sub)
                status = "OK"
                if "/" not in remote.strip("/") and not allow_root:
                    status = "ROOT BLOCKED"
                elif sub.lower() in blocked or token.lower() in blocked:
                    status = "BLOCKED"
                self.rows.append({"token": token, "sub": sub, "local": full,
                                  "remote": remote, "status": status})

        # a file listed twice (e.g. by name and by glob) is uploaded once
        seen, unique = set(), []
        for r in self.rows:
            key = (r["status"], r["remote"], r["sub"])
            if r["status"] == "OK" and r["remote"] in seen:
                continue
            if r["status"] == "OK":
                seen.add(r["remote"])
            unique.append(r)
        self.rows = unique

        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            self.tree.insert("", "end",
                             values=(r["status"],
                                     (base + "/" + r["sub"]) if r["sub"] else r["token"],
                                     r["remote"]))

        ok = sum(1 for r in self.rows if r["status"] == "OK")
        self.log("Base '%s'. Resolved %d file(s) from %d listed item(s). Target %s"
                 % (base, ok, len(tokens), self.target_label()))
        for r in self.rows:
            if r["status"] == "OK":
                continue
            self.log("  %s  %s" % (r["status"], r["token"]))
            if r["status"] == "AMBIGUOUS":
                for o in r.get("options", []):
                    self.log("     could be %s" % o)
                self.log("     list the full path to say which one you mean")
            elif r["status"] == "ROOT BLOCKED":
                self.log("     %s sits at the remote root. Tick 'Allow writes to the "
                         "remote root' only if you really mean it." % r["remote"])
            elif r["status"] == "MISSING":
                self.log("     nothing under '%s' matches that" % base)
        self.save_now()

    def root_level_warning(self, todo):
        """Files landing directly in the remote root, with nothing above them.
           This is what destroys a live site when a chat line names a bare
           filename, so it is never done without saying so out loud."""
        root = self.var["remote_root"].get().strip().strip("/")
        bare = [r for r in todo if "/" not in r["remote"].strip("/")]
        if not bare:
            return True
        where = ("/" + root) if root else "the login folder (your web root)"
        names = "\n".join("%s   <-  %s" % (r["remote"], r["sub"]) for r in bare)
        return self.ask_yes_no(
            "%d file(s) will be written straight into %s, overwriting whatever is "
            "there. Is that what you want?" % (len(bare), where),
            detail=names, danger=True)

    def on_upload(self):
        if self.busy:
            return
        todo = [r for r in self.rows if r["status"] == "OK"]
        if not todo:
            self.say_info("Nothing resolved to upload. Press Resolve first.")
            return
        s = self.collect()
        if not s["host"] or not s["user"]:
            self.sec_server.open()
            self.say_error("Host and user are required.")
            return

        listing = "\n".join(r["remote"] for r in todo)
        if not self.ask_yes_no(
                "Project '%s'\n\nUpload %d file(s) to %s ?"
                % (self.project, len(todo), self.target_label()),
                detail=listing):
            return
        if not self.root_level_warning(todo):
            return
        self.save_now()
        self.busy = True
        self.btn_upload.configure(state="disabled")
        threading.Thread(target=self._upload_worker, args=(todo, s), daemon=True).start()

    def _upload_worker(self, todo, s):
        import datetime
        conn = None
        sent = failed = saved = 0
        do_backup = as_bool(s.get("backup", "true"))
        backup_dir = os.path.join(s["work_dir"].strip() or os.path.expanduser("~"),
                                  "_chat2ftp_backup",
                                  datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        try:
            self.log("Connecting %s to %s:%s ..." % (s["protocol"], s["host"], s["port"]))
            conn = Transfer(s["protocol"], s["host"], s["port"], s["user"], s["password"], self.log)
            self.log("Connected. Login folder: %s" % conn.home)
            if do_backup:
                self.log("Backups of replaced files go to %s" % backup_dir)
            for r in todo:
                try:
                    if do_backup:
                        dest = os.path.join(backup_dir, *r["remote"].strip("/").split("/"))
                        if conn.download(r["remote"], dest):
                            saved += 1
                            self.log("     backed up existing %s" % r["remote"])
                    conn.upload(r["local"], r["remote"])
                    sent += 1
                    self.log("OK   %s" % r["remote"])
                except Exception as e:
                    failed += 1
                    self.log("FAIL %s  (%s)" % (r["remote"], e))
            self.log("Done. %d uploaded, %d failed, %d previous version(s) backed up."
                     % (sent, failed, saved),
                     tag="fail" if failed else "ok")
            if saved:
                self.log("Backups: %s" % backup_dir)
        except Exception as e:
            self.log("ERROR: %s" % e)
            self.log(traceback.format_exc().strip())
        finally:
            if conn:
                conn.close()
            self.after(0, self._upload_done)

    def _upload_done(self):
        self.busy = False
        self.btn_upload.configure(state="normal")

    def _on_close(self):
        self.save_now()
        self.destroy()


# ---------------------------------------------------------------- crash handling

def crash_log_path():
    folder = os.path.dirname(INI_FILE) or app_dir()
    return os.path.join(folder, "chat2ftp-crash.log")


def report_crash(exc_type, exc, tb):
    """A windowed exe shows nothing when it dies, so write it down and say so."""
    import datetime
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = crash_log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n===== %s  %s %s =====\n%s" % (stamp, APP_NAME, APP_VERSION, text))
    except Exception:
        path = "(could not write a log file)"
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("%s crashed" % APP_NAME,
                             "%s\n\nWritten to:\n%s" % (text[-1200:], path))
        root.destroy()
    except Exception:
        sys.stderr.write(text)


def main():
    sys.excepthook = report_crash
    app = App()
    # errors raised inside Tk callbacks land here instead of vanishing
    app.report_callback_exception = report_crash
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        report_crash(*sys.exc_info())
        sys.exit(1)
