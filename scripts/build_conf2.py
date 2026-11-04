#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apex Worker Toolkit — Tkinter GUI  (v3.2.1)
Tabs: Obfuscate · Deobfuscate · Deploy · Usage · D1 · Subdomain · Dashboard · History · Settings

v3.2.1 FIX: Login screen no longer closes the app on Pydroid.
  - Root is created and shown immediately.
  - Login / Setup are Frames inside a container, not Toplevels.
  - Screen switching uses pack_forget + pack, never destroy mid-event.
  - App class accepts a Frame (not Tk) so it never touches Tk-only APIs.
"""

import base64, csv, datetime as dt, gzip, hashlib, hmac, json, os, queue, re
import secrets, shutil, string, subprocess, sys, threading, time, traceback
import urllib.error, urllib.parse, urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False
    AESGCM = None

try:
    import qrcode
    HAS_QRCODE = True
except Exception:
    HAS_QRCODE = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False


APP_NAME      = "Apex Worker Toolkit"
APP_VERSION   = "3.2.1"
DEFAULT_KEY   = "ApexSecretKey2024-ChangeMe!"
MIN_LEN       = 6
SETTINGS_FILE = Path.home() / ".apex_obf.json"
HISTORY_FILE  = Path.home() / ".apex_obf_history.json"
BACKUP_DIR    = Path.home() / ".apex_obf" / "backups"
DELETED_BACKUPS_DIR = Path.home() / ".apex_obf" / "deleted_backups"
SCHEDULE_FILE = Path.home() / ".apex_obf_schedule.json"

PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16

CF_API        = "https://api.cloudflare.com/client/v4"
CF_GRAPHQL    = "https://api.cloudflare.com/client/v4/graphql"

DEFAULT_D1_BINDING = "netra"
DEFAULT_D1_NAME    = "netra"

COMPAT_DATE   = "2026-09-24"
COMPAT_FLAGS  = ["nodejs_compat"]
DAILY_LIMIT        = 100_000
D1_READS_LIMIT     = 5_000_000
D1_WRITES_LIMIT    = 100_000

DEPLOY_MAX_ATTEMPTS  = 4
DEPLOY_RETRY_BASE_MS = 1000


KEYWORDS_BEFORE_REGEX = {"return","typeof","instanceof","in","of","new",
    "delete","void","do","else","case","yield","await","throw"}

FROM_RE         = re.compile(r'(?<![.\w])from\s*([\'"])((?:\\.|(?!\1).)*)\1', re.DOTALL)
EXPORT_FROM_RE  = re.compile(r'(?<![.\w])export\s+(?:\*|\{[^}]*\})\s+from\s*([\'"])((?:\\.|(?!\1).)*)\1', re.DOTALL)
IMPORT_CALL_RE  = re.compile(r'\bimport\s*\(\s*([\'"])((?:\\.|(?!\1).)*)\1', re.DOTALL)
BARE_IMPORT_RE  = re.compile(r'(?<![\w.])import\s+([\'"])((?:\\.|(?!\1).)*)\1', re.DOTALL)
ES_MODULE_RE    = re.compile(
    r'(?:^|[\s;}])(?:import\s+(?:[\w*{]|[\'"])'
    r'|export\s+(?:default|\{|\*|const|let|var|function|class|async|\[)'
    r'|export\s*\{)', re.MULTILINE)

XORK_RE   = re.compile(r'const\s+__XORK\s*=\s*\[([0-9,\s]*)\]')
RAW_RE    = re.compile(r'const\s+__RAW\s*=\s*\[((?:"(?:[^"\\]|\\.)*"\s*,?\s*)+)\]', re.DOTALL)
RAW_ITEM  = re.compile(r'"((?:[^"\\]|\\.)*)"')
S_BRACKET = re.compile(r'\[__S\((\d+)\)\]')
S_PLAIN   = re.compile(r'__S\((\d+)\)')
S_MARKER  = "function __S(_i){return __STR[_i];}"

WHOLE_PAYLOAD_RE = re.compile(r'const\s+__P\s*=\s*"([0-9a-fA-F]+)"')
WHOLE_KEY_RE     = re.compile(r'const\s+__K\s*=\s*(\d+)')
WHOLE_IMPORT_RE  = re.compile(
    r'^\s*import\s+[\s\S]*?\s+from\s+([\'"])(?:\\.|(?!\1).)*\1\s*;?\s*$',
    re.MULTILINE)

SUBDOMAIN_RE = re.compile(r'^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?$')

BUNDLE_KEY_FILENAMES = {
    "PANEL_HTML_CONTENT": "panel.html",
    "LOGIN_HTML_CONTENT": "login.html",
    "ERROR_HTML_CONTENT": "error.html",
    "PROXY_IP_HTML_CONTENT": "proxyip.html",
    "ICON_CONTENT": "icon.b64",
    "SOURCE_CONTENT": "worker.source.js",
    "EMBEDED_SETTINGS": "settings.json"}


# ---------- password ----------
def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256": return False
        iters = int(iters_s); salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(h, expected)
    except Exception:
        return False


# ---------- history / backups ----------
def load_history() -> list:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception: return []


def save_history(entries: list):
    try:
        entries = entries[-2000:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception: pass


def append_history(action: str, detail: str = "", status: str = "info", account: str = ""):
    entries = load_history()
    entries.append({"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "action": action, "detail": detail[:300],
                    "status": status, "account": account[:60]})
    save_history(entries)


def do_backup_settings() -> Path | None:
    try:
        if not SETTINGS_FILE.exists(): return None
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dst = BACKUP_DIR / f"apex_obf_{ts}.json"
        shutil.copy2(SETTINGS_FILE, dst)
        for old in sorted(BACKUP_DIR.glob("apex_obf_*.json"), reverse=True)[10:]:
            try: old.unlink()
            except Exception: pass
        return dst
    except Exception: return None


def list_backups() -> list:
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(BACKUP_DIR.glob("apex_obf_*.json"), reverse=True)
    except Exception: return []


def encrypt_accounts_file(plain: Path, password: str, out: Path) -> None:
    if not HAS_CRYPTO: raise RuntimeError("cryptography not installed")
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 300_000, dklen=32)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plain.read_bytes(), None)
    out.write_bytes(b"APEXENC1" + salt + nonce + ct)


def decrypt_accounts_file(enc: Path, password: str) -> bytes:
    if not HAS_CRYPTO: raise RuntimeError("cryptography not installed")
    blob = enc.read_bytes()
    if not blob.startswith(b"APEXENC1"): raise RuntimeError("Invalid header")
    body = blob[8:]; salt, nonce, ct = body[:16], body[16:28], body[28:]
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 300_000, dklen=32)
    return AESGCM(key).decrypt(nonce, ct, None)


def make_qr_pil(text: str):
    if not HAS_QRCODE: raise RuntimeError("qrcode not installed")
    qr = qrcode.QRCode(version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6, border=2)
    qr.add_data(text); qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


# ---------- ScrollableFrame ----------
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, bg="#0f172a"):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, borderwidth=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = tk.Scrollbar(self, orient="vertical",
            command=self.canvas.yview, width=22, bg="#4ade80",
            troughcolor="#1e293b", activebackground="#22c55e", bd=0,
            relief="flat", elementborderwidth=2, highlightthickness=0)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = ttk.Frame(self.canvas, padding=8)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.up_btn = tk.Button(self, text="▲", command=self._scroll_up,
            bg="#2563eb", fg="white", activebackground="#1d4ed8",
            activeforeground="white", relief="flat", borderwidth=0,
            highlightthickness=0, font=("Segoe UI", 14, "bold"),
            padx=6, pady=2, cursor="hand2")
        self.dn_btn = tk.Button(self, text="▼", command=self._scroll_down,
            bg="#2563eb", fg="white", activebackground="#1d4ed8",
            activeforeground="white", relief="flat", borderwidth=0,
            highlightthickness=0, font=("Segoe UI", 14, "bold"),
            padx=6, pady=2, cursor="hand2")
        self.inner.bind("<Configure>", self._on_inner_config)
        self.canvas.bind("<Configure>", self._on_canvas_config)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_wheel, add="+")
        self.canvas.bind_all("<Button-5>", self._on_wheel, add="+")
        self.after(150, self._place_overlay_buttons)

    def _on_inner_config(self, event=None):
        try: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception: pass
    def _on_canvas_config(self, event):
        try:
            self.canvas.itemconfig(self._win_id, width=event.width)
            self._place_overlay_buttons()
        except Exception: pass
    def _place_overlay_buttons(self):
        try:
            self.up_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-30, y=6)
            self.dn_btn.place(relx=1.0, rely=1.0, anchor="se", x=-30, y=-6)
            self.up_btn.lift(); self.dn_btn.lift()
        except Exception: pass
    def _scroll_up(self):
        try: self.canvas.yview_scroll(-6, "units")
        except Exception: pass
    def _scroll_down(self):
        try: self.canvas.yview_scroll(6, "units")
        except Exception: pass
    def _on_wheel(self, event):
        try:
            if not self.winfo_ismapped(): return
        except Exception: return
        num = getattr(event, "num", 0); delta = getattr(event, "delta", 0)
        if num == 5 or delta < 0: self.canvas.yview_scroll(1, "units")
        elif num == 4 or delta > 0: self.canvas.yview_scroll(-1, "units")
    def _on_drag_start(self, event):
        self._drag_y0 = event.y
        try: self._drag_view0 = self.canvas.yview()[0]
        except Exception: self._drag_view0 = 0.0
    def _on_drag_move(self, event):
        if not hasattr(self, "_drag_y0"): return
        try:
            bbox = self.canvas.bbox("all")
            if not bbox: return
            total_h = max(1, bbox[3] - bbox[1])
            view_h = max(1, self.canvas.winfo_height())
            if total_h <= view_h: return
            dy = event.y - self._drag_y0
            new_pos = self._drag_view0 - (dy / total_h)
            self.canvas.yview_moveto(max(0.0, min(1.0, new_pos)))
        except Exception: pass
    def _on_drag_end(self, event):
        for a in ("_drag_y0", "_drag_view0"):
            if hasattr(self, a):
                try: delattr(self, a)
                except Exception: pass


# ---------- PasswordDialog (only for change / disable during runtime) ----------
class PasswordDialog(tk.Toplevel):
    def __init__(self, parent, mode="login", stored_hash=None,
                 title=None, subtitle=None):
        super().__init__(parent)
        self.mode = mode; self.stored_hash = stored_hash or ""
        self.result = None; self.attempts = 0
        self.max_attempts = 5 if mode == "login" else 999
        self._entries = {}
        self.title(title or {"login":"Unlock","setup":"Set master password",
                             "change":"Change master password"}.get(mode,"Password"))
        self.geometry("480x500"); self.minsize(420, 460)
        self.configure(bg="#0f172a")
        try:
            self.transient(parent); self.grab_set()
        except Exception: pass
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        foot = tk.Frame(self, bg="#0f172a"); foot.pack(side="bottom", fill="x",
                                                        padx=16, pady=(6,14))
        btn_text = {"login":"Unlock","setup":"Save","change":"Change"}.get(mode,"OK")
        self.ok_btn = tk.Button(foot, text=btn_text, command=self._submit_safe,
            bg="#2563eb", fg="white", activebackground="#1d4ed8",
            activeforeground="white", relief="flat",
            font=("Segoe UI",12,"bold"), padx=20, pady=12, cursor="hand2",
            borderwidth=0, highlightthickness=0)
        self.ok_btn.pack(side="right", fill="x", expand=True, padx=(4,0))
        tk.Button(foot, text="Cancel", command=self._cancel,
            bg="#1e293b", fg="#cbd5e1", activebackground="#334155",
            activeforeground="white", relief="flat",
            font=("Segoe UI",11), padx=20, pady=12, cursor="hand2",
            borderwidth=0, highlightthickness=0).pack(
            side="right", fill="x", expand=True, padx=(0,4))
        head = tk.Frame(self, bg="#1e293b", height=76)
        head.pack(side="top", fill="x"); head.pack_propagate(False)
        tk.Label(head, text=APP_NAME, bg="#1e293b", fg="#38bdf8",
                 font=("Segoe UI",13,"bold")).pack(anchor="w", padx=16, pady=(12,0))
        tk.Label(head, text=subtitle or "", bg="#1e293b", fg="#94a3b8",
                 font=("Segoe UI",9)).pack(anchor="w", padx=16)
        body = tk.Frame(self, bg="#0f172a")
        body.pack(side="top", fill="both", expand=True, padx=16, pady=(12,4))
        def add_field(key, label):
            tk.Label(body, text=label, bg="#0f172a", fg="#cbd5e1",
                     font=("Segoe UI",10,"bold"), anchor="w").pack(fill="x", pady=(6,2))
            var = tk.StringVar()
            e = tk.Entry(body, textvariable=var, show="*", bg="#1e293b",
                fg="#e2e8f0", insertbackground="#38bdf8", relief="flat",
                font=("Segoe UI",12), highlightthickness=2,
                highlightbackground="#334155", highlightcolor="#38bdf8")
            e.pack(fill="x", ipady=8)
            self._entries[key] = (e, var); return e
        first = None
        if mode == "login": first = add_field("password","Password")
        elif mode == "setup":
            first = add_field("password","New password")
            add_field("confirm","Confirm password")
        elif mode == "change":
            first = add_field("current","Current password")
            add_field("password","New password")
            add_field("confirm","Confirm new password")
        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg="#0f172a", fg="#f87171",
                 font=("Segoe UI",9,"bold"), wraplength=420, justify="left",
                 anchor="w").pack(fill="x", pady=(8,0))
        self.bind("<Return>", lambda e: self._submit_safe())
        self.bind("<Escape>", lambda e: self._cancel())
        self._center(parent)
        if first: self.after(150, first.focus_set)
        try: self.wait_window(self)
        except Exception: pass

    def _center(self, p):
        self.update_idletasks()
        try:
            px = p.winfo_rootx() + (p.winfo_width() - self.winfo_width())//2
            py = p.winfo_rooty() + (p.winfo_height() - self.winfo_height())//2
            self.geometry(f"+{max(0,px)}+{max(0,py)}")
        except Exception: pass
    def _set_status(self, msg, kind="error"):
        self.status_var.set(msg)
        try:
            self.status_var._root  # noqa
        except Exception: pass
    def _submit_safe(self):
        try: self._submit()
        except Exception as e:
            self.status_var.set(f"Error: {e}")
    def _submit(self):
        e = self._entries
        if self.mode == "login":
            pw = e["password"][1].get().strip()
            if not pw: self.status_var.set("Empty."); return
            if not verify_password(pw, self.stored_hash):
                self.attempts += 1
                if self.attempts >= self.max_attempts:
                    self.status_var.set("Too many."); self.after(900, self._cancel); return
                self.status_var.set(f"Incorrect ({self.attempts}/{self.max_attempts}).")
                e["password"][1].set(""); return
            self.result = pw; self.destroy()
        elif self.mode == "setup":
            pw = e["password"][1].get().strip(); cf = e["confirm"][1].get().strip()
            if len(pw) < 6: self.status_var.set("Min 6 chars."); return
            if pw != cf: self.status_var.set("No match."); return
            self.result = pw; self.destroy()
        elif self.mode == "change":
            cur = e["current"][1].get().strip()
            pw = e["password"][1].get().strip()
            cf = e["confirm"][1].get().strip()
            if not verify_password(cur, self.stored_hash):
                self.status_var.set("Current wrong."); return
            if len(pw) < 6: self.status_var.set("Min 6 chars."); return
            if pw != cf: self.status_var.set("No match."); return
            if pw == cur: self.status_var.set("Must differ."); return
            self.result = pw; self.destroy()
    def _cancel(self):
        self.result = None
        try: self.grab_release()
        except Exception: pass
        self.destroy()


# ---------- Command Palette ----------
class CommandPalette(tk.Toplevel):
    def __init__(self, parent, commands):
        super().__init__(parent)
        self.commands = commands; self.filtered = list(commands)
        self.title("Command Palette"); self.geometry("520x420")
        self.configure(bg="#0f172a")
        try:
            self.transient(parent); self.grab_set()
        except Exception: pass
        tk.Label(self, text="Type a command...", bg="#1e293b", fg="#38bdf8",
                 font=("Segoe UI",11,"bold"), anchor="w", padx=12, pady=8
                 ).pack(fill="x")
        self.q_var = tk.StringVar()
        self.q_var.trace_add("write", lambda *a: self._refresh())
        entry = tk.Entry(self, textvariable=self.q_var, bg="#1e293b",
            fg="#e2e8f0", insertbackground="#38bdf8", relief="flat",
            font=("Segoe UI",12), highlightthickness=2,
            highlightbackground="#334155", highlightcolor="#38bdf8")
        entry.pack(fill="x", padx=12, pady=8, ipady=6); entry.focus_set()
        self.lb = tk.Listbox(self, bg="#0b0f17", fg="#e2e8f0",
            selectbackground="#2563eb", selectforeground="white",
            relief="flat", highlightthickness=0,
            font=("Segoe UI",10), activestyle="none")
        self.lb.pack(fill="both", expand=True, padx=12, pady=(0,12))
        self.lb.bind("<Double-Button-1>", lambda e: self._run())
        entry.bind("<Return>", lambda e: self._run())
        entry.bind("<Down>", lambda e: self._move(1))
        entry.bind("<Up>", lambda e: self._move(-1))
        entry.bind("<Escape>", lambda e: self.destroy())
        self.lb.bind("<Return>", lambda e: self._run())
        self._refresh(); self._center(parent)
    def _center(self, p):
        self.update_idletasks()
        try:
            px = p.winfo_rootx() + (p.winfo_width() - self.winfo_width())//2
            py = p.winfo_rooty() + (p.winfo_height() - self.winfo_height())//2
            self.geometry(f"+{max(0,px)}+{max(0,py)}")
        except Exception: pass
    def _refresh(self):
        q = self.q_var.get().lower().strip()
        if not q: self.filtered = list(self.commands)
        else: self.filtered = [c for c in self.commands
                               if q in c[0].lower() or q in c[1].lower()]
        self.lb.delete(0, "end")
        for label, _kw, _cb in self.filtered: self.lb.insert("end", "  " + label)
        if self.filtered:
            self.lb.selection_clear(0, "end"); self.lb.selection_set(0)
    def _move(self, d):
        if not self.filtered: return
        cur = self.lb.curselection()
        i = (cur[0] + d) if cur else 0
        i = max(0, min(len(self.filtered) - 1, i))
        self.lb.selection_clear(0, "end"); self.lb.selection_set(i); self.lb.see(i)
    def _run(self):
        cur = self.lb.curselection()
        if not cur: return
        idx = cur[0]
        if idx >= len(self.filtered): return
        _label, _kw, cb = self.filtered[idx]
        self.destroy()
        try: cb()
        except Exception as e: print("Palette failed:", e)


# ---------- JS helpers ----------
def decode_js_escapes(raw: str) -> str:
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nc = raw[i + 1]
            if nc == "n": out.append("\n"); i += 2
            elif nc == "t": out.append("\t"); i += 2
            elif nc == "r": out.append("\r"); i += 2
            elif nc == "b": out.append("\b"); i += 2
            elif nc == "f": out.append("\f"); i += 2
            elif nc == "v": out.append("\v"); i += 2
            elif nc == "0": out.append("\0"); i += 2
            elif nc == "x" and i + 4 <= n:
                out.append(chr(int(raw[i+2:i+4], 16))); i += 4
            elif nc == "u":
                if i+2 < n and raw[i+2] == "{":
                    j = raw.find("}", i+3)
                    if j == -1: out.append(nc); i += 2
                    else: out.append(chr(int(raw[i+3:j], 16))); i = j + 1
                elif i + 6 <= n:
                    out.append(chr(int(raw[i+2:i+6], 16))); i += 6
                else: out.append(nc); i += 2
            elif nc == "\n": i += 2
            elif nc in ("'", '"', "`", "\\", "/"): out.append(nc); i += 2
            else: out.append(nc); i += 2
        else: out.append(c); i += 1
    return "".join(out)


def js_escape_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == "\\": out.append("\\\\")
        elif ch == '"': out.append('\\"')
        elif ch == "\n": out.append("\\n")
        elif ch == "\r": out.append("\\r")
        elif ch == "\t": out.append("\\t")
        elif ch == "\b": out.append("\\b")
        elif ch == "\f": out.append("\\f")
        elif ord(ch) < 0x20: out.append(f"\\u{ord(ch):04x}")
        else: out.append(ch)
    out.append('"'); return "".join(out)


def find_strings(src: str):
    results, i, n = [], 0, len(src)
    last_token = ""
    while i < n:
        c = src[i]
        if c == "/" and i+1 < n and src[i+1] == "/":
            j = src.find("\n", i); i = n if j == -1 else j+1; continue
        if c == "/" and i+1 < n and src[i+1] == "*":
            j = src.find("*/", i+2); i = n if j == -1 else j+2; continue
        if c in ("'", '"'):
            quote, j, buf, terminated = c, i+1, [], False
            while j < n:
                cj = src[j]
                if cj == "\\" and j+1 < n:
                    buf.append(cj); buf.append(src[j+1]); j += 2; continue
                if cj == quote: terminated = True; break
                if cj == "\n": break
                buf.append(cj); j += 1
            if terminated:
                results.append((i, j+1, "".join(buf), quote, False))
                i = j+1; last_token = quote; continue
            i += 1; continue
        if c == "`":
            j, buf, has_expr, terminated, depth = i+1, [], False, False, 0
            while j < n:
                cj = src[j]
                if cj == "\\" and j+1 < n:
                    buf.append(cj); buf.append(src[j+1]); j += 2; continue
                if cj == "`" and depth == 0: terminated = True; break
                if cj == "$" and j+1 < n and src[j+1] == "{":
                    has_expr = True; depth += 1
                    buf.append(cj); buf.append(src[j+1]); j += 2; continue
                if depth > 0 and cj == "{": depth += 1
                elif depth > 0 and cj == "}": depth -= 1
                buf.append(cj); j += 1
            if terminated:
                results.append((i, j+1, "".join(buf), "`", has_expr))
                i = j+1; last_token = "`"; continue
            i += 1; continue
        if c == "/":
            starts_regex = (last_token == "" or last_token in "(,=:[!&|?{};+-*%~^<>"
                            or last_token in KEYWORDS_BEFORE_REGEX)
            if starts_regex:
                j, in_class, terminated = i+1, False, False
                while j < n:
                    cj = src[j]
                    if cj == "\\" and j+1 < n: j += 2; continue
                    if cj == "[": in_class = True
                    elif cj == "]": in_class = False
                    elif cj == "/" and not in_class: terminated = True; break
                    elif cj == "\n": break
                    j += 1
                if terminated:
                    j += 1
                    while j < n and src[j].isalpha(): j += 1
                    i = j; last_token = "/"; continue
            i += 1; last_token = "/"; continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"): j += 1
            last_token = src[i:j]; i = j; continue
        if not c.isspace(): last_token = c
        i += 1
    return results


def is_key_context(src: str, start: int, end: int) -> bool:
    n = len(src); j = end
    while j < n and src[j] in " \t\r\n": j += 1
    if j >= n: return False
    i = start - 1
    while i >= 0 and src[i] in " \t\r\n": i -= 1
    if i < 0: return False
    preceded_by_open = src[i] in "{,"
    after = src[j]
    if after == ":":
        if j+1 < n and src[j+1] == ":": return False
        return preceded_by_open
    if after == "(": return preceded_by_open
    return False


def encode_value(value: str, key_bytes: bytes, use_gzip: bool) -> str:
    raw = value.encode("utf-8")
    if use_gzip: raw = gzip.compress(raw, compresslevel=9, mtime=0)
    out = bytearray(len(raw)); kb = key_bytes
    for i, b in enumerate(raw): out[i] = b ^ kb[i % len(kb)]
    return base64.b64encode(bytes(out)).decode("ascii")


def js_string_array(items):
    return "[" + ",".join('"' + s + '"' for s in items) + "]"


def make_header(encoded, key_bytes, use_gzip):
    key_arr = ",".join(str(b) for b in key_bytes)
    if use_gzip:
        return ("/* Apex obfuscated — gzip9+xor+b64 */\n"
                'import { gunzipSync as __gz } from "node:zlib";\n'
                f"const __XORK=[{key_arr}];\n"
                "const __RAW=" + js_string_array(encoded) + ";\n"
                "const __STR=[];\n{\n  const _TD=new TextDecoder();\n"
                "  for(let _i=0;_i<__RAW.length;_i++){\n"
                "    const _b=atob(__RAW[_i]);\n    const _a=new Uint8Array(_b.length);\n"
                "    for(let _j=0;_j<_b.length;_j++)_a[_j]=_b.charCodeAt(_j);\n"
                "    for(let _j=0;_j<_a.length;_j++)_a[_j]^=__XORK[_j%__XORK.length];\n"
                "    __STR[_i]=_TD.decode(__gz(_a));\n  }\n}\n"
                "function __S(_i){return __STR[_i];}\n")
    return ("/* Apex obfuscated — xor+b64 */\n"
            f"const __XORK=[{key_arr}];\n"
            "const __RAW=" + js_string_array(encoded) + ";\n"
            "const __STR=[];\n{\n  const _TD=new TextDecoder();\n"
            "  for(let _i=0;_i<__RAW.length;_i++){\n"
            "    const _b=atob(__RAW[_i]);\n    const _a=new Uint8Array(_b.length);\n"
            "    for(let _j=0;_j<_b.length;_j++)_a[_j]=_b.charCodeAt(_j);\n"
            "    for(let _j=0;_j<_a.length;_j++)_a[_j]^=__XORK[_j%__XORK.length];\n"
            "    __STR[_i]=_TD.decode(_a);\n  }\n}\n"
            "function __S(_i){return __STR[_i];}\n")


def _syntax_check(js_code: str):
    node = shutil.which("node")
    if not node: return None
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mjs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(js_code)
        r = subprocess.run([node, "--check", path], capture_output=True,
                           text=True, timeout=60)
        return (r.stderr or r.stdout or "").strip() if r.returncode != 0 else None
    except Exception as e:
        return f"(skipped: {e})"
    finally:
        try: os.unlink(path)
        except OSError: pass


def obfuscate(src, key, min_len=MIN_LEN, log=print, verify_syntax=True, use_gzip=True):
    if not key: raise ValueError("XOR key empty.")
    strings = find_strings(src)
    log(f"[*] Found {len(strings):,} strings")
    skip_ranges = []
    for rx in (FROM_RE, EXPORT_FROM_RE, IMPORT_CALL_RE, BARE_IMPORT_RE):
        for m in rx.finditer(src): skip_ranges.append((m.start(1), m.end(0)))
    def is_skipped(s, e):
        for a, b in skip_ranges:
            if a <= s and e <= b: return True
        return False
    table, seen, replacements = [], {}, []
    for (start, end, raw, quote, has_expr) in strings:
        if has_expr: continue
        if is_skipped(start, end): continue
        value = decode_js_escapes(raw)
        if len(value) < min_len: continue
        if value in ("use strict", "use asm"): continue
        idx = seen.get(value)
        if idx is None:
            idx = len(table); seen[value] = idx; table.append(value)
        if is_key_context(src, start, end): repl = f"[__S({idx})]"
        elif start > 0 and (src[start-1].isalnum() or src[start-1] in "_$"):
            repl = f" __S({idx})"
        else: repl = f"__S({idx})"
        replacements.append((start, end, repl))
    log(f"[*] Unique: {len(table):,}")
    out = src
    for start, end, repl in sorted(replacements, key=lambda x: x[0], reverse=True):
        out = out[:start] + repl + out[end:]
    key_bytes = key.encode("utf-8")
    encoded = [encode_value(v, key_bytes, use_gzip) for v in table]
    result = make_header(encoded, key_bytes, use_gzip) + out
    if verify_syntax:
        log("[*] node --check...")
        err = _syntax_check(result)
        if err:
            log("[!] Syntax FAILED")
            raise RuntimeError("Syntax:\n" + err)
        elif shutil.which("node"): log("[OK] passed")
    return result


def _parse_import_bindings(import_str: str):
    s = re.sub(r'//[^\n]*', '', import_str)
    s = re.sub(r'/\*[\s\S]*?\*/', '', s).strip().rstrip(';').strip()
    m = re.match(r'^import\s+([\s\S]+?)\s+from\s+["\'][^"\']*["\']$', s, re.DOTALL)
    if not m: return []
    body = m.group(1).strip()
    ns = re.match(r'^\*\s+as\s+([A-Za-z_$][\w$]*)$', body)
    if ns: return [{'name': ns.group(1), 'namespace': True}]
    if body.startswith('{'): default_part, named_part = None, body
    else:
        brace_idx = body.find('{')
        if brace_idx == -1:
            d = re.match(r'^([A-Za-z_$][\w$]*)$', body)
            return [{'name': d.group(1), 'default': True}] if d else []
        default_part = body[:brace_idx].rstrip(',').strip()
        named_part = body[brace_idx:]
    bindings = []
    if default_part:
        d_name = default_part.rstrip(',').strip()
        if d_name: bindings.append({'name': d_name, 'default': True})
    inner = named_part.strip().lstrip('{').rstrip('}')
    for item in inner.split(','):
        item = item.strip()
        if not item: continue
        if ' as ' in item:
            orig, alias = [p.strip() for p in re.split(r'\s+as\s+', item, 1)]
            bindings.append({'name': alias, 'original': orig})
        else: bindings.append({'name': item, 'original': item})
    return bindings


def obfuscate_whole(src: str, log=print, verify_syntax=True) -> str:
    log("[*] whole-code")
    log(f"    {len(src):,} chars")
    imports = [m.group(0).strip() for m in WHOLE_IMPORT_RE.finditer(src)]
    clean = WHOLE_IMPORT_RE.sub('', src)
    has_default = bool(re.search(r'\bexport\s+default\b', clean))
    clean = re.sub(r'\bexport\s+default\s+', 'const _0xApexModule = ', clean)
    clean = re.sub(r'\bexport\s+(const|let|var|function|class|async\s+function)\b', r'\1', clean)
    clean = re.sub(r'\bexport\s*\{[^}]*\}\s*;?', '', clean)
    clean = re.sub(r'\bexport\s*\*\s*from\s*["\'][^"\']*["\'];?', '', clean)
    if has_default: clean = clean.rstrip() + '\nreturn _0xApexModule;\n'
    seen, bindings = set(), []
    for imp in imports:
        for b in _parse_import_bindings(imp):
            if b['name'] not in seen:
                seen.add(b['name']); bindings.append(b['name'])
    xor_key = secrets.randbelow(80) + 64
    raw = clean.encode('utf-8')
    hex_out = ''.join(f'{b ^ xor_key:02x}' for b in raw)
    log(f"[*] {len(raw):,} bytes -> key {xor_key}")
    imports_block = ('\n'.join(imports) + '\n\n') if imports else ''
    if bindings:
        param_part = ', '.join(f'"{n}"' for n in bindings) + ', __CODE'
        arg_part = ', '.join(bindings)
    else:
        param_part = '__CODE'; arg_part = ''
    loader = (imports_block + "/* Apex whole-code */\n"
        + f'const __P = "{hex_out}";\n' + f'const __K = {xor_key};\n'
        + "const __B = new Uint8Array((__P.match(/../g) || []).map(x => parseInt(x, 16) ^ __K));\n"
        + "const __CODE = new TextDecoder().decode(__B);\n"
        + f"const __MOD = new Function({param_part})({arg_part});\n"
        + "export default __MOD;\n")
    if verify_syntax:
        err = _syntax_check(loader)
        if err: raise RuntimeError("Loader syntax:\n" + err)
        elif shutil.which("node"): log("[OK] Loader passed")
    return loader


def _deobf_format_apex(src, log, verify_syntax):
    m = XORK_RE.search(src)
    if not m: raise ValueError("No __XORK.")
    xor_key = bytes(int(x.strip()) for x in m.group(1).split(",") if x.strip().isdigit())
    if not xor_key: raise ValueError("__XORK empty.")
    m = RAW_RE.search(src)
    if not m: raise ValueError("No __RAW.")
    encoded = [mm.group(1) for mm in RAW_ITEM.finditer(m.group(1))]
    table = []
    for i, b64 in enumerate(encoded):
        try: data = base64.b64decode(b64, validate=False)
        except Exception as e: raise ValueError(f"#{i}: {e}")
        dec = bytes(b ^ xor_key[j % len(xor_key)] for j, b in enumerate(data))
        try: table.append(gzip.decompress(dec).decode("utf-8"))
        except Exception:
            try: table.append(dec.decode("utf-8"))
            except Exception as e: raise ValueError(f"#{i}: {e}")
    log(f"[*] Decoded {len(table):,}")
    pos = src.find(S_MARKER)
    if pos < 0: raise ValueError("Marker not found.")
    body_start = pos + len(S_MARKER)
    if body_start < len(src) and src[body_start] == "\n": body_start += 1
    body = src[body_start:]
    def _sub(mm):
        idx = int(mm.group(1))
        if idx >= len(table): raise ValueError(f"__S({idx}) out of range")
        return js_escape_string(table[idx])
    body, n1 = S_BRACKET.subn(_sub, body)
    body, n2 = S_PLAIN.subn(_sub, body)
    log(f"[*] Replaced {n1+n2:,}")
    return body


def _deobf_format_whole(src, log, verify_syntax):
    m = WHOLE_PAYLOAD_RE.search(src)
    if not m: raise ValueError('No __P.')
    hex_str = m.group(1)
    m2 = WHOLE_KEY_RE.search(src)
    if not m2: raise ValueError("No __K.")
    key = int(m2.group(1))
    raw = bytes(b ^ key for b in bytes.fromhex(hex_str))
    decoded = raw.decode("utf-8")
    imports = [mm.group(0).strip() for mm in WHOLE_IMPORT_RE.finditer(src)]
    body = re.sub(r'\bconst\s+_0xApexModule\s*=\s*', 'export default ', decoded)
    body = re.sub(r'\nreturn\s+_0xApexModule\s*;?\s*$', '\n', body)
    header = ('\n'.join(imports) + '\n\n') if imports else ''
    return header + body


def _deobf_format_bundle(src, log, output_dir):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for key in BUNDLE_KEY_FILENAMES:
        patterns = [
            rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*"(?:data:[^";]*?,)?((?:[A-Za-z0-9+/=\r\n]|\\(?:n|r|t)){{8,}})"',
            rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*\'(?:data:[^\';]*?,)?((?:[A-Za-z0-9+/=\r\n]|\\(?:n|r|t)){{8,}})\'',
            rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*`(?:data:[^`;]*?,)?((?:[A-Za-z0-9+/=\r\n]|\\(?:n|r|t)){{8,}})`']
        for pat in patterns:
            m = re.search(pat, src, re.DOTALL)
            if m:
                raw = m.group(1).replace("\\n","\n").replace("\\r","\r").replace("\\t","\t")
                val = re.sub(r'\s+', '', raw)
                if val: results[key] = val; break
    if not results: raise ValueError("No bundled keys.")
    written = 0
    for key, b64 in results.items():
        if key == "ICON_CONTENT":
            (output_dir / BUNDLE_KEY_FILENAMES[key]).write_text(b64, encoding="utf-8")
            written += 1; continue
        try: raw = base64.b64decode(b64)
        except Exception: continue
        try: decoded = gzip.decompress(raw).decode("utf-8")
        except Exception:
            try: decoded = raw.decode("utf-8")
            except Exception: continue
        (output_dir / BUNDLE_KEY_FILENAMES[key]).write_text(decoded, encoding="utf-8")
        written += 1
    if written == 0: raise ValueError("Nothing decoded.")
    return output_dir


def deobfuscate(src, log=print, verify_syntax=True, output_path=None):
    has_whole = bool(WHOLE_PAYLOAD_RE.search(src) and WHOLE_KEY_RE.search(src))
    has_apex = "__XORK" in src and "__RAW" in src and "__S(" in src
    has_bundle = any(k in src for k in BUNDLE_KEY_FILENAMES)
    if has_whole and not has_apex:
        return {"format":"whole","text":_deobf_format_whole(src,log,verify_syntax),"folder":None}
    if has_apex:
        return {"format":"apex","text":_deobf_format_apex(src,log,verify_syntax),"folder":None}
    if has_bundle:
        if not output_path: raise ValueError("Bundle needs output path.")
        out_dir = Path(str(output_path) + ".assets")
        folder = _deobf_format_bundle(src, log, out_dir)
        return {"format":"bundle","text":f"Extracted to: {folder}","folder":folder}
    raise ValueError("Unknown format.")


# ---------- Cloudflare API ----------
def _cf_request(url, token, method="GET", json_body=None,
                raw_body=None, raw_ct=None, timeout=120):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if json_body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(json_body).encode("utf-8")
    elif raw_body is not None:
        req.add_header("Content-Type", raw_ct or "application/octet-stream")
        req.data = raw_body
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try: return resp.status, json.loads(body)
            except Exception: return resp.status, {"success": False, "raw": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try: return e.code, json.loads(body)
        except Exception: return e.code, {"success": False, "raw": body}
    except Exception as e:
        return 0, {"success": False, "raw": str(e)}


def _cf_err(data):
    try: return (data.get("errors") or [{}])[0].get("message") or json.dumps(data)[:300]
    except Exception: return str(data)[:300]


def _acc_fields(account):
    return {"label": account.get("name") or account.get("workerName")
                     or account.get("worker_name") or "(unnamed)",
            "account_id": account.get("accountId") or account.get("account_id")
                          or account.get("accountID"),
            "api_token": account.get("apiToken") or account.get("api_token")
                         or account.get("token"),
            "worker_name": account.get("workerName") or account.get("worker_name")}


def cf_ensure_d1(account_id, api_token, db_name, cache, log=print):
    key = f"d1:{account_id}:{db_name}"
    if key in cache: return cache[key]
    url = (f"{CF_API}/accounts/{account_id}/d1/database"
           f"?name={urllib.parse.quote(db_name)}&per_page=100")
    st, data = _cf_request(url, api_token)
    if not data.get("success"): raise RuntimeError(f"D1 list: {_cf_err(data)}")
    found = next((d for d in (data.get("result") or []) if d.get("name") == db_name), None)
    if found and found.get("uuid"):
        res = {"id": found["uuid"], "name": found.get("name", db_name), "created": False}
        cache[key] = res; return res
    st, data = _cf_request(f"{CF_API}/accounts/{account_id}/d1/database",
                           api_token, method="POST", json_body={"name": db_name})
    if not data.get("success") or not (data.get("result") or {}).get("uuid"):
        raise RuntimeError(f"D1 create: {_cf_err(data)}")
    r = data["result"]
    res = {"id": r["uuid"], "name": r.get("name", db_name), "created": True}
    cache[key] = res; log(f"   [D1] Created '{db_name}'")
    return res


def cf_get_cron(account):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        raise ValueError("missing fields")
    url = f"{CF_API}/accounts/{f['account_id']}/workers/scripts/{f['worker_name']}/schedules"
    st, data = _cf_request(url, f["api_token"])
    if not data.get("success"): raise RuntimeError(_cf_err(data))
    return [s.get("cron") for s in (data.get("result") or {}).get("schedules", [])]


def cf_set_cron(account, crons):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        raise ValueError("missing fields")
    url = f"{CF_API}/accounts/{f['account_id']}/workers/scripts/{f['worker_name']}/schedules"
    st, data = _cf_request(url, f["api_token"], method="PUT",
                           json_body=[{"cron": c} for c in crons])
    if not data.get("success"): raise RuntimeError(_cf_err(data))
    return True


def cf_remove_cron(account): return cf_set_cron(account, [])


def cf_delete_worker(account):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        raise ValueError("missing fields")
    url = f"{CF_API}/accounts/{f['account_id']}/workers/scripts/{f['worker_name']}"
    st, data = _cf_request(url, f["api_token"], method="DELETE")
    if st == 404: return False
    if not data.get("success"):
        msg = _cf_err(data) or f"HTTP {st}"
        if "not found" in msg.lower() or "10007" in msg: return False
        raise RuntimeError(msg)
    return True


def cf_get_workers_subdomain(account):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"]):
        raise ValueError("missing account_id or api_token")
    url = f"{CF_API}/accounts/{f['account_id']}/workers/subdomain"
    st, data = _cf_request(url, f["api_token"])
    if not data.get("success"): raise RuntimeError(_cf_err(data) or f"HTTP {st}")
    return str((data.get("result") or {}).get("subdomain") or "")


def cf_set_workers_subdomain(account, new_subdomain):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"]):
        raise ValueError("missing account_id or api_token")
    base = f"{CF_API}/accounts/{f['account_id']}/workers/subdomain"
    errors = []
    st, data = _cf_request(base, f["api_token"], method="PATCH",
                           json_body={"subdomain": new_subdomain})
    if data.get("success"):
        return str((data.get("result") or {}).get("subdomain") or new_subdomain)
    errors.append(f"PATCH {st}: {_cf_err(data)}")
    st, data = _cf_request(base, f["api_token"], method="PUT",
                           json_body={"subdomain": new_subdomain})
    if data.get("success"):
        return str((data.get("result") or {}).get("subdomain") or new_subdomain)
    errors.append(f"PUT {st}: {_cf_err(data)}")
    joined = " | ".join(errors)
    if "already has" in joined.lower():
        raise RuntimeError("CF blocks API updates. Change from dashboard.")
    raise RuntimeError(joined)


def _enable_workers_dev_route(account):
    f = _acc_fields(account)
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        return (False, "missing fields")
    url = (f"{CF_API}/accounts/{f['account_id']}"
           f"/workers/scripts/{f['worker_name']}/subdomain")
    body = {"enabled": True, "previews_enabled": False}
    st, data = _cf_request(url, f["api_token"], method="PUT", json_body=body)
    if data.get("success"): return (True, "workers.dev enabled")
    errors = [f"PUT {st}: {_cf_err(data)}"]
    st2, data2 = _cf_request(url, f["api_token"], method="POST", json_body=body)
    if data2.get("success"): return (True, "workers.dev enabled (POST)")
    errors.append(f"POST {st2}: {_cf_err(data2)}")
    return (False, " | ".join(errors))


def cf_repair_workers_dev(account, d1_cache, log=print, d1_binding=None, d1_name=None):
    f = _acc_fields(account); label = f["label"]
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        return {"ok": False, "label": label, "msg": "missing fields"}
    ok, msg = _enable_workers_dev_route(account)
    return {"ok": ok, "label": label, "msg": msg}


def cf_get_d1_usage(account, log=print):
    f = _acc_fields(account); label = f["label"]
    if not (f["account_id"] and f["api_token"]):
        return {"label": label, "ok": False, "msg": "missing fields"}
    now = dt.datetime.now(dt.timezone.utc)
    start_d = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
    end_d = now.strftime("%Y-%m-%d")
    query = f"""
    {{
      viewer {{
        accounts(filter: {{ accountTag: "{f['account_id']}" }}) {{
          d1AnalyticsAdaptiveGroups(
            limit: 10000
            filter: {{ date_geq: "{start_d}" date_leq: "{end_d}" }}
          ) {{ sum {{ readQueries writeQueries rowsRead rowsWritten }} }}
        }}
      }}
    }}""".strip()
    req = urllib.request.Request(CF_GRAPHQL, method="POST",
        headers={"Authorization": f"Bearer {f['api_token']}",
                 "Content-Type": "application/json"},
        data=json.dumps({"query": query}).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            msg = (j.get("errors") or [{}])[0].get("message") or body[:200]
        except Exception: msg = body[:200]
        return {"label": label, "ok": False, "msg": f"HTTP {e.code}: {msg}"}
    except Exception as e:
        return {"label": label, "ok": False, "msg": str(e)}
    if data.get("errors"):
        return {"label": label, "ok": False,
                "msg": data["errors"][0].get("message", "graphql error")}
    try: rows = data["data"]["viewer"]["accounts"][0]["d1AnalyticsAdaptiveGroups"]
    except Exception: rows = []
    rq = wq = rr = rw = 0
    for r in rows:
        s = r.get("sum") or {}
        rq += int(s.get("readQueries", 0) or 0)
        wq += int(s.get("writeQueries", 0) or 0)
        rr += int(s.get("rowsRead", 0) or 0)
        rw += int(s.get("rowsWritten", 0) or 0)
    return {"label": label, "ok": True, "readQueries": rq, "writeQueries": wq,
            "rowsRead": rr, "rowsWritten": rw}


def cf_ping_worker(account, subdomain):
    f = _acc_fields(account)
    if not f["worker_name"]: raise ValueError("missing worker_name")
    if not subdomain: raise ValueError("missing subdomain")
    url = f"https://{f['worker_name']}.{subdomain}.workers.dev/"
    t0 = time.time()
    req = urllib.request.Request(url, method="GET",
                                  headers={"User-Agent": "ApexToolkit/3.2.1"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            ms = int((time.time()-t0)*1000)
            return {"ok": True, "status": r.status, "ms": ms, "url": url}
    except urllib.error.HTTPError as e:
        ms = int((time.time()-t0)*1000)
        return {"ok": e.code < 500, "status": e.code, "ms": ms, "url": url}
    except Exception as e:
        ms = int((time.time()-t0)*1000)
        return {"ok": False, "status": 0, "ms": ms, "url": url, "err": str(e)[:80]}


def _is_es_module(src):
    if ES_MODULE_RE.search(src): return True
    if re.search(r'\bexport\s+(?:default|\{|\*|\w)', src): return True
    if re.search(r'\bimport\s+[^\n]*\s+from\s+[\'"]', src): return True
    return False


def _build_multipart(fields, files):
    boundary = "----ApexBoundary" + secrets.token_hex(16)
    body = bytearray()
    for k, v in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.extend(v.encode("utf-8") if isinstance(v, str) else v)
        body.extend(b"\r\n")
    for name, filename, ctype, data in files:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        body.extend(f"Content-Type: {ctype}\r\n\r\n".encode())
        body.extend(data if isinstance(data, bytes) else data.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def cf_deploy_worker(account, script_content, d1_cache, log=print,
                     clear_cron=False, force_module=True,
                     d1_binding=None, d1_name=None, force_reset=False):
    d1_binding = (d1_binding or DEFAULT_D1_BINDING).strip() or DEFAULT_D1_BINDING
    d1_name    = (d1_name    or DEFAULT_D1_NAME).strip() or DEFAULT_D1_NAME
    f = _acc_fields(account); label = f["label"]
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        return {"ok": False, "label": label, "msg": "missing fields"}
    if force_reset:
        try:
            was = cf_delete_worker(account)
            log(f"   [RS] deleted" if was else f"   [RS] gone")
        except Exception as e:
            log(f"   [!] delete failed: {e}")
    try:
        d1 = cf_ensure_d1(f["account_id"], f["api_token"], d1_name, d1_cache, log=log)
    except Exception as e:
        return {"ok": False, "label": label, "msg": f"D1: {e}"}
    is_module = force_module or _is_es_module(script_content)
    metadata = {"compatibility_date": COMPAT_DATE,
                "compatibility_flags": COMPAT_FLAGS,
                "bindings": [{"type":"d1","name":d1_binding,"id":d1["id"]}]}
    if is_module:
        metadata["main_module"] = "worker.js"
        file_name = "worker.js"; ct = "application/javascript+module"
    else:
        metadata["body_part"] = "script"
        file_name = "script"; ct = "application/javascript"
    body, content_type = _build_multipart(
        fields={"metadata": json.dumps(metadata)},
        files=[(file_name, "worker.js", ct, script_content.encode("utf-8"))])
    url = f"{CF_API}/accounts/{f['account_id']}/workers/scripts/{f['worker_name']}"
    st, data = _cf_request(url, f["api_token"], method="PUT",
                           raw_body=body, raw_ct=content_type, timeout=180)
    if not data.get("success"):
        return {"ok": False, "label": label, "msg": _cf_err(data) or f"HTTP {st}"}
    enable_ok, enable_msg = _enable_workers_dev_route(account)
    msg = f"{d1_binding}->{d1['name']}" + (" (new)" if d1["created"] else "")
    if is_module: msg += " | ESM"
    if force_reset: msg += " | reset"
    if clear_cron:
        try: cf_remove_cron(account); msg += " | cron cleared"
        except Exception as e: msg += f" | cron: {e}"
    if enable_ok: msg += " | workers.dev ON"
    else: msg += f" | route FAIL ({enable_msg[:30]})"
    return {"ok": True, "label": label, "msg": msg}


def _should_retry_deploy(msg):
    m = str(msg).strip()
    if not m: return True
    if m.startswith("missing fields"): return False
    if m.startswith("Uncaught"): return False
    if "SyntaxError" in m: return False
    if "is not defined" in m: return False
    mm = re.match(r'HTTP (\d{3})', m)
    if mm:
        code = int(mm.group(1))
        return code in (408, 429) or code >= 500
    if m.startswith("D1:"): return True
    low = m.lower()
    for needle in ("timeout","urlerror","connection","reset","network",
                   "timed out","ssl","temporar","exception:","eof","unreachable"):
        if needle in low: return True
    return True


def _today_utc_range():
    now = dt.datetime.now(dt.timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start.isoformat().replace("+00:00","Z"),
            now.isoformat().replace("+00:00","Z"))


def cf_get_usage(account, log=print):
    f = _acc_fields(account); label = f["label"]
    if not (f["account_id"] and f["api_token"] and f["worker_name"]):
        return {"label": label, "ok": False, "msg": "missing fields"}
    start_iso, end_iso = _today_utc_range()
    query = f"""
    {{
      viewer {{
        accounts(filter: {{ accountTag: "{f['account_id']}" }}) {{
          workersInvocationsAdaptive(
            limit: 10000
            filter: {{
              scriptName: "{f['worker_name']}"
              datetime_geq: "{start_iso}"
              datetime_leq: "{end_iso}"
            }}
          ) {{ sum {{ requests errors }} }}
        }}
      }}
    }}""".strip()
    req = urllib.request.Request(CF_GRAPHQL, method="POST",
        headers={"Authorization": f"Bearer {f['api_token']}",
                 "Content-Type": "application/json"},
        data=json.dumps({"query": query}).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            msg = (j.get("errors") or [{}])[0].get("message") or body[:200]
        except Exception: msg = body[:200]
        return {"label": label, "ok": False, "msg": f"HTTP {e.code}: {msg}"}
    except Exception as e:
        return {"label": label, "ok": False, "msg": str(e)}
    if data.get("errors"):
        return {"label": label, "ok": False,
                "msg": data["errors"][0].get("message", "graphql error")}
    try: rows = data["data"]["viewer"]["accounts"][0]["workersInvocationsAdaptive"]
    except Exception: rows = []
    return {"label": label, "ok": True,
            "requests": int(sum((r.get("sum") or {}).get("requests", 0) for r in rows)),
            "errors": int(sum((r.get("sum") or {}).get("errors", 0) for r in rows))}


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f: return json.load(f) or {}
    except Exception: return {}


def save_settings(d):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception: pass


def load_schedule():
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f: return json.load(f) or {}
    except Exception: return {}


def save_schedule(d):
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception: pass


def open_in_file_manager(path):
    try:
        if sys.platform.startswith("win"): os.startfile(str(path))
        elif sys.platform == "darwin": subprocess.Popen(["open", str(path)])
        else: subprocess.Popen(["xdg-open", str(path)])
    except Exception as e: print("fm error:", e)


# ============================================================
#  App — now takes a Frame (not Tk root) as parent
# ============================================================
class App:
    def __init__(self, parent):
        self.root = parent
        try: self.tk = parent.winfo_toplevel()
        except Exception: self.tk = parent

        self.log_q = queue.Queue()
        self.obf_thread = self.deobf_thread = self.deploy_thread = None
        self.usage_thread = self.cron_thread = None
        self.sd_thread = self.reset_thread = self.ping_thread = None
        self.repair_thread = self.d1_usage_thread = None
        self.deploy_cancel = threading.Event()
        self.usage_cancel = threading.Event()
        self.sd_cancel = threading.Event()
        self.d1_usage_cancel = threading.Event()
        self.last_output = None
        self.last_deobf_output = None
        self.accounts = []
        self.sd_accounts = []
        self.d1_analytics_accounts = []
        self.d1_cache = {}
        self._qr_photo = None

        try:
            self.tk.title(f"{APP_NAME} v{APP_VERSION}")
            self.tk.geometry("1080x1000")
            self.tk.minsize(360, 500)
            self.tk.configure(bg="#0f172a")
        except Exception: pass

        self._apply_theme()
        self._build_topbar()
        self._build_widgets()
        self._build_statusbar()
        self._bind_shortcuts()
        self._restore_settings()
        self._poll_log()
        self._tick_clock()
        self._start_scheduler()
        try: self.tk.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception: pass

    def _on_close(self):
        try:
            p = do_backup_settings()
            if p: print("Backup:", p)
        except Exception: pass
        try: self.tk.destroy()
        except Exception: pass

    def _apply_theme(self):
        s = ttk.Style()
        try:
            if "clam" in s.theme_names(): s.theme_use("clam")
        except Exception: pass
        try:
            s.configure("TNotebook", background="#0f172a", borderwidth=0,
                        tabmargins=(2, 4, 2, 0))
            s.configure("TNotebook.Tab", background="#1e293b",
                        foreground="#94a3b8", padding=(8, 8),
                        font=("Segoe UI", 9, "bold"))
            s.map("TNotebook.Tab",
                  background=[("selected", "#0f172a")],
                  foreground=[("selected", "#38bdf8")])
            s.configure("TFrame", background="#0f172a")
            s.configure("TLabelframe", background="#0f172a",
                        foreground="#cbd5e1", borderwidth=1, relief="solid")
            s.configure("TLabelframe.Label", background="#0f172a",
                        foreground="#38bdf8", font=("Segoe UI", 9, "bold"))
            s.configure("TLabel", background="#0f172a", foreground="#cbd5e1",
                        font=("Segoe UI", 9))
            s.configure("TCheckbutton", background="#0f172a",
                        foreground="#cbd5e1", font=("Segoe UI", 9))
            s.configure("TRadiobutton", background="#0f172a",
                        foreground="#cbd5e1", font=("Segoe UI", 9))
            s.configure("TEntry", fieldbackground="#1e293b",
                        foreground="#e2e8f0", bordercolor="#334155", padding=4)
            s.configure("TSpinbox", fieldbackground="#1e293b",
                        foreground="#e2e8f0", bordercolor="#334155", padding=3)
            s.configure("TButton", padding=(8, 6), font=("Segoe UI", 9))
            s.configure("TProgressbar", background="#38bdf8",
                        troughcolor="#1e293b", borderwidth=0)
            s.configure("Treeview", background="#1e293b",
                        fieldbackground="#1e293b", foreground="#e2e8f0",
                        rowheight=26, font=("Segoe UI", 9))
            s.configure("Treeview.Heading", background="#334155",
                        foreground="#e2e8f0", font=("Segoe UI", 9, "bold"))
        except Exception: pass
        s.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 8))
        s.configure("Go.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 10))
        s.configure("GoU.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#a33", padding=(10, 10))
        s.configure("Go2.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#0b3", padding=(10, 10))
        s.configure("Go3.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#036", padding=(10, 10))
        s.configure("Go4.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#603", padding=(10, 10))
        s.configure("Reset.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#7c2d12", padding=(10, 10))
        s.configure("Repair.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#0369a1", padding=(10, 10))
        s.configure("D1.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#7c3aed", padding=(10, 10))
        s.configure("Warn.TButton", font=("Segoe UI", 9, "bold"),
                    foreground="#a33", padding=(10, 8))
        s.configure("Small.TButton", font=("Segoe UI", 8), padding=(6, 4))
        s.configure("Hint.TLabel", foreground="#64748b", font=("Segoe UI", 8))
        s.configure("D1Lbl.TLabelframe.Label", foreground="#036")
        s.configure("SD.TLabelframe.Label", foreground="#603")
        s.configure("Set.TLabelframe.Label", foreground="#a78bfa")
        s.configure("Reset.TLabelframe.Label", foreground="#c2410c")
        s.configure("Repair.TLabelframe.Label", foreground="#0ea5e9")
        s.configure("Dash.TLabelframe.Label", foreground="#22c55e")
        s.configure("D1Box.TLabelframe.Label", foreground="#7c3aed")

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg="#1e293b"); bar.pack(fill="x", side="top")
        left = tk.Frame(bar, bg="#1e293b")
        left.pack(side="left", fill="x", padx=(10, 4), pady=8)
        tk.Label(left, text="Apex", bg="#1e293b", fg="#38bdf8",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(left, text="Toolkit", bg="#1e293b", fg="#f1f5f9",
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=(4,0))
        tk.Label(left, text=f"v{APP_VERSION}", bg="#1e293b", fg="#64748b",
                 font=("Segoe UI", 8)).pack(side="left", padx=(6,0))
        right = tk.Frame(bar, bg="#1e293b")
        right.pack(side="right", padx=(4, 10), pady=8)
        def mk(text, cmd, bg="#334155", abg="#475569"):
            tk.Button(right, text=text, command=cmd, bg=bg, fg="#e2e8f0",
                      activebackground=abg, activeforeground="white",
                      relief="flat", font=("Segoe UI", 9, "bold"),
                      padx=10, pady=6, cursor="hand2", borderwidth=0,
                      highlightthickness=0).pack(side="left", padx=(3,0))
        self._quick_btn = tk.Button(right, text="Quick ▾",
            command=self._show_quick_menu, bg="#0ea5e9", fg="white",
            activebackground="#0284c7", activeforeground="white",
            relief="flat", font=("Segoe UI", 9, "bold"), padx=10, pady=6,
            cursor="hand2", borderwidth=0, highlightthickness=0)
        self._quick_btn.pack(side="left", padx=(3,0))
        mk("Cmd", self._open_palette, bg="#6366f1", abg="#4f46e5")
        mk("Set", self._goto_settings, bg="#2563eb", abg="#1d4ed8")
        mk("Lock", self._lock_now, bg="#7c3aed", abg="#6d28d9")

    def _show_quick_menu(self):
        menu = tk.Menu(self.tk, tearoff=0, bg="#1e293b", fg="#e2e8f0",
                       activebackground="#334155", activeforeground="white")
        menu.add_command(label="Ping all workers", command=self._run_ping_all)
        menu.add_command(label="Refresh usage", command=self._run_usage)
        menu.add_command(label="Refresh D1", command=self._run_d1_usage)
        menu.add_command(label="Refresh dashboard", command=self._refresh_dashboard)
        menu.add_separator()
        menu.add_command(label="Deploy (last worker)", command=self._run_deploy)
        menu.add_command(label="Remove cron (all)", command=self._run_remove_cron)
        menu.add_separator()
        menu.add_command(label="Enable workers.dev (all)",
                         command=self._run_enable_route_all)
        menu.add_command(label="Reset all workers (DELETE)",
                         command=self._run_force_reset_all)
        menu.add_separator()
        menu.add_command(label="Command palette (Ctrl+K)",
                         command=self._open_palette)
        try:
            x = self._quick_btn.winfo_rootx()
            y = self._quick_btn.winfo_rooty() + self._quick_btn.winfo_height()
            menu.tk_popup(x, y)
        except Exception: pass
        finally:
            try: menu.grab_release()
            except Exception: pass

    def _goto_settings(self):
        try: self.nb.select(self.settings_tab)
        except Exception: pass

    def _build_widgets(self):
        outer = ttk.Frame(self.root, padding=4)
        outer.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(outer); self.nb.pack(fill="both", expand=True)
        self.obf_tab = ttk.Frame(self.nb); self.deobf_tab = ttk.Frame(self.nb)
        self.deploy_tab = ttk.Frame(self.nb); self.usage_tab = ttk.Frame(self.nb)
        self.d1_tab = ttk.Frame(self.nb); self.sd_tab = ttk.Frame(self.nb)
        self.dash_tab = ttk.Frame(self.nb); self.hist_tab = ttk.Frame(self.nb)
        self.settings_tab = ttk.Frame(self.nb)
        self.nb.add(self.obf_tab, text=" Obf ")
        self.nb.add(self.deobf_tab, text=" Deob ")
        self.nb.add(self.deploy_tab, text=" Deploy ")
        self.nb.add(self.usage_tab, text=" Use ")
        self.nb.add(self.d1_tab, text=" D1 ")
        self.nb.add(self.sd_tab, text=" Sub ")
        self.nb.add(self.dash_tab, text=" Dash ")
        self.nb.add(self.hist_tab, text=" Hist ")
        self.nb.add(self.settings_tab, text=" Set ")
        self.obf_sf = ScrollableFrame(self.obf_tab)
        self.deobf_sf = ScrollableFrame(self.deobf_tab)
        self.deploy_sf = ScrollableFrame(self.deploy_tab)
        self.usage_sf = ScrollableFrame(self.usage_tab)
        self.d1_sf = ScrollableFrame(self.d1_tab)
        self.sd_sf = ScrollableFrame(self.sd_tab)
        self.dash_sf = ScrollableFrame(self.dash_tab)
        self.hist_sf = ScrollableFrame(self.hist_tab)
        self.settings_sf = ScrollableFrame(self.settings_tab)
        for sf in (self.obf_sf, self.deobf_sf, self.deploy_sf, self.usage_sf,
                   self.d1_sf, self.sd_sf, self.dash_sf, self.hist_sf, self.settings_sf):
            sf.pack(fill="both", expand=True)
        self._build_obfuscate_tab(self.obf_sf.inner)
        self._build_deobfuscate_tab(self.deobf_sf.inner)
        self._build_deploy_tab(self.deploy_sf.inner)
        self._build_usage_tab(self.usage_sf.inner)
        self._build_d1_tab(self.d1_sf.inner)
        self._build_subdomain_tab(self.sd_sf.inner)
        self._build_dashboard_tab(self.dash_sf.inner)
        self._build_history_tab(self.hist_sf.inner)
        self._build_settings_tab(self.settings_sf.inner)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg="#1e293b", height=26)
        bar.pack(fill="x", side="bottom"); bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(bar, textvariable=self.status_var, bg="#1e293b",
                 fg="#e2e8f0", font=("Segoe UI", 9), anchor="w", padx=10
                 ).pack(side="left", fill="x", expand=True)
        self.clock_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.clock_var, bg="#1e293b", fg="#94a3b8",
                 font=("Consolas", 9), padx=10).pack(side="right")
        self.sec_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.sec_var, bg="#1e293b", fg="#22c55e",
                 font=("Segoe UI", 9, "bold"), padx=10).pack(side="right")
        self.d1_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.d1_var, bg="#1e293b", fg="#38bdf8",
                 font=("Consolas", 9), padx=10).pack(side="right")

    def _tick_clock(self):
        try:
            self.clock_var.set(dt.datetime.now().strftime("%H:%M:%S"))
            self._refresh_status_segments()
        except Exception: pass
        self.root.after(1000, self._tick_clock)

    def _refresh_status_segments(self):
        try: self.d1_var.set(f"D1:{self.d1_binding_var.get()}/{self.d1_name_var.get()}")
        except Exception: self.d1_var.set("D1: --")
        try:
            s = load_settings()
            self.sec_var.set("Lock:ON" if s.get("password_hash") else "Lock:OFF")
        except Exception: self.sec_var.set("")

    def _attach_log_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0)
        def copy_all():
            self.root.clipboard_clear()
            self.root.clipboard_append(widget.get("1.0", "end-1c"))
        def clear():
            widget.config(state="normal"); widget.delete("1.0", "end")
            widget.config(state="disabled")
        menu.add_command(label="Copy all", command=copy_all)
        menu.add_command(label="Clear", command=clear)
        def show(e):
            try: menu.tk_popup(e.x_root, e.y_root)
            finally: menu.grab_release()
        widget.bind("<Button-3>", show); widget.bind("<Button-2>", show)
        widget.bind("<Control-Button-1>", show)

    def _make_log_box(self, parent, height=8):
        frame = ttk.LabelFrame(parent, text=" Log ", padding=(4, 4))
        head = ttk.Frame(frame); head.pack(side="bottom", fill="x", pady=(4, 0))
        txt = tk.Text(frame, height=height, wrap="word", state="disabled",
                      bg="#0b0f17", fg="#d6e2f5", insertbackground="#d6e2f5",
                      relief="flat", borderwidth=0, font=("Consolas", 9))
        sb = ttk.Scrollbar(frame, command=txt.yview)
        sb.pack(side="right", fill="y", before=head)
        txt.pack(side="left", fill="both", expand=True, before=head)
        txt.config(yscrollcommand=sb.set)
        ttk.Button(head, text="Copy", command=lambda: self._copy_log(txt),
                   style="Small.TButton").pack(side="right")
        ttk.Button(head, text="Clear", command=lambda: self._clear_log(txt),
                   style="Small.TButton").pack(side="right", padx=(0, 4))
        for tag, color in (("info","#8fa3c7"),("ok","#4ade80"),
                           ("error","#f87171"),("header","#22d3ee"),
                           ("warn","#fbbf24")):
            txt.tag_configure(tag, foreground=color,
                              font=("Consolas",9,"bold") if tag=="header"
                              else ("Consolas",9))
        self._attach_log_menu(txt); return frame, txt

    def _copy_log(self, widget):
        content = widget.get("1.0", "end-1c")
        self.root.clipboard_clear(); self.root.clipboard_append(content)
        self.status_var.set(f"Copied {len(content):,} chars.")

    def _clear_log(self, widget):
        widget.config(state="normal"); widget.delete("1.0", "end")
        widget.config(state="disabled")

    def _banner(self, parent, title, subtitle, accent="#38bdf8"):
        banner = tk.Frame(parent, bg="#1e293b"); banner.pack(fill="x", pady=(0, 8))
        tk.Frame(banner, bg=accent, width=4).pack(side="left", fill="y")
        inner = tk.Frame(banner, bg="#1e293b")
        inner.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        tk.Label(inner, text=title, bg="#1e293b", fg=accent,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(inner, text=subtitle, bg="#1e293b", fg="#94a3b8",
                 font=("Segoe UI", 8), justify="left", anchor="w",
                 wraplength=440).pack(fill="x")

    def _lbl_entry(self, parent, label, var, show=None):
        row = tk.Frame(parent, bg="#0f172a"); row.pack(fill="x", pady=(2, 6))
        ttk.Label(row, text=label).pack(anchor="w")
        e = ttk.Entry(row, textvariable=var, show=show) if show else ttk.Entry(row, textvariable=var)
        e.pack(fill="x", ipady=4); return e

    # --- tabs build ---
    def _build_obfuscate_tab(self, parent):
        self._banner(parent, "Obfuscate", "Bundle + obfuscate.", accent="#38bdf8")
        files = ttk.LabelFrame(parent, text=" Files ", padding=8); files.pack(fill="x", pady=(0, 8))
        self.src_var = tk.StringVar(); self._lbl_entry(files, "Source JS", self.src_var)
        ttk.Button(files, text="Browse source", command=self._pick_src).pack(fill="x", pady=(0, 6))
        self.dst_var = tk.StringVar(); self._lbl_entry(files, "Output", self.dst_var)
        ttk.Button(files, text="Browse output", command=self._pick_dst).pack(fill="x")
        opts = ttk.LabelFrame(parent, text=" Options ", padding=8); opts.pack(fill="x", pady=(0, 8))
        ttk.Label(opts, text="Obfuscation mode").pack(anchor="w")
        self.obf_mode_var = tk.StringVar(value="string-table")
        mf = tk.Frame(opts, bg="#0f172a"); mf.pack(fill="x", pady=(2, 6))
        ttk.Radiobutton(mf, text="string-table", variable=self.obf_mode_var,
                        value="string-table").pack(side="left", fill="x", expand=True)
        ttk.Radiobutton(mf, text="whole-code", variable=self.obf_mode_var,
                        value="whole-code").pack(side="left", fill="x", expand=True)
        self.key_var = tk.StringVar(value=DEFAULT_KEY)
        self._lbl_entry(opts, "XOR key (string-table only)", self.key_var, show="*")
        ttk.Button(opts, text="Randomize key", command=self._randomize_key).pack(fill="x", pady=(0, 6))
        ttk.Label(opts, text="Min string length").pack(anchor="w", pady=(4, 2))
        self.minlen_var = tk.StringVar(value=str(MIN_LEN))
        ttk.Spinbox(opts, from_=1, to=64, textvariable=self.minlen_var, width=6).pack(anchor="w")
        self.gzip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="gzip string table", variable=self.gzip_var).pack(anchor="w", pady=(8, 0))
        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Verify with node --check", variable=self.verify_var).pack(anchor="w")
        assets = ttk.LabelFrame(parent, text=" Assets (optional) ", padding=8); assets.pack(fill="x", pady=(0, 8))
        self.assets_var = tk.StringVar(); self._lbl_entry(assets, "Folder", self.assets_var)
        r = tk.Frame(assets, bg="#0f172a"); r.pack(fill="x")
        ttk.Button(r, text="Browse", command=self._pick_assets).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Check", command=self._check_assets).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.assets_info_var = tk.StringVar(value="")
        ttk.Label(assets, textvariable=self.assets_info_var, style="Hint.TLabel",
                  wraplength=440, justify="left").pack(anchor="w", pady=(4, 0))
        run = ttk.Frame(parent); run.pack(fill="x", pady=(0, 6))
        self.run_btn = ttk.Button(run, text="Bundle & Obfuscate", style="Go.TButton",
                                  command=self._run_obf)
        self.run_btn.pack(fill="x")
        r2 = tk.Frame(parent); r2.pack(fill="x", pady=(0, 8))
        self.open_btn = ttk.Button(r2, text="Open folder", style="Big.TButton",
                                   command=self._open_output, state="disabled")
        self.open_btn.pack(side="left", fill="x", expand=True)
        self.obf_progress = ttk.Progressbar(r2, mode="indeterminate")
        self.obf_progress.pack(side="left", fill="x", expand=True, padx=(6, 0))
        lf, self.log_text = self._make_log_box(parent, height=8); lf.pack(fill="x")

    def _build_deobfuscate_tab(self, parent):
        self._banner(parent, "Deobfuscate", "Auto-detect format.", accent="#f97316")
        files = ttk.LabelFrame(parent, text=" Files ", padding=8); files.pack(fill="x", pady=(0, 8))
        self.deobf_src_var = tk.StringVar(); self._lbl_entry(files, "Obfuscated JS", self.deobf_src_var)
        r = tk.Frame(files, bg="#0f172a"); r.pack(fill="x", pady=(0, 6))
        ttk.Button(r, text="Browse", command=self._pick_deobf_src).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Use obf. output", command=self._use_obf_as_deobf_input).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.deobf_dst_var = tk.StringVar(); self._lbl_entry(files, "Restored JS", self.deobf_dst_var)
        ttk.Button(files, text="Browse", command=self._pick_deobf_dst).pack(fill="x")
        self.deobf_verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="Verify with node --check", variable=self.deobf_verify_var).pack(anchor="w", pady=(0, 8))
        run = ttk.Frame(parent); run.pack(fill="x", pady=(0, 6))
        self.deobf_btn = ttk.Button(run, text="Deobfuscate", style="GoU.TButton", command=self._run_deobf)
        self.deobf_btn.pack(fill="x")
        r2 = tk.Frame(parent); r2.pack(fill="x", pady=(0, 8))
        self.deobf_open_btn = ttk.Button(r2, text="Open folder", style="Big.TButton",
                                          command=self._open_deobf_output, state="disabled")
        self.deobf_open_btn.pack(side="left", fill="x", expand=True)
        self.deobf_progress = ttk.Progressbar(r2, mode="indeterminate")
        self.deobf_progress.pack(side="left", fill="x", expand=True, padx=(6, 0))
        lf, self.deobf_log = self._make_log_box(parent, height=8); lf.pack(fill="x")

    def _build_deploy_tab(self, parent):
        self._banner(parent, "Deploy", "Push worker to all accounts.", accent="#22c55e")
        files = ttk.LabelFrame(parent, text=" Inputs ", padding=8); files.pack(fill="x", pady=(0, 8))
        self.acc_path_var = tk.StringVar(); self._lbl_entry(files, "Accounts JSON", self.acc_path_var)
        r = tk.Frame(files, bg="#0f172a"); r.pack(fill="x", pady=(0, 6))
        ttk.Button(r, text="Browse", command=self._pick_accounts).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Load", command=self._load_accounts).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.worker_path_var = tk.StringVar(); self._lbl_entry(files, "Worker JS", self.worker_path_var)
        r = tk.Frame(files, bg="#0f172a"); r.pack(fill="x")
        ttk.Button(r, text="Browse", command=self._pick_worker).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Use obf. output", command=self._use_obf_output).pack(side="left", fill="x", expand=True, padx=(4, 0))
        d1_box = ttk.LabelFrame(parent, text=" D1 Database ", padding=8, style="D1Lbl.TLabelframe")
        d1_box.pack(fill="x", pady=(0, 8))
        self.d1_binding_var = tk.StringVar(value=DEFAULT_D1_BINDING)
        self._lbl_entry(d1_box, "D1 binding name (env.<NAME>)", self.d1_binding_var)
        self.d1_name_var = tk.StringVar(value=DEFAULT_D1_NAME)
        self._lbl_entry(d1_box, "D1 database name", self.d1_name_var)
        ttk.Button(d1_box, text="Reset to defaults", style="Small.TButton",
                   command=self._reset_d1_defaults).pack(anchor="e")
        route_box = ttk.LabelFrame(parent, text=" workers.dev route ", padding=8, style="Repair.TLabelframe")
        route_box.pack(fill="x", pady=(0, 8))
        ttk.Label(route_box,
            text="If a worker shows 'There is nothing here yet', its workers.dev "
                 "route is disabled. Click to re-enable for all accounts.",
            style="Hint.TLabel", wraplength=440, justify="left").pack(anchor="w", pady=(0, 6))
        ttk.Button(route_box, text="Enable workers.dev route (all accounts)",
                   style="Go3.TButton", command=self._run_enable_route_all).pack(fill="x")
        reset_box = ttk.LabelFrame(parent, text=" Reset worker ", padding=8, style="Reset.TLabelframe")
        reset_box.pack(fill="x", pady=(0, 8))
        self.force_reset_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(reset_box,
            text="Force reset: delete worker before each deploy",
            variable=self.force_reset_var).pack(anchor="w", pady=(0, 6))
        ttk.Label(reset_box, text="WARNING: Deletes worker + route.",
                  style="Hint.TLabel", foreground="#f87171").pack(anchor="w", pady=(0, 6))
        ttk.Button(reset_box, text="Delete worker from all accounts",
                   style="Reset.TButton", command=self._run_force_reset_all).pack(fill="x")
        opt = ttk.LabelFrame(parent, text=" Options ", padding=8); opt.pack(fill="x", pady=(0, 8))
        self.clear_cron_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Remove cron triggers after deploy", variable=self.clear_cron_var).pack(anchor="w")
        self.force_module_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Force ES Module format", variable=self.force_module_var).pack(anchor="w")
        self.acc_info_var = tk.StringVar(value="No accounts loaded.")
        ttk.Label(opt, textvariable=self.acc_info_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        run = ttk.Frame(parent); run.pack(fill="x", pady=(0, 6))
        self.deploy_btn = ttk.Button(run, text="Deploy to all accounts",
                                     style="Go2.TButton", command=self._run_deploy)
        self.deploy_btn.pack(fill="x")
        r2 = tk.Frame(parent); r2.pack(fill="x", pady=(0, 6))
        self.cancel_btn = ttk.Button(r2, text="Cancel", style="Big.TButton",
                                     command=self._cancel_deploy, state="disabled")
        self.cancel_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Clear D1 cache", style="Big.TButton",
                   command=self._clear_d1_cache).pack(side="left", fill="x", expand=True, padx=(4, 0))
        r3 = tk.Frame(parent); r3.pack(fill="x", pady=(0, 6))
        ttk.Button(r3, text="Ping all workers", style="Go3.TButton",
                   command=self._run_ping_all).pack(fill="x")
        r4 = tk.Frame(parent); r4.pack(fill="x", pady=(0, 8))
        ttk.Button(r4, text="Remove cron (all)", style="Warn.TButton",
                   command=self._run_remove_cron).pack(fill="x")
        self.deploy_progress = ttk.Progressbar(parent, mode="determinate")
        self.deploy_progress.pack(fill="x", pady=(0, 8))
        res = ttk.LabelFrame(parent, text=" Results ", padding=(4, 4)); res.pack(fill="x", pady=(0, 6))
        cols = ("name","account","worker","status")
        self.tree = ttk.Treeview(res, columns=cols, show="headings", height=6)
        for c, w, txt in (("name",100,"Account"),("account",110,"ID"),
                          ("worker",90,"Worker"),("status",220,"Status")):
            self.tree.heading(c, text=txt); self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(res, command=self.tree.yview); sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.config(yscrollcommand=sb.set)
        self.tree.tag_configure("ok", foreground="#4ade80")
        self.tree.tag_configure("err", foreground="#f87171")
        self.tree.tag_configure("wait", foreground="#94a3b8")
        lf, self.deploy_log = self._make_log_box(parent, height=6); lf.pack(fill="x")

    def _reset_d1_defaults(self):
        self.d1_binding_var.set(DEFAULT_D1_BINDING)
        self.d1_name_var.set(DEFAULT_D1_NAME)
        self._dlog("info", "D1 defaults restored.")

    def _build_usage_tab(self, parent):
        self._banner(parent, "Usage", "Today's request count.", accent="#0ea5e9")
        files = ttk.LabelFrame(parent, text=" Inputs ", padding=8); files.pack(fill="x", pady=(0, 8))
        self.usage_acc_path_var = tk.StringVar(); self._lbl_entry(files, "Accounts JSON", self.usage_acc_path_var)
        r = tk.Frame(files, bg="#0f172a"); r.pack(fill="x", pady=(0, 6))
        ttk.Button(r, text="Browse", command=self._usage_pick_accounts).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Load & Check", command=self._usage_load_and_check).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(files, text="Same as Deploy", command=self._usage_use_same_accounts).pack(fill="x")
        self.usage_info_var = tk.StringVar(value="No accounts loaded.")
        ttk.Label(files, textvariable=self.usage_info_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        run = ttk.Frame(parent); run.pack(fill="x", pady=(0, 8))
        self.usage_btn = ttk.Button(run, text="Refresh usage", style="Go3.TButton", command=self._run_usage)
        self.usage_btn.pack(side="left", fill="x", expand=True)
        self.usage_cancel_btn = ttk.Button(run, text="Cancel", style="Big.TButton",
                                            command=self._cancel_usage, state="disabled")
        self.usage_cancel_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.usage_progress = ttk.Progressbar(parent, mode="determinate")
        self.usage_progress.pack(fill="x", pady=(0, 8))
        res = ttk.LabelFrame(parent, text=" Daily usage (UTC) ", padding=(4, 4)); res.pack(fill="x", pady=(0, 8))
        cols = ("dot","name","used","remaining","pct","bar","errors")
        self.usage_tree = ttk.Treeview(res, columns=cols, show="headings", height=8)
        for c, w, txt, a in (("dot",24,"","center"),("name",100,"Account","w"),
                             ("used",70,"Used","e"),("remaining",70,"Left","e"),
                             ("pct",45,"%","e"),("bar",100,"Bar","w"),
                             ("errors",50,"Err","e")):
            self.usage_tree.heading(c, text=txt); self.usage_tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(res, command=self.usage_tree.yview); sb.pack(side="right", fill="y")
        self.usage_tree.pack(side="left", fill="x", expand=True)
        self.usage_tree.config(yscrollcommand=sb.set)
        for t, col in (("ok","#4ade80"),("warn","#fbbf24"),("danger","#f87171"),
                       ("err","#94a3b8"),("wait","#94a3b8")):
            self.usage_tree.tag_configure(t, foreground=col)
        self.usage_summary_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.usage_summary_var,
                  style="Hint.TLabel", anchor="w").pack(fill="x", pady=(0, 4))
        lf, self.usage_log = self._make_log_box(parent, height=5); lf.pack(fill="x")

    def _build_d1_tab(self, parent):
        self._banner(parent, "D1 Analytics", "Today's D1 usage per account.",
                     accent="#a78bfa")
        files = ttk.LabelFrame(parent, text=" Inputs ", padding=8); files.pack(fill="x", pady=(0, 8))
        self.d1_an_acc_path_var = tk.StringVar()
        self._lbl_entry(files, "Accounts JSON", self.d1_an_acc_path_var)
        r = tk.Frame(files, bg="#0f172a"); r.pack(fill="x", pady=(0, 6))
        ttk.Button(r, text="Browse", command=self._d1_pick_accounts).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Load & Check", command=self._d1_load_and_check).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(files, text="Same as Deploy", command=self._d1_use_same_accounts).pack(fill="x")
        self.d1_an_info_var = tk.StringVar(value="No accounts loaded.")
        ttk.Label(files, textvariable=self.d1_an_info_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        lim = ttk.LabelFrame(parent, text=" Free-tier daily limits ", padding=8, style="D1Box.TLabelframe")
        lim.pack(fill="x", pady=(0, 8))
        ttk.Label(lim, text=f"Requests:   {DAILY_LIMIT:,}/day\n"
                            f"Rows read:  {D1_READS_LIMIT:,}/day\n"
                            f"Rows writ.: {D1_WRITES_LIMIT:,}/day",
                  style="Hint.TLabel", justify="left").pack(anchor="w")
        run = ttk.Frame(parent); run.pack(fill="x", pady=(0, 8))
        self.d1_an_btn = ttk.Button(run, text="Refresh D1 usage", style="Go.TButton",
                                     command=self._run_d1_usage)
        self.d1_an_btn.pack(side="left", fill="x", expand=True)
        self.d1_an_cancel_btn = ttk.Button(run, text="Cancel", style="Big.TButton",
                                            command=self.d1_usage_cancel.set, state="disabled")
        self.d1_an_cancel_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.d1_an_progress = ttk.Progressbar(parent, mode="determinate")
        self.d1_an_progress.pack(fill="x", pady=(0, 8))
        res = ttk.LabelFrame(parent, text=" D1 usage (today, UTC) ",
                             padding=(4, 4), style="D1Box.TLabelframe")
        res.pack(fill="x", pady=(0, 8))
        cols = ("dot","name","reads","writes","rows_r","rows_w","bar_r")
        self.d1_an_tree = ttk.Treeview(res, columns=cols, show="headings", height=8)
        for c, w, txt, a in (("dot",24,"","center"),("name",90,"Account","w"),
                             ("reads",60,"Reads","e"),("writes",60,"Writes","e"),
                             ("rows_r",85,"Rows Read","e"),("rows_w",85,"Rows Written","e"),
                             ("bar_r",100,"Read Bar","w")):
            self.d1_an_tree.heading(c, text=txt); self.d1_an_tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(res, command=self.d1_an_tree.yview); sb.pack(side="right", fill="y")
        self.d1_an_tree.pack(side="left", fill="x", expand=True)
        self.d1_an_tree.config(yscrollcommand=sb.set)
        for t, col in (("ok","#4ade80"),("warn","#fbbf24"),("danger","#f87171"),
                       ("err","#94a3b8"),("wait","#94a3b8")):
            self.d1_an_tree.tag_configure(t, foreground=col)
        self.d1_an_summary_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.d1_an_summary_var,
                  style="Hint.TLabel", anchor="w").pack(fill="x", pady=(0, 4))
        lf, self.d1_an_log = self._make_log_box(parent, height=5); lf.pack(fill="x")

    def _build_subdomain_tab(self, parent):
        self._banner(parent, "Subdomain", "workers.dev subdomain.", accent="#ec4899")
        src = ttk.LabelFrame(parent, text=" Accounts source ", padding=8); src.pack(fill="x", pady=(0, 8))
        self.sd_acc_path_var = tk.StringVar(); self._lbl_entry(src, "Accounts JSON", self.sd_acc_path_var)
        r = tk.Frame(src, bg="#0f172a"); r.pack(fill="x", pady=(0, 6))
        ttk.Button(r, text="Browse", command=self._sd_pick_accounts).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Load", command=self._sd_load_accounts).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(src, text="Same as Deploy", command=self._sd_use_same_accounts).pack(fill="x")
        self.sd_info_var = tk.StringVar(value="No accounts loaded.")
        ttk.Label(src, textvariable=self.sd_info_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        ch = ttk.LabelFrame(parent, text=" Change subdomain ", padding=8, style="SD.TLabelframe")
        ch.pack(fill="x", pady=(0, 8))
        info = tk.Frame(ch, bg="#0f172a"); info.pack(fill="x", pady=2)
        ttk.Label(info, text="Selected:").pack(side="left")
        self.sd_selected_var = tk.StringVar(value="(none)")
        ttk.Label(info, textvariable=self.sd_selected_var, font=("Segoe UI",9,"bold")).pack(side="left", padx=(4,12))
        ttk.Label(info, text="Current:").pack(side="left")
        self.sd_current_var = tk.StringVar(value="--")
        ttk.Label(info, textvariable=self.sd_current_var, font=("Consolas",9,"bold")).pack(side="left", padx=(4,0))
        self.sd_new_var = tk.StringVar(); self._lbl_entry(ch, "New subdomain", self.sd_new_var)
        r1 = tk.Frame(ch, bg="#0f172a"); r1.pack(fill="x", pady=(8, 4))
        ttk.Button(r1, text="Refresh selected", command=self._sd_refresh_selected).pack(side="left", fill="x", expand=True)
        ttk.Button(r1, text="Refresh ALL", command=self._sd_refresh_all).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(r1, text="Cancel", command=self._sd_cancel).pack(side="left", fill="x", expand=True, padx=(4, 0))
        r2 = tk.Frame(ch, bg="#0f172a"); r2.pack(fill="x", pady=(0, 4))
        ttk.Button(r2, text="Apply to selected", style="Go4.TButton",
                   command=self._sd_apply_selected).pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Apply to ALL", style="Warn.TButton",
                   command=self._sd_apply_all).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.sd_progress = ttk.Progressbar(ch, mode="determinate")
        self.sd_progress.pack(fill="x", pady=(6, 0))
        res = ttk.LabelFrame(parent, text=" Accounts ", padding=(4, 4)); res.pack(fill="x", pady=(0, 6))
        cols = ("name","account","worker","subdomain","status")
        self.sd_tree = ttk.Treeview(res, columns=cols, show="headings",
                                    height=6, selectmode="browse")
        for c, w, txt in (("name",80,"Account"),("account",110,"ID"),
                          ("worker",80,"Worker"),("subdomain",80,"Current"),
                          ("status",180,"Status")):
            self.sd_tree.heading(c, text=txt); self.sd_tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(res, command=self.sd_tree.yview); sb.pack(side="right", fill="y")
        self.sd_tree.pack(side="left", fill="x", expand=True)
        self.sd_tree.config(yscrollcommand=sb.set)
        self.sd_tree.tag_configure("ok", foreground="#4ade80")
        self.sd_tree.tag_configure("err", foreground="#f87171")
        self.sd_tree.tag_configure("wait", foreground="#94a3b8")
        self.sd_tree.bind("<<TreeviewSelect>>", self._sd_on_select)
        lf, self.sd_log = self._make_log_box(parent, height=5); lf.pack(fill="x")

    def _build_dashboard_tab(self, parent):
        self._banner(parent, "Dashboard", "Live overview.", accent="#22c55e")
        cards = ttk.Frame(parent); cards.pack(fill="x", pady=(0, 8))
        for i in range(3): cards.columnconfigure(i, weight=1)
        def kpi(col, label, var, color="#38bdf8"):
            box = tk.Frame(cards, bg="#1e293b", bd=0)
            box.grid(row=0, column=col, sticky="ew", padx=3, ipady=8)
            tk.Label(box, text=label, bg="#1e293b", fg="#94a3b8",
                     font=("Segoe UI", 8)).pack(anchor="w", padx=10)
            tk.Label(box, textvariable=var, bg="#1e293b", fg=color,
                     font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=10)
        self.dash_accounts_var = tk.StringVar(value="--")
        self.dash_workers_var = tk.StringVar(value="--")
        self.dash_requests_var = tk.StringVar(value="--")
        self.dash_errors_var = tk.StringVar(value="--")
        self.dash_d1_reads_var = tk.StringVar(value="--")
        self.dash_d1_writes_var = tk.StringVar(value="--")
        kpi(0, "Accounts", self.dash_accounts_var, "#38bdf8")
        kpi(1, "Workers", self.dash_workers_var, "#22c55e")
        kpi(2, "Requests today", self.dash_requests_var, "#a78bfa")
        cards2 = ttk.Frame(parent); cards2.pack(fill="x", pady=(0, 8))
        for i in range(3): cards2.columnconfigure(i, weight=1)
        def kpi2(col, label, var, color="#38bdf8"):
            box = tk.Frame(cards2, bg="#1e293b", bd=0)
            box.grid(row=0, column=col, sticky="ew", padx=3, ipady=8)
            tk.Label(box, text=label, bg="#1e293b", fg="#94a3b8",
                     font=("Segoe UI", 8)).pack(anchor="w", padx=10)
            tk.Label(box, textvariable=var, bg="#1e293b", fg=color,
                     font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=10)
        kpi2(0, "Errors today", self.dash_errors_var, "#f87171")
        kpi2(1, "D1 rows read", self.dash_d1_reads_var, "#a78bfa")
        kpi2(2, "D1 rows written", self.dash_d1_writes_var, "#ec4899")
        info = ttk.LabelFrame(parent, text=" System ", padding=8, style="Dash.TLabelframe")
        info.pack(fill="x", pady=(0, 8))
        self.dash_info_var = tk.StringVar(value="Not refreshed yet.")
        ttk.Label(info, textvariable=self.dash_info_var, style="Hint.TLabel",
                  wraplength=440, justify="left").pack(anchor="w")
        top = ttk.LabelFrame(parent, text=" Top accounts ", padding=(4, 4), style="Dash.TLabelframe")
        top.pack(fill="x", pady=(0, 8))
        cols = ("dot","name","used","pct")
        self.dash_top_tree = ttk.Treeview(top, columns=cols, show="headings", height=5)
        for c, w, t, a in (("dot",24,"","center"),("name",180,"Account","w"),
                           ("used",120,"Used today","e"),("pct",100,"Quota %","e")):
            self.dash_top_tree.heading(c, text=t); self.dash_top_tree.column(c, width=w, anchor=a)
        self.dash_top_tree.pack(fill="x")
        for t, col in (("ok","#4ade80"),("warn","#fbbf24"),
                       ("danger","#f87171"),("err","#94a3b8")):
            self.dash_top_tree.tag_configure(t, foreground=col)
        act = ttk.LabelFrame(parent, text=" Actions ", padding=8, style="Dash.TLabelframe")
        act.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(act, bg="#0f172a"); r1.pack(fill="x", pady=(0, 4))
        ttk.Button(r1, text="Refresh now", style="Go.TButton",
                   command=self._refresh_dashboard).pack(fill="x")
        r2 = tk.Frame(act, bg="#0f172a"); r2.pack(fill="x", pady=(0, 4))
        ttk.Button(r2, text="Ping all", style="Go3.TButton",
                   command=self._run_ping_all).pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Refresh usage", style="Go3.TButton",
                   command=self._run_usage).pack(side="left", fill="x", expand=True, padx=(4, 0))
        r3 = tk.Frame(act, bg="#0f172a"); r3.pack(fill="x", pady=(0, 4))
        ttk.Button(r3, text="Refresh D1", style="D1.TButton",
                   command=self._run_d1_usage).pack(fill="x")
        self.dash_auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="Auto-refresh every 60s",
                        variable=self.dash_auto_var).pack(anchor="w", pady=(6, 0))
        qr_box = ttk.LabelFrame(parent, text=" QR Code generator ", padding=8, style="Dash.TLabelframe")
        qr_box.pack(fill="x", pady=(0, 8))
        ttk.Label(qr_box, text="URL / subscription link").pack(anchor="w")
        self.qr_text_var = tk.StringVar()
        ttk.Entry(qr_box, textvariable=self.qr_text_var).pack(fill="x", ipady=4, pady=(2, 6))
        qr_btns = tk.Frame(qr_box, bg="#0f172a"); qr_btns.pack(fill="x")
        ttk.Button(qr_btns, text="Generate QR", style="Go.TButton",
                   command=self._generate_qr).pack(side="left", fill="x", expand=True)
        ttk.Button(qr_btns, text="Save PNG", style="Big.TButton",
                   command=self._save_qr).pack(side="left", fill="x", expand=True, padx=(4, 0))
        if not HAS_QRCODE:
            ttk.Label(qr_box, text="[!] qrcode not installed.",
                      style="Hint.TLabel", foreground="#f87171").pack(anchor="w", pady=(6, 0))
        self.qr_canvas = tk.Label(qr_box, bg="#0f172a", bd=0)
        self.qr_canvas.pack(pady=(6, 0))

    def _generate_qr(self):
        text = self.qr_text_var.get().strip()
        if not text: messagebox.showinfo("Info", "Enter URL."); return
        if not HAS_QRCODE: messagebox.showerror("Error", "no qrcode."); return
        try:
            img = make_qr_pil(text)
            if HAS_PIL:
                self._qr_photo = ImageTk.PhotoImage(img)
                self.qr_canvas.config(image=self._qr_photo, text="")
            else:
                p = Path.home() / ".apex_obf_qr.png"
                img.save(p)
                self.qr_canvas.config(text=f"Saved: {p}", fg="#22c55e")
        except Exception as e: messagebox.showerror("QR", str(e))

    def _save_qr(self):
        text = self.qr_text_var.get().strip()
        if not text: messagebox.showinfo("Info", "Enter URL."); return
        if not HAS_QRCODE: messagebox.showerror("Error", "no qrcode."); return
        p = filedialog.asksaveasfilename(title="Save QR as", defaultextension=".png",
            filetypes=[("PNG","*.png"),("All files","*.*")])
        if not p: return
        try:
            make_qr_pil(text).save(p)
            append_history("qr_save", p, "ok")
            messagebox.showinfo("Done", f"Saved:\n{p}")
        except Exception as e: messagebox.showerror("QR", str(e))

    def _build_history_tab(self, parent):
        self._banner(parent, "History", "All operations.", accent="#a78bfa")
        btns = ttk.Frame(parent); btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Refresh", style="Go.TButton",
                   command=self._refresh_history).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Export CSV", style="Big.TButton",
                   command=self._export_history).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(btns, text="Clear all", style="Warn.TButton",
                   command=self._clear_history).pack(side="left", fill="x", expand=True, padx=(4, 0))
        res = ttk.LabelFrame(parent, text=" Entries ", padding=(4, 4)); res.pack(fill="both", expand=True)
        cols = ("ts","action","account","status","detail")
        self.hist_tree = ttk.Treeview(res, columns=cols, show="headings", height=14)
        for c, w, t in (("ts",130,"Timestamp"),("action",110,"Action"),
                        ("account",110,"Account"),("status",70,"Status"),
                        ("detail",300,"Detail")):
            self.hist_tree.heading(c, text=t); self.hist_tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(res, command=self.hist_tree.yview); sb.pack(side="right", fill="y")
        self.hist_tree.pack(side="left", fill="both", expand=True)
        self.hist_tree.config(yscrollcommand=sb.set)
        self.hist_tree.tag_configure("ok", foreground="#4ade80")
        self.hist_tree.tag_configure("err", foreground="#f87171")
        self.hist_tree.tag_configure("info", foreground="#cbd5e1")

    def _refresh_history(self):
        try:
            for row in self.hist_tree.get_children(): self.hist_tree.delete(row)
            entries = load_history()
            for e in reversed(entries):
                tag = {"ok":"ok","err":"err","info":"info"}.get(e.get("status","info"),"info")
                self.hist_tree.insert("", "end",
                    values=(e.get("ts",""), e.get("action",""), e.get("account",""),
                            e.get("status",""), e.get("detail","")), tags=(tag,))
            self.status_var.set(f"History: {len(entries)}")
        except Exception as e: self.status_var.set(f"History error: {e}")

    def _export_history(self):
        p = filedialog.asksaveasfilename(title="Export CSV", defaultextension=".csv",
            filetypes=[("CSV","*.csv"),("All files","*.*")])
        if not p: return
        try:
            entries = load_history()
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["ts","action","account","status","detail"])
                w.writeheader()
                for e in entries: w.writerow(e)
            append_history("history_export", p, "ok")
            messagebox.showinfo("Done", f"Exported {len(entries)}.")
        except Exception as e: messagebox.showerror("Export", str(e))

    def _clear_history(self):
        if not messagebox.askyesno("Confirm", "Delete ALL history?"): return
        save_history([]); self._refresh_history()
        messagebox.showinfo("Done", "Cleared.")

    def _build_settings_tab(self, parent):
        self._banner(parent, "Settings", "Security, backup, tools.",
                     accent="#a78bfa")
        sec = ttk.LabelFrame(parent, text=" Security ", padding=8, style="Set.TLabelframe")
        sec.pack(fill="x", pady=(0, 8))
        self.settings_sec_var = tk.StringVar(value="Status: checking...")
        ttk.Label(sec, textvariable=self.settings_sec_var,
                  font=("Segoe UI",10,"bold"), foreground="#a78bfa").pack(anchor="w", pady=(0, 6))
        ttk.Button(sec, text="Change password", style="Go.TButton",
                   command=self._change_password).pack(fill="x", pady=(0, 4))
        r = tk.Frame(sec, bg="#0f172a"); r.pack(fill="x")
        ttk.Button(r, text="Disable password", style="Warn.TButton",
                   command=self._disable_password).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Lock now", style="Big.TButton",
                   command=self._lock_now).pack(side="left", fill="x", expand=True, padx=(4, 0))
        bk = ttk.LabelFrame(parent, text=" Backup & Restore ", padding=8, style="Set.TLabelframe")
        bk.pack(fill="x", pady=(0, 8))
        self.backup_info_var = tk.StringVar(value="No backups yet.")
        ttk.Label(bk, textvariable=self.backup_info_var, style="Hint.TLabel",
                  wraplength=440, justify="left").pack(anchor="w", pady=(0, 6))
        r = tk.Frame(bk, bg="#0f172a"); r.pack(fill="x", pady=(0, 4))
        ttk.Button(r, text="Backup now", style="Go.TButton",
                   command=self._backup_now).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Open folder", style="Big.TButton",
                   command=lambda: open_in_file_manager(BACKUP_DIR)
                   ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(bk, text="Restore latest backup", style="Warn.TButton",
                   command=self._restore_latest).pack(fill="x", pady=(4, 0))
        self._refresh_backup_info()
        enc = ttk.LabelFrame(parent, text=" Encryption (AES-256-GCM) ",
                             padding=8, style="Set.TLabelframe")
        enc.pack(fill="x", pady=(0, 8))
        if not HAS_CRYPTO:
            ttk.Label(enc, text="[!] cryptography not installed.",
                      style="Hint.TLabel", foreground="#f87171").pack(anchor="w")
        else:
            ttk.Button(enc, text="Encrypt accounts JSON", style="Go.TButton",
                       command=self._encrypt_accounts).pack(fill="x", pady=(0, 4))
            ttk.Button(enc, text="Decrypt & save plain", style="Big.TButton",
                       command=self._decrypt_accounts).pack(fill="x")
        sch = ttk.LabelFrame(parent, text=" Scheduled tasks ", padding=8, style="Set.TLabelframe")
        sch.pack(fill="x", pady=(0, 8))
        self.sched_auto_usage_var = tk.BooleanVar(value=False)
        self.sched_auto_ping_var = tk.BooleanVar(value=False)
        self.sched_auto_backup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sch, text="Auto-refresh usage every 6h",
                        variable=self.sched_auto_usage_var).pack(anchor="w")
        ttk.Checkbutton(sch, text="Auto-ping every 6h",
                        variable=self.sched_auto_ping_var).pack(anchor="w")
        ttk.Checkbutton(sch, text="Auto-backup every 24h",
                        variable=self.sched_auto_backup_var).pack(anchor="w")
        ttk.Button(sch, text="Save schedule", style="Big.TButton",
                   command=self._save_schedule_ui).pack(fill="x", pady=(6, 0))
        fbox = ttk.LabelFrame(parent, text=" Files & folders ", padding=8, style="Set.TLabelframe")
        fbox.pack(fill="x", pady=(0, 8))
        ttk.Button(fbox, text="Open settings folder", style="Big.TButton",
                   command=self._open_settings_folder).pack(fill="x", pady=(0, 4))
        ttk.Button(fbox, text="Open settings file", style="Big.TButton",
                   command=self._open_settings_file).pack(fill="x", pady=(0, 4))
        ttk.Button(fbox, text="Open output folder", style="Big.TButton",
                   command=self._open_output).pack(fill="x", pady=(0, 4))
        ttk.Button(fbox, text="Open deobf. folder", style="Big.TButton",
                   command=self._open_deobf_output).pack(fill="x")
        tbox = ttk.LabelFrame(parent, text=" Quick tools ", padding=8, style="Set.TLabelframe")
        tbox.pack(fill="x", pady=(0, 8))
        ttk.Button(tbox, text="Clear D1 cache", style="Big.TButton",
                   command=self._clear_d1_cache).pack(fill="x", pady=(0, 4))
        ttk.Button(tbox, text="Reset D1 fields", style="Big.TButton",
                   command=self._reset_d1_defaults).pack(fill="x", pady=(0, 4))
        ttk.Button(tbox, text="Shortcuts", style="Big.TButton",
                   command=self._show_shortcuts).pack(fill="x", pady=(0, 4))
        ttk.Button(tbox, text="About", style="Big.TButton",
                   command=self._show_about).pack(fill="x")

    def _refresh_backup_info(self):
        backups = list_backups()
        if not backups: self.backup_info_var.set("No backups yet.")
        else:
            latest = backups[0]
            try: size = latest.stat().st_size
            except Exception: size = 0
            self.backup_info_var.set(f"{len(backups)} backup(s). Latest: {latest.name} ({size} B)")

    def _backup_now(self):
        p = do_backup_settings()
        if p:
            append_history("backup", str(p), "ok")
            self._refresh_backup_info()
            messagebox.showinfo("Done", f"Backup:\n{p}")
        else: messagebox.showwarning("Backup", "No settings.")

    def _restore_latest(self):
        backups = list_backups()
        if not backups: messagebox.showinfo("Info", "No backups."); return
        latest = backups[0]
        if not messagebox.askyesno("Restore?", f"From:\n{latest.name}?"): return
        try:
            do_backup_settings(); shutil.copy2(latest, SETTINGS_FILE)
            append_history("restore", str(latest), "ok")
            messagebox.showinfo("Done", "Restored. Restart.")
        except Exception as e: messagebox.showerror("Error", str(e))

    def _encrypt_accounts(self):
        src = self.acc_path_var.get().strip()
        if not src or not Path(src).is_file():
            messagebox.showerror("Error", "No accounts JSON."); return
        dlg = PasswordDialog(self.tk, mode="setup", title="Encryption password")
        if not dlg.result: return
        out = Path(src).with_suffix(".json.enc")
        try:
            encrypt_accounts_file(Path(src), dlg.result, out)
            append_history("encrypt_accounts", str(out), "ok")
            messagebox.showinfo("Done", f"Encrypted:\n{out}")
        except Exception as e: messagebox.showerror("Error", str(e))

    def _decrypt_accounts(self):
        p = filedialog.askopenfilename(title="Open encrypted",
            filetypes=[("Encrypted","*.enc"),("All files","*.*")])
        if not p: return
        dlg = PasswordDialog(self.tk, mode="setup", title="Decryption password")
        if not dlg.result: return
        try:
            data = decrypt_accounts_file(Path(p), dlg.result)
            out = filedialog.asksaveasfilename(title="Save as", defaultextension=".json",
                filetypes=[("JSON","*.json"),("All files","*.*")])
            if not out: return
            Path(out).write_bytes(data)
            append_history("decrypt_accounts", out, "ok")
            messagebox.showinfo("Done", f"Decrypted:\n{out}")
        except Exception as e: messagebox.showerror("Error", str(e))

    def _save_schedule_ui(self):
        sched = {"auto_usage": bool(self.sched_auto_usage_var.get()),
                 "auto_ping": bool(self.sched_auto_ping_var.get()),
                 "auto_backup": bool(self.sched_auto_backup_var.get()),
                 "last_usage": load_schedule().get("last_usage", 0),
                 "last_ping": load_schedule().get("last_ping", 0),
                 "last_backup": load_schedule().get("last_backup", 0)}
        save_schedule(sched)
        messagebox.showinfo("Done", "Schedule saved.")

    def _start_scheduler(self):
        def loop():
            while True:
                try:
                    s = load_schedule(); now = time.time()
                    if s.get("auto_usage") and now - s.get("last_usage", 0) > 6*3600:
                        self.root.after(0, self._run_usage)
                        s["last_usage"] = now; save_schedule(s)
                    if s.get("auto_ping") and now - s.get("last_ping", 0) > 6*3600:
                        self.root.after(0, self._run_ping_all)
                        s["last_ping"] = now; save_schedule(s)
                    if s.get("auto_backup") and now - s.get("last_backup", 0) > 24*3600:
                        do_backup_settings()
                        s["last_backup"] = now; save_schedule(s)
                except Exception: pass
                time.sleep(120)
        threading.Thread(target=loop, daemon=True).start()

    def _open_palette(self):
        cmds = [
            ("Deploy to all", "deploy push upload",
             lambda: (self.nb.select(self.deploy_tab), self._run_deploy())),
            ("Enable workers.dev", "enable route fix",
             lambda: self._run_enable_route_all()),
            ("Refresh D1 usage", "d1 analytics",
             lambda: self._run_d1_usage()),
            ("Refresh usage", "usage quota",
             lambda: self._run_usage()),
            ("Refresh dashboard", "dashboard overview",
             lambda: self._refresh_dashboard()),
            ("Ping all", "ping health", lambda: self._run_ping_all()),
            ("Reset all workers", "reset delete",
             lambda: self._run_force_reset_all()),
            ("Remove cron", "cron remove", lambda: self._run_remove_cron()),
            ("Obfuscate", "bundle obfuscate",
             lambda: self.nb.select(self.obf_tab)),
            ("Deobfuscate", "deobfuscate restore",
             lambda: self.nb.select(self.deobf_tab)),
            ("Backup now", "backup", lambda: self._backup_now()),
            ("Lock now", "lock password", lambda: self._lock_now()),
            ("Show shortcuts", "help", lambda: self._show_shortcuts()),
            ("About", "info version", lambda: self._show_about()),
        ]
        try: CommandPalette(self.tk, cmds)
        except Exception as e: print("palette:", e)

    def _refresh_dashboard(self):
        try:
            self.dash_accounts_var.set(str(len(self.accounts)))
            workers = len([a for a in self.accounts
                           if a.get("workerName") or a.get("worker_name")])
            self.dash_workers_var.set(str(workers))
            total_used = 0; total_err = 0; rows = []
            for iid in self.usage_tree.get_children():
                vals = self.usage_tree.item(iid, "values")
                if len(vals) < 7: continue
                try:
                    used = int(str(vals[2]).replace(",", ""))
                    err = int(str(vals[6]).replace(",", ""))
                    name = str(vals[1]); pct_str = str(vals[4]).replace("%", "")
                    pct = float(pct_str) if pct_str not in ("-", "") else 0
                    rows.append((name, used, err, pct))
                    total_used += used; total_err += err
                except Exception: continue
            self.dash_requests_var.set(f"{total_used:,}")
            self.dash_errors_var.set(f"{total_err:,}")
            for iid in self.dash_top_tree.get_children(): self.dash_top_tree.delete(iid)
            rows.sort(key=lambda r: r[1], reverse=True)
            for name, used, _err, pct in rows[:5]:
                if pct >= 90: dot, tag = "[R]", "danger"
                elif pct >= 70: dot, tag = "[Y]", "warn"
                else: dot, tag = "[G]", "ok"
                self.dash_top_tree.insert("", "end",
                    values=(dot, name, f"{used:,}", f"{pct:.1f}%"), tags=(tag,))
            d1_rr = 0; d1_rw = 0
            for iid in self.d1_an_tree.get_children():
                vals = self.d1_an_tree.item(iid, "values")
                if len(vals) < 6: continue
                try:
                    d1_rr += int(str(vals[4]).replace(",", ""))
                    d1_rw += int(str(vals[5]).replace(",", ""))
                except Exception: continue
            self.dash_d1_reads_var.set(f"{d1_rr:,}")
            self.dash_d1_writes_var.set(f"{d1_rw:,}")
            s = load_settings()
            lock = "ON" if s.get("password_hash") else "OFF"
            self.dash_info_var.set(
                f"Last: {dt.datetime.now():%H:%M:%S}\nLock: {lock}\n"
                f"D1: {self.d1_binding_var.get()} / {self.d1_name_var.get()}\n"
                f"Force-reset: {'ON' if self.force_reset_var.get() else 'OFF'}")
            self.status_var.set("Dashboard refreshed.")
        except Exception as e: self.status_var.set(f"Dashboard error: {e}")
        try:
            if self.dash_auto_var.get():
                self.root.after(60_000, self._refresh_dashboard)
        except Exception: pass

    def _run_ping_all(self):
        if self.ping_thread and self.ping_thread.is_alive(): return
        if not self.accounts:
            self._load_accounts()
            if not self.accounts: return
        self.ping_thread = threading.Thread(target=self._ping_worker, daemon=True)
        self.ping_thread.start()
        append_history("ping_all", f"{len(self.accounts)} accounts", "info")

    def _ping_worker(self):
        total = len(self.accounts); ok_count = 0
        self._dlog("header", f"Pinging {total}...")
        subs = {}
        for acc in self.accounts:
            try: subs[id(acc)] = cf_get_workers_subdomain(acc)
            except Exception: subs[id(acc)] = ""
        for idx, acc in enumerate(self.accounts, start=1):
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            sub = subs.get(id(acc), "")
            self._dlog("info", f"[{idx}/{total}] {label}")
            try:
                res = cf_ping_worker(acc, sub)
                if res["ok"]: ok_count += 1; msg = f"[{res['status']}] {res['ms']}ms"; tag = "ok"
                else: msg = f"[{res['status']}] {res.get('err','')[:40]}"; tag = "err"
            except Exception as e:
                msg = f"error: {e}"; tag = "err"
            self._dlog("ok" if tag == "ok" else "error", f"   {msg}")
            self.root.after(0, self._update_row, str(id(acc)), label,
                            (acc.get("accountId") or "")[:20],
                            acc.get("workerName") or "",
                            ("[OK] " if tag == "ok" else "[X] ") + msg, tag)
        self._dlog("header", f"Ping: {ok_count}/{total} ok.")

    def _bind_shortcuts(self):
        t = self.tk
        t.bind("<Control-o>", lambda e: self._pick_src())
        t.bind("<Control-s>", lambda e: self._run_obf())
        t.bind("<F5>", lambda e: self._run_obf())
        t.bind("<Control-r>", lambda e: self._run_deobf())
        t.bind("<Control-d>", lambda e: self._run_deploy())
        t.bind("<Control-u>", lambda e: self._run_usage())
        t.bind("<Control-l>", lambda e: self._lock_now())
        t.bind("<Control-k>", lambda e: self._open_palette())
        t.bind("<Control-q>", lambda e: self._on_close())

    def _restore_settings(self):
        s = load_settings()
        for k, var in (("key", self.key_var), ("assets", self.assets_var),
                       ("accounts", self.acc_path_var),
                       ("worker", self.worker_path_var),
                       ("minlen", self.minlen_var),
                       ("d1_binding", self.d1_binding_var),
                       ("d1_name", self.d1_name_var),
                       ("sd_accounts", self.sd_acc_path_var)):
            if k in s:
                try: var.set(str(s[k]))
                except Exception: pass
        if "accounts" in s:
            try:
                self.usage_acc_path_var.set(s["accounts"])
                self.d1_an_acc_path_var.set(s["accounts"])
            except Exception: pass
        for k, var in (("verify", self.verify_var), ("gzip", self.gzip_var),
                       ("clear_cron", self.clear_cron_var),
                       ("force_module", self.force_module_var),
                       ("force_reset", self.force_reset_var)):
            if k in s:
                try: var.set(bool(s[k]))
                except Exception: pass
        if "obf_mode" in s:
            try: self.obf_mode_var.set(str(s["obf_mode"]))
            except Exception: pass
        self._log("header", f"{APP_NAME} v{APP_VERSION}")
        self._refresh_status_segments()
        self._refresh_settings_card()
        sch = load_schedule()
        self.sched_auto_usage_var.set(bool(sch.get("auto_usage")))
        self.sched_auto_ping_var.set(bool(sch.get("auto_ping")))
        self.sched_auto_backup_var.set(bool(sch.get("auto_backup")))
        self._refresh_history()

    def _save_settings(self):
        s = load_settings()
        s.update({"key": self.key_var.get(), "minlen": self.minlen_var.get(),
                  "verify": self.verify_var.get(), "gzip": self.gzip_var.get(),
                  "clear_cron": self.clear_cron_var.get(),
                  "force_module": self.force_module_var.get(),
                  "force_reset": self.force_reset_var.get(),
                  "obf_mode": self.obf_mode_var.get(),
                  "d1_binding": self.d1_binding_var.get(),
                  "d1_name": self.d1_name_var.get(),
                  "assets": self.assets_var.get(),
                  "accounts": self.acc_path_var.get(),
                  "worker": self.worker_path_var.get(),
                  "sd_accounts": self.sd_acc_path_var.get()})
        save_settings(s)

    def _refresh_settings_card(self):
        try:
            s = load_settings()
            self.settings_sec_var.set("Password: ENABLED"
                if s.get("password_hash") else "Password: DISABLED")
        except Exception: pass

    def _change_password(self):
        s = load_settings(); stored = s.get("password_hash")
        if not stored:
            dlg = PasswordDialog(self.tk, mode="setup")
            if dlg.result:
                s["password_hash"] = hash_password(dlg.result)
                save_settings(s)
                self._refresh_status_segments(); self._refresh_settings_card()
                append_history("password_set", "", "ok")
                messagebox.showinfo("Done", "Password set.")
            return
        dlg = PasswordDialog(self.tk, mode="change", stored_hash=stored)
        if dlg.result:
            s["password_hash"] = hash_password(dlg.result)
            save_settings(s)
            self._refresh_status_segments(); self._refresh_settings_card()
            append_history("password_change", "", "ok")
            messagebox.showinfo("Done", "Changed.")

    def _disable_password(self):
        s = load_settings(); stored = s.get("password_hash")
        if not stored: messagebox.showinfo("Info", "No password."); return
        dlg = PasswordDialog(self.tk, mode="login", stored_hash=stored,
                             title="Confirm disable")
        if dlg.result is None: return
        s.pop("password_hash", None); save_settings(s)
        self._refresh_status_segments(); self._refresh_settings_card()
        append_history("password_disabled", "", "ok")
        messagebox.showinfo("Done", "Disabled.")

    def _lock_now(self):
        s = load_settings(); stored = s.get("password_hash")
        if not stored: messagebox.showinfo("Info", "No password."); return
        dlg = PasswordDialog(self.tk, mode="login", stored_hash=stored,
                             title="Locked")
        if dlg.result is None: return
        self.status_var.set("Unlocked.")

    def _show_about(self):
        try:
            s = load_settings()
            sec = "enabled" if s.get("password_hash") else "disabled"
            msg = (f"{APP_NAME}\nVersion: {APP_VERSION}\n\n"
                f"Settings: {SETTINGS_FILE}\nPassword: {sec}\n"
                f"cryptography: {'yes' if HAS_CRYPTO else 'no'}\n"
                f"qrcode: {'yes' if HAS_QRCODE else 'no'}\n"
                f"PIL: {'yes' if HAS_PIL else 'no'}")
            messagebox.showinfo("About", msg)
        except Exception as e: messagebox.showerror("Error", str(e))

    def _show_shortcuts(self):
        messagebox.showinfo("Shortcuts",
            "Ctrl+O Open JS\nCtrl+S Obfuscate\nF5 Obfuscate\n"
            "Ctrl+R Deobfuscate\nCtrl+D Deploy\nCtrl+U Usage\n"
            "Ctrl+K Palette\nCtrl+L Lock\nCtrl+Q Exit")

    def _open_settings_folder(self):
        try: open_in_file_manager(SETTINGS_FILE.parent)
        except Exception as e: messagebox.showerror("Error", str(e))

    def _open_settings_file(self):
        try:
            if SETTINGS_FILE.exists(): open_in_file_manager(SETTINGS_FILE)
            else: messagebox.showinfo("Info", "No settings file.")
        except Exception as e: messagebox.showerror("Error", str(e))

    def _pick_src(self):
        p = filedialog.askopenfilename(title="Source JS",
            filetypes=[("JavaScript","*.js"),("All files","*.*")])
        if not p: return
        self.src_var.set(p)
        if not self.dst_var.get().strip():
            sp = Path(p); self.dst_var.set(str(sp.with_name(sp.stem + ".obf.js")))

    def _pick_dst(self):
        p = filedialog.asksaveasfilename(title="Save as", defaultextension=".js",
            filetypes=[("JavaScript","*.js"),("All files","*.*")])
        if p: self.dst_var.set(p)

    def _pick_assets(self):
        p = filedialog.askdirectory(title="Assets folder")
        if p: self.assets_var.set(p); self._check_assets()

    def _check_assets(self):
        d = self.assets_var.get().strip()
        if not d or not Path(d).is_dir():
            self.assets_info_var.set("[!] Not found."); return
        folder = Path(d)
        spec = {"PANEL_HTML_CONTENT":["panel.html"], "LOGIN_HTML_CONTENT":["login.html"],
                "ERROR_HTML_CONTENT":["error.html"], "PROXY_IP_HTML_CONTENT":["proxyip.html"],
                "ICON_CONTENT":["icon.b64","icon.txt"]}
        found, missing = [], []
        for key, cands in spec.items():
            hit = next((c for c in cands if (folder / c).is_file()), None)
            (found if hit else missing).append(f"{key}<-{hit}" if hit else key)
        parts = [f"[OK] {len(found)}/{len(spec)}: " + ", ".join(found)]
        if missing: parts.append("[--] missing: " + ", ".join(missing))
        self.assets_info_var.set("  |  ".join(parts))
        self._log("info", self.assets_info_var.get())

    def _randomize_key(self):
        alphabet = string.ascii_letters + string.digits + "-_!@#$%*"
        self.key_var.set("".join(secrets.choice(alphabet) for _ in range(32)))

    def _open_output(self):
        if not self.last_output: messagebox.showinfo("Info", "No output."); return
        if self.last_output.parent.exists(): open_in_file_manager(self.last_output.parent)

    def _run_obf(self):
        if self.obf_thread and self.obf_thread.is_alive(): return
        src = self.src_var.get().strip(); dst = self.dst_var.get().strip()
        key = self.key_var.get(); mode = self.obf_mode_var.get() or "string-table"
        if not src or not Path(src).is_file():
            messagebox.showerror("Error", "Source not found."); return
        if not dst: messagebox.showerror("Error", "Output?"); return
        if src == dst: messagebox.showerror("Error", "Must differ."); return
        if mode == "string-table" and not key:
            messagebox.showerror("Error", "Key empty."); return
        try: min_len = max(1, min(64, int(self.minlen_var.get())))
        except ValueError: messagebox.showerror("Error", "Min len?"); return
        self.minlen_var.set(str(min_len))
        assets = self.assets_var.get().strip() or None
        self._save_settings()
        self.log_text.config(state="normal"); self.log_text.delete("1.0","end")
        self.log_text.config(state="disabled")
        self.run_btn.config(state="disabled"); self.open_btn.config(state="disabled")
        self.obf_progress.start(12)
        self.status_var.set(f"Obfuscating [{mode}]...")
        self.obf_thread = threading.Thread(target=self._obf_worker,
            args=(src, dst, key, min_len, self.verify_var.get(),
                  self.gzip_var.get(), assets, mode), daemon=True)
        self.obf_thread.start()

    def _obf_worker(self, src, dst, key, min_len, verify, use_gzip, assets, mode):
        try:
            sp, dp = Path(src), Path(dst)
            self._log("info", f"[*] Reading {sp}")
            text = sp.read_text(encoding="utf-8")
            self._log("info", f"    {len(text):,} chars")
            bundled = _build_bundle(text, Path(assets) if assets else None,
                                     log=lambda m: self._log("info", m))
            if mode == "whole-code":
                out = obfuscate_whole(bundled, log=lambda m: self._log("info", m),
                                       verify_syntax=verify)
            else:
                out = obfuscate(bundled, key, min_len=min_len,
                                 log=lambda m: self._log("info", m),
                                 verify_syntax=verify, use_gzip=use_gzip)
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(out, encoding="utf-8")
            self._log("ok", f"[OK] [{mode}] -> {dp}")
            self.last_output = dp
            self.root.after(0, lambda: self.worker_path_var.set(str(dp)))
            self.root.after(0, lambda: self.deobf_src_var.set(str(dp)))
            append_history("obfuscate", f"[{mode}] {dp.name}", "ok")
            self._set_obf_ui("Done.", enable_open=True)
        except Exception as e:
            self._log("error", f"[!] {e}")
            self._log("error", traceback.format_exc())
            append_history("obfuscate", str(e)[:200], "err")
            self._set_obf_ui(f"Failed: {e}")

    def _set_obf_ui(self, status, enable_open=False):
        def _apply():
            self.obf_progress.stop(); self.run_btn.config(state="normal")
            if enable_open: self.open_btn.config(state="normal")
            self.status_var.set(status)
        self.root.after(0, _apply)

    def _pick_deobf_src(self):
        p = filedialog.askopenfilename(title="Obfuscated JS",
            filetypes=[("JavaScript","*.js"),("All files","*.*")])
        if not p: return
        self.deobf_src_var.set(p)
        if not self.deobf_dst_var.get().strip():
            sp = Path(p); self.deobf_dst_var.set(str(sp.with_name(sp.stem + ".deobf.js")))

    def _pick_deobf_dst(self):
        p = filedialog.asksaveasfilename(title="Save as", defaultextension=".js",
            filetypes=[("JavaScript","*.js"),("All files","*.*")])
        if p: self.deobf_dst_var.set(p)

    def _use_obf_as_deobf_input(self):
        if self.last_output:
            self.deobf_src_var.set(str(self.last_output))
            if not self.deobf_dst_var.get().strip():
                sp = self.last_output
                self.deobf_dst_var.set(str(sp.with_name(sp.stem + ".deobf.js")))
        else: messagebox.showinfo("Info", "No obf. output.")

    def _open_deobf_output(self):
        if not self.last_deobf_output: messagebox.showinfo("Info", "No deobf."); return
        p = self.last_deobf_output
        folder = p if p.is_dir() else p.parent
        if folder.exists(): open_in_file_manager(folder)

    def _run_deobf(self):
        if self.deobf_thread and self.deobf_thread.is_alive(): return
        src = self.deobf_src_var.get().strip(); dst = self.deobf_dst_var.get().strip()
        if not src or not Path(src).is_file():
            messagebox.showerror("Error", "Not found."); return
        if not dst: messagebox.showerror("Error", "Output?"); return
        if src == dst: messagebox.showerror("Error", "Must differ."); return
        self._save_settings()
        self.deobf_log.config(state="normal"); self.deobf_log.delete("1.0","end")
        self.deobf_log.config(state="disabled")
        self.deobf_btn.config(state="disabled"); self.deobf_open_btn.config(state="disabled")
        self.deobf_progress.start(12); self.status_var.set("Deobfuscating...")
        self.deobf_thread = threading.Thread(target=self._deobf_worker,
            args=(src, dst, self.deobf_verify_var.get()), daemon=True)
        self.deobf_thread.start()

    def _deobf_worker(self, src, dst, verify):
        try:
            sp, dp = Path(src), Path(dst)
            self._dlog2("info", f"[*] Reading {sp}")
            text = sp.read_text(encoding="utf-8")
            res = deobfuscate(text, log=lambda m: self._dlog2("info", m),
                               verify_syntax=verify, output_path=dp)
            if res["format"] in ("apex","whole"):
                dp.parent.mkdir(parents=True, exist_ok=True)
                dp.write_text(res["text"], encoding="utf-8")
                self._dlog2("ok", f"[OK] [{res['format']}] -> {dp}")
                self.last_deobf_output = dp
                append_history("deobfuscate", f"[{res['format']}] {dp.name}", "ok")
            else:
                folder = res["folder"]
                self._dlog2("ok", f"[OK] -> {folder}")
                self.last_deobf_output = folder
                append_history("deobfuscate", f"bundle -> {folder}", "ok")
            self._set_deobf_ui("Done.", enable_open=True)
        except Exception as e:
            self._dlog2("error", f"[!] {e}")
            append_history("deobfuscate", str(e)[:200], "err")
            self._set_deobf_ui(f"Failed: {e}")

    def _set_deobf_ui(self, status, enable_open=False):
        def _apply():
            self.deobf_progress.stop(); self.deobf_btn.config(state="normal")
            if enable_open: self.deobf_open_btn.config(state="normal")
            self.status_var.set(status)
        self.root.after(0, _apply)

    def _pick_accounts(self):
        p = filedialog.askopenfilename(title="Accounts JSON",
            filetypes=[("JSON","*.json"),("All files","*.*")])
        if p:
            self.acc_path_var.set(p); self.usage_acc_path_var.set(p)
            self.d1_an_acc_path_var.set(p); self.sd_acc_path_var.set(p)
            self._load_accounts()

    def _pick_worker(self):
        p = filedialog.askopenfilename(title="Worker JS",
            filetypes=[("JavaScript","*.js"),("All files","*.*")])
        if p: self.worker_path_var.set(p)

    def _use_obf_output(self):
        if self.last_output:
            self.worker_path_var.set(str(self.last_output))
        else:
            dst = self.dst_var.get().strip()
            if dst and Path(dst).is_file(): self.worker_path_var.set(dst)
            else: messagebox.showinfo("Info", "No obf. output.")

    def _load_accounts(self):
        path = self.acc_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showerror("Error", "Not found."); return
        try: data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e: messagebox.showerror("Error", f"JSON: {e}"); return
        if not isinstance(data, list): messagebox.showerror("Error", "Must be array."); return
        self.accounts = data
        for row in self.tree.get_children(): self.tree.delete(row)
        for acc in self.accounts:
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            acc_id = (acc.get("accountId") or acc.get("account_id") or "")[:20]
            wname = acc.get("workerName") or acc.get("worker_name") or ""
            self.tree.insert("", "end", iid=str(id(acc)),
                             values=(label, acc_id, wname, "waiting"), tags=("wait",))
        self.acc_info_var.set(f"Loaded {len(self.accounts)} account(s).")
        self._dlog("header", f"Loaded {len(self.accounts)}")
        append_history("load_accounts", f"{len(self.accounts)}", "ok")
        self._refresh_dashboard()

    def _clear_d1_cache(self):
        self.d1_cache.clear(); self._dlog("info", "D1 cache cleared.")
        append_history("d1_cache_clear", "", "ok")

    def _cancel_deploy(self):
        self.deploy_cancel.set(); self._dlog("error", "Cancelling...")

    def _run_deploy(self):
        if self.deploy_thread and self.deploy_thread.is_alive(): return
        if not self.accounts:
            self._load_accounts()
            if not self.accounts: return
        wpath = self.worker_path_var.get().strip()
        if not wpath or not Path(wpath).is_file():
            messagebox.showerror("Error", "Worker JS not found."); return
        d1_binding = (self.d1_binding_var.get().strip() or DEFAULT_D1_BINDING)
        d1_name    = (self.d1_name_var.get().strip() or DEFAULT_D1_NAME)
        bad = re.compile(r'[^A-Za-z0-9_\-]')
        if bad.search(d1_binding) or bad.search(d1_name):
            messagebox.showerror("Error", "D1 names invalid."); return
        try: content = Path(wpath).read_text(encoding="utf-8")
        except Exception as e: messagebox.showerror("Error", f"Read: {e}"); return
        is_module = self.force_module_var.get() or _is_es_module(content)
        force_reset = bool(self.force_reset_var.get())
        self._dlog("header", f"Deploying to {len(self.accounts)}...")
        self._dlog("info", f"Worker: {wpath} ({len(content):,} B)")
        self._dlog("info", f"D1: {d1_binding} / {d1_name}")
        if force_reset: self._dlog("warn", "Force-reset ON.")
        self._save_settings()
        self.deploy_cancel.clear()
        self.deploy_btn.config(state="disabled"); self.cancel_btn.config(state="normal")
        self.deploy_progress.config(maximum=len(self.accounts), value=0)
        self.status_var.set("Deploying...")
        for acc in self.accounts:
            self.tree.item(str(id(acc)), values=(
                (acc.get("name") or acc.get("workerName") or "(unnamed)"),
                (acc.get("accountId") or acc.get("account_id") or "")[:20],
                acc.get("workerName") or acc.get("worker_name") or "",
                "waiting"), tags=("wait",))
        self.deploy_thread = threading.Thread(target=self._deploy_worker,
            args=(content, self.clear_cron_var.get(), self.force_module_var.get(),
                  d1_binding, d1_name, force_reset), daemon=True)
        self.deploy_thread.start()

    def _deploy_one_with_retry(self, acc, content, clear_cron, force_module,
                                d1_binding, d1_name, force_reset):
        last = None
        for attempt in range(1, DEPLOY_MAX_ATTEMPTS+1):
            if self.deploy_cancel.is_set():
                return {"ok": False, "label": "cancelled", "msg": "cancelled"}
            try:
                res = cf_deploy_worker(acc, content, self.d1_cache,
                    log=lambda m: self._dlog("info", m),
                    clear_cron=clear_cron, force_module=force_module,
                    d1_binding=d1_binding, d1_name=d1_name,
                    force_reset=force_reset)
            except Exception as e:
                res = {"ok": False, "label": "err", "msg": f"exception: {e}"}
            if res["ok"]:
                if attempt > 1: self._dlog("ok", f"   [OK] attempt {attempt}")
                return res
            last = res
            if attempt < DEPLOY_MAX_ATTEMPTS and _should_retry_deploy(res["msg"]):
                delay_ms = DEPLOY_RETRY_BASE_MS * (2 ** (attempt-1))
                self._dlog("error", f"   [R] a{attempt}: {res['msg'][:80]}")
                self._dlog("info", f"   [W] backoff {delay_ms}ms")
                slept = 0
                while slept < delay_ms:
                    if self.deploy_cancel.is_set(): break
                    time.sleep(0.1); slept += 100
                continue
            return res
        return last

    def _deploy_worker(self, content, clear_cron, force_module,
                        d1_binding, d1_name, force_reset):
        ok_count = 0; total = len(self.accounts)
        for idx, acc in enumerate(self.accounts, start=1):
            if self.deploy_cancel.is_set(): break
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            self._dlog("info", f"[{idx}/{total}] {label}")
            res = self._deploy_one_with_retry(acc, content, clear_cron,
                force_module, d1_binding, d1_name, force_reset)
            acc_id = (acc.get("accountId") or acc.get("account_id") or "")[:20]
            wname = acc.get("workerName") or acc.get("worker_name") or ""
            tag = "ok" if res and res.get("ok") else "err"
            status_txt = ("[OK] " if res and res.get("ok") else "[X] ") \
                         + (res.get("msg") if res else "unknown")
            self.root.after(0, self._update_row, str(id(acc)), label,
                            acc_id, wname, status_txt, tag)
            if res and res.get("ok"):
                ok_count += 1
                self._dlog("ok", f"   [OK] {res['msg']}")
                append_history("deploy", res["msg"], "ok", label)
            else:
                self._dlog("error", f"   [X] {res.get('msg','?')}")
                append_history("deploy", res.get("msg","?"), "err", label)
            self.root.after(0, lambda v=idx:
                            self.deploy_progress.config(value=v))
        self._dlog("header", f"Finished: {ok_count}/{total}")
        self.root.after(0, self._deploy_done, ok_count, total)

    def _update_row(self, iid, name, acc_id, wname, status_txt, tag):
        try:
            self.tree.item(iid, values=(name, acc_id, wname, status_txt), tags=(tag,))
        except Exception: pass

    def _deploy_done(self, ok, total):
        self.deploy_btn.config(state="normal"); self.cancel_btn.config(state="disabled")
        self.status_var.set(f"Deploy: {ok}/{total} ok.")
        try: self._refresh_dashboard()
        except Exception: pass

    def _run_enable_route_all(self):
        if self.repair_thread and self.repair_thread.is_alive(): return
        if not self.accounts:
            self._load_accounts()
            if not self.accounts: return
        if not messagebox.askyesno("Confirm",
            f"Enable workers.dev for {len(self.accounts)}?"): return
        self.repair_thread = threading.Thread(target=self._enable_route_worker,
                                                daemon=True)
        self.repair_thread.start()

    def _enable_route_worker(self):
        ok_count = 0; total = len(self.accounts)
        self._dlog("header", f"Enabling workers.dev for {total}...")
        for idx, acc in enumerate(self.accounts, start=1):
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            self._dlog("info", f"[{idx}/{total}] {label}")
            res = cf_repair_workers_dev(acc, self.d1_cache,
                                         log=lambda m: self._dlog("info", m))
            tag = "ok" if res and res.get("ok") else "err"
            msg = res.get("msg", "?") if res else "unknown"
            acc_id = (acc.get("accountId") or acc.get("account_id") or "")[:20]
            wname = acc.get("workerName") or acc.get("worker_name") or ""
            self.root.after(0, self._update_row, str(id(acc)), label,
                            acc_id, wname, ("[OK] " if tag=="ok" else "[X] ") + msg, tag)
            if tag == "ok":
                ok_count += 1
                self._dlog("ok", f"   [OK] {msg}")
                append_history("enable_route", msg, "ok", label)
            else:
                self._dlog("error", f"   [X] {msg}")
                append_history("enable_route", msg, "err", label)
        self._dlog("header", f"Enable route: {ok_count}/{total} ok.")

    def _run_force_reset_all(self):
        if self.reset_thread and self.reset_thread.is_alive(): return
        if not self.accounts:
            self._load_accounts()
            if not self.accounts: return
        if not messagebox.askyesno("Confirm DELETE",
            f"DELETE from {len(self.accounts)} account(s)?\n\n"
            f"workers.dev route will be removed."): return
        self.reset_thread = threading.Thread(target=self._force_reset_worker,
                                               daemon=True)
        self.reset_thread.start()

    def _force_reset_worker(self):
        ok_count = 0; total = len(self.accounts)
        for idx, acc in enumerate(self.accounts, start=1):
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            try:
                was = cf_delete_worker(acc); ok = True
                msg = "deleted" if was else "gone"
            except Exception as e:
                ok = False; msg = f"failed: {e}"
            tag = "ok" if ok else "err"
            self.root.after(0, self._update_row, str(id(acc)), label,
                            (acc.get("accountId") or "")[:20],
                            acc.get("workerName") or "",
                            ("[OK] " if ok else "[X] ") + msg, tag)
            if ok: ok_count += 1
            self._dlog("ok" if ok else "error", f"   {msg}")
            append_history("reset_worker", msg, "ok" if ok else "err", label)
        self._dlog("header", f"Delete: {ok_count}/{total}")

    def _run_remove_cron(self):
        if self.cron_thread and self.cron_thread.is_alive(): return
        if not self.accounts:
            self._load_accounts()
            if not self.accounts: return
        if not messagebox.askyesno("Confirm", f"Remove crons from {len(self.accounts)}?"): return
        self.cron_thread = threading.Thread(target=self._remove_cron_worker,
                                              daemon=True)
        self.cron_thread.start()

    def _remove_cron_worker(self):
        ok_count = 0
        for acc in self.accounts:
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            try:
                before = cf_get_cron(acc); cf_remove_cron(acc); ok = True
                msg = f"cleared {len(before)}"
            except Exception as e:
                ok = False; msg = f"failed: {e}"
            if ok: ok_count += 1
            self._dlog("ok" if ok else "error", f"   {msg}")
            append_history("cron_remove", msg, "ok" if ok else "err", label)

    # ---------- usage ----------
    def _usage_pick_accounts(self):
        p = filedialog.askopenfilename(title="Accounts",
            filetypes=[("JSON","*.json"),("All files","*.*")])
        if p: self.usage_acc_path_var.set(p)

    def _usage_use_same_accounts(self):
        self.usage_acc_path_var.set(self.acc_path_var.get())

    def _usage_load_and_check(self): self._run_usage()

    def _cancel_usage(self): self.usage_cancel.set()

    def _run_usage(self):
        if self.usage_thread and self.usage_thread.is_alive(): return
        path = self.usage_acc_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showerror("Error", "Not found."); return
        try: data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e: messagebox.showerror("Error", f"JSON: {e}"); return
        if not isinstance(data, list) or not data:
            messagebox.showerror("Error", "Empty."); return
        self.usage_cancel.clear()
        self.usage_btn.config(state="disabled"); self.usage_cancel_btn.config(state="normal")
        self.usage_progress.config(maximum=len(data), value=0)
        self.status_var.set("Fetching usage...")
        for row in self.usage_tree.get_children(): self.usage_tree.delete(row)
        for acc in data:
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            self.usage_tree.insert("", "end", iid=str(id(acc)),
                values=("[.]", label, "...", "...", "...", "", ""), tags=("wait",))
        self.usage_thread = threading.Thread(target=self._usage_worker,
                                               args=(data,), daemon=True)
        self.usage_thread.start()

    def _usage_worker(self, accounts):
        results = []; total = len(accounts)
        for idx, acc in enumerate(accounts, start=1):
            if self.usage_cancel.is_set(): break
            try: res = cf_get_usage(acc, log=lambda m: self._ulog("info", m))
            except Exception as e:
                res = {"label": "(?)", "ok": False, "msg": f"exception: {e}"}
            results.append(res)
            self.root.after(0, self._usage_update_row, str(id(acc)), res)
            self.root.after(0, lambda v=idx: self.usage_progress.config(value=v))
        self.root.after(0, self._usage_done, results, total)

    def _usage_update_row(self, iid, res):
        try:
            if not res["ok"]:
                self.usage_tree.item(iid, values=("[!]", res["label"], "-","-","-","",
                    res["msg"][:40]), tags=("err",))
                return
            used, errors = res["requests"], res["errors"]
            remaining = max(0, DAILY_LIMIT - used)
            pct = min(999.0, (used / DAILY_LIMIT) * 100)
            if pct >= 90: dot, tag = "[R]", "danger"
            elif pct >= 70: dot, tag = "[Y]", "warn"
            else: dot, tag = "[G]", "ok"
            self.usage_tree.item(iid, values=(dot, res["label"], f"{used:,}",
                f"{remaining:,}", f"{pct:.1f}%", _make_bar(pct, 16), f"{errors:,}"),
                tags=(tag,))
        except Exception: pass

    def _usage_done(self, results, total):
        self.usage_btn.config(state="normal"); self.usage_cancel_btn.config(state="disabled")
        ok = [r for r in results if r["ok"]]
        total_used = sum(r["requests"] for r in ok)
        self.status_var.set(f"Usage: {len(ok)}/{total} ok, {total_used:,} req.")
        append_history("usage_refresh", f"{len(ok)}/{total}", "ok")
        self._refresh_dashboard()

    # ---------- D1 ----------
    def _d1_pick_accounts(self):
        p = filedialog.askopenfilename(title="Accounts",
            filetypes=[("JSON","*.json"),("All files","*.*")])
        if p: self.d1_an_acc_path_var.set(p)

    def _d1_use_same_accounts(self):
        self.d1_an_acc_path_var.set(self.acc_path_var.get())

    def _d1_load_and_check(self): self._run_d1_usage()

    def _run_d1_usage(self):
        if self.d1_usage_thread and self.d1_usage_thread.is_alive(): return
        path = self.d1_an_acc_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showerror("Error", "Not found."); return
        try: data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e: messagebox.showerror("Error", f"JSON: {e}"); return
        if not isinstance(data, list) or not data:
            messagebox.showerror("Error", "Empty."); return
        self.d1_analytics_accounts = data
        self.d1_usage_cancel.clear()
        self.d1_an_btn.config(state="disabled"); self.d1_an_cancel_btn.config(state="normal")
        self.d1_an_progress.config(maximum=len(data), value=0)
        self.status_var.set("Fetching D1...")
        for row in self.d1_an_tree.get_children(): self.d1_an_tree.delete(row)
        for acc in data:
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            self.d1_an_tree.insert("", "end", iid=str(id(acc)),
                values=("[.]", label, "...", "...", "...", "...", ""), tags=("wait",))
        self.d1_an_info_var.set(f"Checking {len(data)}...")
        self.d1_an_summary_var.set("")
        self.d1_an_log.config(state="normal"); self.d1_an_log.delete("1.0","end")
        self.d1_an_log.config(state="disabled")
        self._d1_an_log("header", f"Fetching D1 for {len(data)}...")
        self.d1_usage_thread = threading.Thread(target=self._d1_usage_worker,
                                                  args=(data,), daemon=True)
        self.d1_usage_thread.start()

    def _d1_usage_worker(self, accounts):
        results = []; total = len(accounts)
        for idx, acc in enumerate(accounts, start=1):
            if self.d1_usage_cancel.is_set(): break
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            self._d1_an_log("info", f"[{idx}/{total}] {label}")
            try: res = cf_get_d1_usage(acc, log=lambda m: self._d1_an_log("info", m))
            except Exception as e:
                res = {"label": label, "ok": False, "msg": f"exception: {e}"}
            results.append(res)
            self.root.after(0, self._d1_usage_update_row, str(id(acc)), res)
            self.root.after(0, lambda v=idx: self.d1_an_progress.config(value=v))
            if res["ok"]:
                self._d1_an_log("ok", f"   [OK] {res['readQueries']:,}R "
                                       f"{res['writeQueries']:,}W "
                                       f"rows {res['rowsRead']:,}R/"
                                       f"{res['rowsWritten']:,}W")
            else: self._d1_an_log("error", f"   [X] {res['msg']}")
        self.root.after(0, self._d1_usage_done, results, total)

    def _d1_usage_update_row(self, iid, res):
        try:
            if not res["ok"]:
                self.d1_an_tree.item(iid, values=("[!]", res["label"], "-",
                    "-", "-", "-", res["msg"][:30]), tags=("err",))
                return
            rr = res["rowsRead"]; rw = res["rowsWritten"]
            rq = res["readQueries"]; wq = res["writeQueries"]
            pct_r = min(999.0, (rr / D1_READS_LIMIT) * 100)
            pct_w = min(999.0, (rw / D1_WRITES_LIMIT) * 100)
            if pct_r >= 90 or pct_w >= 90: dot, tag = "[R]", "danger"
            elif pct_r >= 70 or pct_w >= 70: dot, tag = "[Y]", "warn"
            else: dot, tag = "[G]", "ok"
            self.d1_an_tree.item(iid, values=(dot, res["label"], f"{rq:,}", f"{wq:,}",
                f"{rr:,}", f"{rw:,}", _make_bar(pct_r, 12)), tags=(tag,))
        except Exception: pass

    def _d1_usage_done(self, results, total):
        self.d1_an_btn.config(state="normal"); self.d1_an_cancel_btn.config(state="disabled")
        ok = [r for r in results if r["ok"]]
        total_rr = sum(r["rowsRead"] for r in ok)
        total_rw = sum(r["rowsWritten"] for r in ok)
        self.d1_an_summary_var.set(
            f"{len(ok)}/{total} ok | reads {total_rr:,} | writes {total_rw:,}")
        self.d1_an_info_var.set(f"Last: {dt.datetime.now():%H:%M:%S}")
        self.status_var.set(f"D1: {len(ok)}/{total} ok")
        append_history("d1_usage", f"{len(ok)}/{total}", "ok")
        self._refresh_dashboard()

    # ---------- subdomain ----------
    def _sd_pick_accounts(self):
        p = filedialog.askopenfilename(title="Accounts",
            filetypes=[("JSON","*.json"),("All files","*.*")])
        if p: self.sd_acc_path_var.set(p)

    def _sd_use_same_accounts(self):
        self.sd_acc_path_var.set(self.acc_path_var.get())

    def _sd_load_accounts(self):
        path = self.sd_acc_path_var.get().strip()
        if not path or not Path(path).is_file():
            messagebox.showerror("Error", "Not found."); return
        try: data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e: messagebox.showerror("Error", f"JSON: {e}"); return
        if not isinstance(data, list) or not data:
            messagebox.showerror("Error", "Empty."); return
        self.sd_accounts = data
        for row in self.sd_tree.get_children(): self.sd_tree.delete(row)
        for acc in self.sd_accounts:
            label = acc.get("name") or acc.get("workerName") or "(unnamed)"
            acc_id = (acc.get("accountId") or acc.get("account_id") or "")[:20]
            wname = acc.get("workerName") or acc.get("worker_name") or ""
            self.sd_tree.insert("", "end", iid=str(id(acc)),
                values=(label, acc_id, wname, "(unknown)", ""), tags=("wait",))
        self.sd_info_var.set(f"Loaded {len(self.sd_accounts)}.")

    def _sd_on_select(self, event=None):
        sel = self.sd_tree.selection()
        if not sel: return
        iid = sel[0]
        acc = next((a for a in self.sd_accounts if str(id(a)) == iid), None)
        if not acc: return
        self.sd_selected_var.set(acc.get("name") or acc.get("workerName") or "(unnamed)")
        vals = self.sd_tree.item(iid, "values")
        if len(vals) >= 4:
            current = str(vals[3])
            if current not in ("(unknown)","--",""):
                self.sd_current_var.set(current); self.sd_new_var.set(current)
            else: self.sd_current_var.set("--")

    def _sd_refresh_selected(self):
        sel = self.sd_tree.selection()
        if not sel: messagebox.showinfo("Info","Select an account."); return
        acc = next((a for a in self.sd_accounts if str(id(a)) == sel[0]), None)
        if acc: self._sd_refresh_accounts([acc])

    def _sd_refresh_all(self):
        if not self.sd_accounts: messagebox.showinfo("Info","Load first."); return
        self._sd_refresh_accounts(list(self.sd_accounts))

    def _sd_cancel(self): self.sd_cancel.set()

    def _sd_refresh_accounts(self, accounts):
        if self.sd_thread and self.sd_thread.is_alive(): return
        self.sd_cancel.clear()
        self.sd_progress.config(maximum=len(accounts), value=0)
        self.sd_thread = threading.Thread(target=self._sd_refresh_worker,
                                            args=(accounts,), daemon=True)
        self.sd_thread.start()

    def _sd_refresh_worker(self, accounts):
        for idx, acc in enumerate(accounts, start=1):
            if self.sd_cancel.is_set(): break
            try:
                sub = cf_get_workers_subdomain(acc)
                display = sub if sub else "(not set)"
                tag = "ok"; msg = display
            except Exception as e:
                display = "--"; msg = f"error: {e}"; tag = "err"
            self.root.after(0, self._sd_update_row, str(id(acc)), display, msg, tag)
            self.root.after(0, lambda v=idx: self.sd_progress.config(value=v))

    def _sd_apply_selected(self):
        sel = self.sd_tree.selection()
        if not sel: messagebox.showinfo("Info","Select an account."); return
        acc = next((a for a in self.sd_accounts if str(id(a)) == sel[0]), None)
        if not acc: return
        new_sub = self.sd_new_var.get().strip().lower()
        if not new_sub or not SUBDOMAIN_RE.match(new_sub):
            messagebox.showerror("Error", "Invalid subdomain."); return
        self._sd_apply_accounts([acc], new_sub)

    def _sd_apply_all(self):
        if not self.sd_accounts: messagebox.showinfo("Info","Load first."); return
        new_sub = self.sd_new_var.get().strip().lower()
        if not new_sub or not SUBDOMAIN_RE.match(new_sub):
            messagebox.showerror("Error", "Invalid."); return
        if not messagebox.askyesno("Confirm", f"Change ALL {len(self.sd_accounts)}?"): return
        self._sd_apply_accounts(list(self.sd_accounts), new_sub)

    def _sd_apply_accounts(self, accounts, new_sub):
        if self.sd_thread and self.sd_thread.is_alive(): return
        self.sd_cancel.clear()
        self.sd_progress.config(maximum=len(accounts), value=0)
        self.sd_thread = threading.Thread(target=self._sd_apply_worker,
                                            args=(accounts, new_sub), daemon=True)
        self.sd_thread.start()

    def _sd_apply_worker(self, accounts, new_sub):
        ok_count = 0
        for idx, acc in enumerate(accounts, start=1):
            if self.sd_cancel.is_set(): break
            try:
                result = cf_set_workers_subdomain(acc, new_sub)
                tag = "ok"; msg = f"changed to '{result}'"; display = result
                ok_count += 1
            except Exception as e:
                tag = "err"; msg = f"failed: {e}"; display = "--"
            self.root.after(0, self._sd_update_row, str(id(acc)), display, msg, tag)
            self.root.after(0, lambda v=idx: self.sd_progress.config(value=v))
            append_history("subdomain", msg, "ok" if tag=="ok" else "err")

    def _sd_update_row(self, iid, sub, msg, tag):
        try:
            cur = self.sd_tree.item(iid, "values")
            if not cur: return
            name, acc_id, wname = cur[0], cur[1], cur[2]
            short = msg if len(msg) < 100 else msg[:97] + "..."
            self.sd_tree.item(iid, values=(name, acc_id, wname, sub, short), tags=(tag,))
            sel = self.sd_tree.selection()
            if sel and sel[0] == iid: self.sd_current_var.set(sub)
        except Exception: pass

    # ---------- logging ----------
    def _log(self, kind, msg): self.log_q.put(("obf", kind, str(msg)))
    def _dlog2(self, kind, msg): self.log_q.put(("dbf", kind, str(msg)))
    def _dlog(self, kind, msg): self.log_q.put(("dep", kind, str(msg)))
    def _ulog(self, kind, msg): self.log_q.put(("use", kind, str(msg)))
    def _sd_log(self, kind, msg): self.log_q.put(("sd", kind, str(msg)))
    def _d1_an_log(self, kind, msg): self.log_q.put(("d1", kind, str(msg)))

    def _poll_log(self):
        try:
            while True:
                chan, kind, msg = self.log_q.get_nowait()
                if chan == "obf": w = self.log_text
                elif chan == "dbf": w = self.deobf_log
                elif chan == "dep": w = self.deploy_log
                elif chan == "sd": w = self.sd_log
                elif chan == "d1": w = self.d1_an_log
                else: w = self.usage_log
                w.config(state="normal")
                w.insert("end", msg + "\n", kind)
                w.see("end"); w.config(state="disabled")
        except queue.Empty: pass
        self.root.after(80, self._poll_log)


def _make_bar(pct, width=24):
    filled = int(round(width * min(pct, 100) / 100))
    return "#" * filled + "." * (width - filled)


def _gz_b64(text):
    gz = gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)
    return base64.b64encode(gz).decode("ascii")


def _build_bundle(workero_src, assets_dir, log=print):
    if not assets_dir or not Path(assets_dir).is_dir():
        log("[ii] No assets folder."); return workero_src
    html_assets = {"PANEL_HTML_CONTENT":["panel.html"],"LOGIN_HTML_CONTENT":["login.html"],
                   "ERROR_HTML_CONTENT":["error.html"],"PROXY_IP_HTML_CONTENT":["proxyip.html"]}
    raw_assets = {"ICON_CONTENT":["icon.b64","icon.txt","favicon.b64"]}
    values, missing = {}, []
    assets_dir = Path(assets_dir)
    for key, cands in html_assets.items():
        found = next((assets_dir / c for c in cands if (assets_dir / c).is_file()), None)
        if not found: missing.append(key); continue
        values[key] = _gz_b64(found.read_text(encoding="utf-8"))
        log(f"[+] {key}")
    for key, cands in raw_assets.items():
        found = next((assets_dir / c for c in cands if (assets_dir / c).is_file()), None)
        if not found: missing.append(key); continue
        values[key] = found.read_text(encoding="utf-8").strip()
        log(f"[+] {key}")
    values["SOURCE_CONTENT"] = _gz_b64(workero_src)
    if missing: log(f"[!] Missing: {', '.join(missing)}")
    prefix = ("/* Apex bundled globals */\n"
              "Object.assign(globalThis, " + json.dumps(values) + ");\n")
    return prefix + workero_src


# ============================================================
#  Entry point — frame-based screen switching
# ============================================================
def _clear_frame(f):
    for w in list(f.winfo_children()):
        try: w.destroy()
        except Exception: pass


def _build_login_ui(parent, on_success, on_abort, max_attempts=5):
    _clear_frame(parent)
    head = tk.Frame(parent, bg="#1e293b"); head.pack(fill="x")
    tk.Label(head, text=APP_NAME, bg="#1e293b", fg="#38bdf8",
             font=("Segoe UI", 16, "bold")).pack(pady=(30, 4))
    tk.Label(head, text="Enter your master password",
             bg="#1e293b", fg="#94a3b8",
             font=("Segoe UI", 10)).pack(pady=(0, 24))
    body = tk.Frame(parent, bg="#0f172a"); body.pack(fill="both", expand=True, padx=40, pady=20)
    tk.Label(body, text="Password", bg="#0f172a", fg="#cbd5e1",
             font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
    pw_var = tk.StringVar()
    entry = tk.Entry(body, textvariable=pw_var, show="*", bg="#1e293b",
        fg="#e2e8f0", insertbackground="#38bdf8", relief="flat",
        font=("Segoe UI", 14), highlightthickness=2,
        highlightbackground="#334155", highlightcolor="#38bdf8")
    entry.pack(fill="x", ipady=10, pady=(4, 16))
    entry.focus_set()
    status_var = tk.StringVar(value="")
    tk.Label(body, textvariable=status_var, bg="#0f172a", fg="#f87171",
             font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
    attempts = [0]
    def submit(*_a):
        pw = pw_var.get().strip()
        if not pw: status_var.set("Password is empty."); return
        s = load_settings()
        if not verify_password(pw, s.get("password_hash", "")):
            attempts[0] += 1
            if attempts[0] >= max_attempts:
                status_var.set("Too many failures."); return
            status_var.set(f"Incorrect ({attempts[0]}/{max_attempts}).")
            pw_var.set(""); return
        on_success()
    tk.Button(body, text="Unlock", command=submit,
              bg="#2563eb", fg="white", activebackground="#1d4ed8",
              activeforeground="white", relief="flat",
              font=("Segoe UI", 13, "bold"), padx=30, pady=14,
              cursor="hand2", borderwidth=0,
              highlightthickness=0).pack(fill="x", pady=(20, 0))
    entry.bind("<Return>", submit)


def _build_setup_ui(parent, on_save, on_skip):
    _clear_frame(parent)
    head = tk.Frame(parent, bg="#1e293b"); head.pack(fill="x")
    tk.Label(head, text=APP_NAME, bg="#1e293b", fg="#38bdf8",
             font=("Segoe UI", 16, "bold")).pack(pady=(30, 4))
    tk.Label(head, text="Set a master password (min 6 chars)",
             bg="#1e293b", fg="#94a3b8",
             font=("Segoe UI", 10)).pack(pady=(0, 24))
    body = tk.Frame(parent, bg="#0f172a"); body.pack(fill="both", expand=True, padx=40, pady=20)
    tk.Label(body, text="New password", bg="#0f172a", fg="#cbd5e1",
             font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
    pw_var = tk.StringVar()
    e1 = tk.Entry(body, textvariable=pw_var, show="*", bg="#1e293b",
        fg="#e2e8f0", insertbackground="#38bdf8", relief="flat",
        font=("Segoe UI", 14), highlightthickness=2,
        highlightbackground="#334155", highlightcolor="#38bdf8")
    e1.pack(fill="x", ipady=10, pady=(4, 12)); e1.focus_set()
    tk.Label(body, text="Confirm password", bg="#0f172a", fg="#cbd5e1",
             font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
    cf_var = tk.StringVar()
    e2 = tk.Entry(body, textvariable=cf_var, show="*", bg="#1e293b",
        fg="#e2e8f0", insertbackground="#38bdf8", relief="flat",
        font=("Segoe UI", 14), highlightthickness=2,
        highlightbackground="#334155", highlightcolor="#38bdf8")
    e2.pack(fill="x", ipady=10, pady=(4, 16))
    status_var = tk.StringVar(value="")
    tk.Label(body, textvariable=status_var, bg="#0f172a", fg="#f87171",
             font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
    def submit(*_a):
        pw = pw_var.get().strip(); cf = cf_var.get().strip()
        if len(pw) < 6: status_var.set("Min 6 chars."); return
        if pw != cf: status_var.set("Passwords do not match."); return
        on_save(pw)
    btn_row = tk.Frame(body, bg="#0f172a"); btn_row.pack(fill="x", pady=(20, 0))
    tk.Button(btn_row, text="Save", command=submit,
              bg="#2563eb", fg="white", activebackground="#1d4ed8",
              activeforeground="white", relief="flat",
              font=("Segoe UI", 13, "bold"), padx=30, pady=14,
              cursor="hand2", borderwidth=0,
              highlightthickness=0).pack(side="left", fill="x", expand=True)
    tk.Button(btn_row, text="Skip", command=on_skip,
              bg="#1e293b", fg="#cbd5e1", activebackground="#334155",
              activeforeground="white", relief="flat",
              font=("Segoe UI", 12), padx=30, pady=14,
              cursor="hand2", borderwidth=0,
              highlightthickness=0).pack(side="left", fill="x", expand=True, padx=(8, 0))
    e2.bind("<Return>", submit)


def main():
    tk_root = tk.Tk()
    tk_root.title(f"{APP_NAME} v{APP_VERSION}")
    tk_root.geometry("1080x1000")
    tk_root.minsize(360, 500)
    try: tk_root.configure(bg="#0f172a")
    except Exception: pass
    tk_root.deiconify()
    try: tk_root.update_idletasks()
    except Exception: pass

    holder = tk.Frame(tk_root, bg="#0f172a")
    holder.pack(fill="both", expand=True)

    login_frame = tk.Frame(holder, bg="#0f172a")
    app_frame = tk.Frame(holder, bg="#0f172a")

    state = {"app": None}

    def show_app():
        try: login_frame.pack_forget()
        except Exception: pass
        try: app_frame.pack(fill="both", expand=True)
        except Exception: pass
        try: tk_root.update_idletasks()
        except Exception: pass
        if state["app"] is None:
            try:
                state["app"] = App(app_frame)
            except Exception as e:
                import traceback as tb
                tb.print_exc()
                try:
                    messagebox.showerror("Startup error", str(e),
                                          parent=tk_root)
                except Exception: pass

    s = load_settings()
    stored = s.get("password_hash")

    if stored:
        login_frame.pack(fill="both", expand=True)
        _build_login_ui(login_frame, on_success=show_app,
                        on_abort=lambda: tk_root.destroy())
    else:
        try:
            want = messagebox.askyesno(
                "Set a master password?",
                "Protect the toolkit with a master password?\n\n"
                "You can change or disable it later from Settings.",
                parent=tk_root)
        except Exception:
            want = False
        if want:
            login_frame.pack(fill="both", expand=True)
            def on_save(pw):
                s2 = load_settings()
                s2["password_hash"] = hash_password(pw)
                save_settings(s2)
                show_app()
            _build_setup_ui(login_frame, on_save=on_save, on_skip=show_app)
        else:
            show_app()

    tk_root.mainloop()


if __name__ == "__main__":
    main()
