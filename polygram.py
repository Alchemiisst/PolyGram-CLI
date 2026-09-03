import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import select
import shutil
import struct
from contextlib import contextmanager
import subprocess
import sys
import tempfile
import time

# ------------------------------------------------------------------ deps
try:
    from telethon import TelegramClient as _TC_async, events as _ev, errors as _err
    from telethon.sync import TelegramClient
    from telethon.tl.functions.account import (
        GetPasswordRequest,
        GetAuthorizationsRequest,
        ResetAuthorizationRequest,
    )
    try:
        from telethon.tl.functions.account import InvalidateSignInCodesRequest
    except ImportError:                      # very old Telethon
        InvalidateSignInCodesRequest = None
    try:
        from telethon.tl.functions.auth import (
            RequestPasswordRecoveryRequest,
            CheckRecoveryPasswordRequest,
            RecoverPasswordRequest,
        )
        RECOVERY_OK = True
    except ImportError:
        RequestPasswordRecoveryRequest = CheckRecoveryPasswordRequest = None
        RecoverPasswordRequest = None
        RECOVERY_OK = False
    import logging as _logging
    _logging.getLogger("telethon").setLevel(_logging.CRITICAL)
    TELETHON_OK, TELETHON_ERR = True, ""
except Exception as _e:                                  # pragma: no cover
    TELETHON_OK, TELETHON_ERR = False, str(_e)
    _err = _ev = None

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    CRYPTO_OK, CRYPTO_ERR = True, ""
except Exception as _e:                                  # pragma: no cover
    AESGCM = InvalidTag = None
    CRYPTO_OK, CRYPTO_ERR = False, str(_e)

try:
    import termios
    import tty
    USE_TERMIOS = True
except Exception:
    USE_TERMIOS = False

# ------------------------------------------------------------------ consts
APP_NAME = "Polygram"
APP_VERSION = "1.1.4"
TAGLINE = "✦ EVERY ACCOUNT · ONE TERMINAL ✦"
DEVICE_MODEL = "Polygram-CLI"
LOGIN_NOTIFY_ID = 777000            # Telegram login-notification service user
MY_TELEGRAM_URL = "https://my.telegram.org"

MAGIC = b"POLYGRAM1"
BUNDLE_FMT = 1
MAX_BUNDLE = 1024 * 1024            # 1 MB gate cap
EXPORT_KDF_ITERS = 300_000
VAULT_KDF_ITERS = 400_000
VAULT_SENTINEL_PT = b"POLYGRAM-VAULT-OK"
UNLOCK_TTL = 300                    # 5 min idle → vault re-locks

POLYGRAM_HOME = os.environ.get("POLYGRAM_HOME",
                               os.path.join(os.path.expanduser("~"), ".polygram"))

# ------------------------------------------------------------------ UI kit
ANIM = True
ASCII_MODE = False
COLOR = True
OUT = sys.stdout

def C(code):
    """Color wrapper — idempotent: constants are already-wrapped escapes,
    so C(BRAND) must not double-wrap (double-wrap = white text on phones)."""
    if not COLOR:
        return ""
    if code.startswith("\x1b["):
        return code
    return f"\x1b[{code}m"

ANIM = False
ASCII_MODE = False
COLOR = False
BOLD = RST = ""
BRAND = OK = WARN = DANGER = SECRET = DIM = WHITE = ""

def set_ui(anim, ascii_mode):
    global ANIM, ASCII_MODE, COLOR
    global BOLD, RST, BRAND, OK, WARN, DANGER, SECRET, DIM, WHITE
    ANIM = bool(anim) and sys.stdout.isatty()
    ASCII_MODE = bool(ascii_mode)
    COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and not ascii_mode
    BOLD, RST = C("1"), C("0")
    BRAND, OK, WARN, DANGER, SECRET, DIM, WHITE = (C("36"), C("92"), C("93"),
                                                   C("91"), C("95"), C("90"), C("97"))

SPINNER = "⠋⠙⠧⠇⠏"
def spin(i):
    return SPINNER[int(time.time() * 8) % len(SPINNER)]

ICONS = {
    "add": "➕", "login": "🔑", "accounts": "👥", "export": "📤", "import": "📥",
    "devices": "📱", "security": "🛡", "settings": "⚙", "exit": "🚪",
    "lock": "🔒", "unlock": "🔓", "key": "🗝", "zap": "⚡", "star4": "✦",
    "plane": "✈", "wave": "👋", "block": "🚫",
}
ICONS_ASCII = {
    "add": "+", "login": "K", "accounts": "A", "export": "^", "import": "v",
    "devices": "D", "security": "S", "settings": "C", "exit": "X",
    "lock": "[", "unlock": "]", "key": "*", "zap": "*", "star4": "*",
    "plane": ">", "wave": "-", "block": "X",
}

def icon(name):
    m = ICONS_ASCII if ASCII_MODE else ICONS
    return m.get(name, name)

BANNER_ART = [
    " ___  ___  _ __   _____ ___    _   __  __ ",
    "| _ \\/ _ \\| |\\ \\ / / __| _ \\  /_\\ |  \\/  |",
    "|  _/ (_) | |_\\ V / (_ |   / / _ \\| |\\/| |",
    "|_|  \\___/|____|_| \\___|_|_\\/_/ \\_\\_|  |_|",
]

def _w(s):
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))

def clear():
    if sys.stdout.isatty() and COLOR:
        OUT.write("\x1b[2J\x1b[H"); OUT.flush()

def line(s=""):
    print(s)

def draw(s):
    OUT.write("\r\x1b[2K" + s); OUT.flush()

def clear_line():
    OUT.write("\r\x1b[2K"); OUT.flush()

def live_line(text):
    # truncate to the real terminal width so \r-redraw never wraps (wrap = spam)
    try:
        w = shutil.get_terminal_size((60, 24)).columns
    except Exception:
        w = 60
    return text[:max(24, w - 1)]

def mmss(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"

def banner():
    w = max(len(l) for l in BANNER_ART)
    inner = w + 6
    def c(s):
        s = s[:inner - 2]
        pad = inner - 2 - len(s)
        return " " * (pad // 2) + s + " " * (pad - pad // 2)
    rows = [c(""), *BANNER_ART, c(""), c(TAGLINE), c(f"v{APP_VERSION} · vault mode")]
    print(C(BRAND))
    if ANIM:
        top = "╭" + "─" * inner + "╮"
        for r in [top] + ["│" + c(r) + "│" for r in rows] + ["╰" + "─" * inner + "╯"]:
            print(r)
            time.sleep(0.05)
    else:
        for r in ["╭" + "─" * inner + "╮"] + ["│" + c(r) + "│" for r in rows] + ["╰" + "─" * inner + "╯"]:
            print(r)
    print(C(RST))

def panel(title, lines, heavy=False, color=BRAND):
    if heavy:
        L, R, BL, BR, H, V = "╔", "╗", "╚", "╝", "═", "║"
    else:
        L, R, BL, BR, H, V = "╭", "╮", "╰", "╯", "─", "│"
    content = ([title] if title else []) + list(lines)
    w = max([_w(x) for x in content] + [24]) + 6
    if title:
        top = L + H * 2 + " " + color + BOLD + title + RST + \
              H * max(1, w - 6 - _w(title)) + R
    else:
        top = L + H * (w - 2) + R
    body = [V + "   " + l + " " * max(0, w - 6 - _w(l)) + V for l in lines]
    bot = BL + H * (w - 2) + BR
    if ANIM and heavy:
        print(top); time.sleep(0.15)
        for b in body:
            print(b); time.sleep(0.08)
        print(bot)
    else:
        print(top)
        for b in body:
            print(b)
        print(bot)

def success(title, lines):
    line()
    panel(title, lines, heavy=True, color=OK)
    line()

def fail(title, lines):
    line()
    panel(title, lines, heavy=True, color=DANGER)
    line()

def footer(keys):
    line(DIM + "  " + "─" * 40)
    line(DIM + "  " + keys + RST)

def header(cfg, n=None):
    n = len(load_accounts()) if n is None else n
    locked = not unlocked_now()
    line(C(BRAND) + BOLD + f"  ▛ POLYGRAM v{APP_VERSION} ▞" + RST +
         f"   {OK}{n} accounts{RST}  "
         + (C(DANGER) + icon("lock") + " locked" + RST if locked
            else C(OK) + icon("unlock") + " unlocked" + RST))
    line()

def ask(prompt, default=None):
    if default is not None:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = prompt + " "
    v = input(prompt).strip()
    if not v and default is not None:
        return default
    return v

def secret_input(prompt):
    if not sys.stdin.isatty():
        return input(prompt + " ").strip()
    if not USE_TERMIOS:
        return getpass.getpass(prompt + " ").strip()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        OUT.write(prompt + " "); OUT.flush()
        chars = []
        while True:
            ch = sys.stdin.read(1)
            if ch in "\r\n":
                break
            if ch == "\x03":
                OUT.write("\n"); raise KeyboardInterrupt
            if ch in "\x7f\x08":
                if chars:
                    chars.pop(); OUT.write(" \b"); OUT.flush()
            elif 32 <= ord(ch) < 127:
                chars.append(ch); OUT.write("•"); OUT.flush()
        OUT.write("\n"); OUT.flush()
        return "".join(chars).strip()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

@contextmanager
def cbreak_input():
    """Raw-ish stdin: every key arrives INSTANTLY (no Enter needed) and
    the terminal stops echoing — we draw the buffer ourselves.
    In cooked mode the kernel holds keys until Enter AND the spinner
    redraw wipes the terminal's echo, so on phones it feels like
    'I can't type'."""
    if not (USE_TERMIOS and sys.stdin.isatty()):
        yield
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def confirm(prompt, default=True):
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        v = input(f"{prompt} {suffix} ").strip().lower()
        if v in ("y", "yes"):
            return True
        if v in ("n", "no", ""):
            return default if not v else False

def type_yes(prompt):
    if not sys.stdin.isatty():
        return True
    while True:
        v = input(f"{prompt} [yes/no] ").strip().lower()
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False

def choose(prompt, items):
    if not sys.stdin.isatty():
        return 0
    for i, it in enumerate(items, 1):
        line(f"   {i}  {it}")
    while True:
        v = ask(prompt)
        if v.isdigit() and 1 <= int(v) <= len(items):
            return int(v) - 1
        line(DANGER + "  ⚠ enter a number from the list" + RST)

def pause():
    if sys.stdin.isatty():
        input(DIM + "  press enter to continue… " + RST)

def toast(msg, color=OK):
    line(color + "  " + msg + RST)

def strength(pp):
    s = min(len(pp), 16) // 4
    if re.search(r"\d", pp) and re.search(r"[a-zA-Z]", pp):
        s += 1
    if re.search(r"[^a-zA-Z0-9]", pp):
        s += 1
    if len(pp) >= 16:
        s += 1
    s = max(1, min(8, s))
    label = "weak" if s < 4 else ("ok" if s < 6 else "strong")
    return "▓" * s + "░" * (8 - s), label

# ------------------------------------------------------------- directories
def vault_dir():
    return POLYGRAM_HOME

def sub_dir(name, mode=0o700):
    p = os.path.join(POLYGRAM_HOME, name)
    os.makedirs(p, exist_ok=True)
    try:
        os.chmod(p, mode)
    except OSError:
        pass
    return p

def ensure_dirs():
    os.makedirs(POLYGRAM_HOME, exist_ok=True)
    try:
        os.chmod(POLYGRAM_HOME, 0o700)
    except OSError:
        pass
    for d in ("sessions", "exports", "quarantine", "tmp"):
        sub_dir(d)
    for f in os.listdir(os.path.join(POLYGRAM_HOME, "tmp")):
        if f.startswith("pgsess-"):
            try:
                os.unlink(os.path.join(POLYGRAM_HOME, "tmp", f))
            except OSError:
                pass

def cfg_path():
    return os.path.join(POLYGRAM_HOME, "config.json")

def android_download_dir():
    """Android's shared Download folder if Termux can reach it, else None."""
    cands = [os.path.join(os.path.expanduser("~"), "storage", "shared", "Download"),
             "/sdcard/Download"]
    for d in cands:
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    for d in cands:                      # storage granted but no Download yet
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            pass
    return None

def storage_hint():
    return (DIM + "  💡 to use the Download folder, run once:" + RST +
            "\n     " + C(BRAND) + "termux-setup-storage" + RST +
            "  (grant the storage permission)")

def audit(action, detail="", ok=True):
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(os.path.join(POLYGRAM_HOME, "audit.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {action} {'ok' if ok else 'FAIL'} {detail}\n")
    except OSError:
        pass

def notify(title, msg):
    try:
        cfg = load_cfg()
        if cfg and cfg.get("notify") and shutil.which("termux-notify"):
            subprocess.Popen(["termux-notify", "-t", title, "-s", msg],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def open_url(url):
    try:
        subprocess.run(["termux-open-url", url], timeout=5)
        return True
    except Exception:
        line(WARN + "  open this in your phone browser: " + RST + WHITE + url + RST)
        return False

def clipboard_set(text):
    try:
        subprocess.run(["termux-clipboard-set", "-p", text],
                       check=True, timeout=5, capture_output=True)
        return True
    except Exception:
        return False

def clipboard_get():
    try:
        r = subprocess.run(["termux-clipboard-get"], check=True, timeout=5,
                           capture_output=True, text=True)
        return r.stdout
    except Exception:
        return None

# ------------------------------------------------------------- config (json)
def load_cfg():
    try:
        with open(cfg_path(), encoding="utf-8") as f:
            c = json.load(f)
        if isinstance(c, dict) and c.get("api_id"):
            return c
    except Exception:
        pass
    return None

def save_cfg(cfg):
    p = cfg_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)

# ------------------------------------------------------------------- vault
UNLOCK = {"key": None, "at": 0.0}
NONTTY_PASS = None

def vault_meta():
    p = os.path.join(POLYGRAM_HOME, "vault.meta")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return bytes.fromhex(m["salt"]), int(m.get("iters", VAULT_KDF_ITERS))
    return None, None

def vault_init(passphrase):
    salt = secrets.token_bytes(32)
    with open(os.path.join(POLYGRAM_HOME, "vault.meta"), "w") as f:
        json.dump({"salt": salt.hex(), "iters": VAULT_KDF_ITERS}, f)
    vk = vault_key(passphrase)
    store_enc("vault.sentinel", VK_encrypt(vk, VAULT_SENTINEL_PT), 0o600)

def vault_key(passphrase):
    salt, iters = vault_meta()
    if salt is None:
        raise LoginError("vault not initialized — run `polygram` "
                         "interactively to create it")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iters, 32)

def store_enc(name, data, mode=0o600):
    p = os.path.join(POLYGRAM_HOME, name)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, p)

def read_enc(name):
    with open(os.path.join(POLYGRAM_HOME, name), "rb") as f:
        return f.read()

def VK_encrypt(key, pt):
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, pt, None)

def VK_decrypt(key, blob):
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)

def verify_sentinel(key):
    try:
        return VK_decrypt(key, read_enc("vault.sentinel")) == VAULT_SENTINEL_PT
    except Exception:
        return False

def unlocked_now():
    return UNLOCK["key"] is not None and (time.time() - UNLOCK["at"]) < UNLOCK_TTL

def ensure_unlock():
    if unlocked_now():
        return UNLOCK["key"]
    if vault_meta()[0] is None:
        if not sys.stdin.isatty():
            raise LoginError("vault not initialized — run `polygram` "
                             "interactively to create it")
        vault_setup()
    total = 5 if sys.stdin.isatty() else 1
    for attempt in range(total):
        if sys.stdin.isatty():
            pp = secret_input("  " + icon("lock") + " vault passphrase: ")
        else:
            pp = NONTTY_PASS or os.environ.get("POLYGRAM_PASS")
            if not pp:
                raise LoginError("vault passphrase required non-interactively —"
                                 " use --passphrase or the POLYGRAM_PASS env var")
        vk = vault_key(pp)
        if verify_sentinel(vk):
            UNLOCK.update(key=vk, at=time.time())
            return vk
        line(DANGER + f"  ✗ wrong passphrase (attempt {attempt + 1}/{total})" + RST)
        if attempt + 1 < total:
            time.sleep(1)
    raise LoginError("vault locked — too many wrong passphrases\n"
                     "forgot it? run:  polygram erase")

def rekey_vault(old_pass, new_pass):
    old_vk = vault_key(old_pass)
    if not verify_sentinel(old_vk):
        raise LoginError("current passphrase is wrong")
    new_salt = secrets.token_bytes(32)
    with open(os.path.join(POLYGRAM_HOME, "vault.meta"), "w") as f:
        json.dump({"salt": new_salt.hex(), "iters": VAULT_KDF_ITERS}, f)
    new_vk = hashlib.pbkdf2_hmac("sha256", new_pass.encode(), new_salt,
                                 VAULT_KDF_ITERS, 32)
    store_enc("vault.sentinel", VK_encrypt(new_vk, VAULT_SENTINEL_PT))
    sdir = os.path.join(POLYGRAM_HOME, "sessions")
    for fn in os.listdir(sdir):
        if fn.endswith(".enc"):
            blob = read_enc(os.path.join("sessions", fn))
            pt = VK_decrypt(old_vk, blob)
            store_enc(os.path.join("sessions", fn), VK_encrypt(new_vk, pt))
    UNLOCK.update(key=new_vk, at=time.time())
    audit("vault", "passphrase changed")

# --------------------------------------------------------------- accounts
def accounts_path():
    return os.path.join(POLYGRAM_HOME, "accounts.json")

def load_accounts():
    try:
        with open(accounts_path(), encoding="utf-8") as f:
            a = json.load(f)
        return a if isinstance(a, list) else []
    except Exception:
        return []

def save_accounts(accs):
    tmp = accounts_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(accs, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, accounts_path())

def next_id(accs):
    mx = 0
    for a in accs:
        m = re.search(r"(\d+)$", a.get("id", ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"acct-{mx + 1:04d}"

def store_session(vk, acc_id, data):
    store_enc(os.path.join("sessions", acc_id + ".enc"), VK_encrypt(vk, data))

def load_session_bytes(vk, acc_id):
    return VK_decrypt(vk, read_enc(os.path.join("sessions", acc_id + ".enc")))

def find_account(accs, ref):
    """ref: id ('acct-0003'), 1-based index, or phone."""
    if ref is None:
        return accs[0]
    r = str(ref).strip()
    for a in accs:
        if a["id"] == r:
            return a
    if r.isdigit():
        i = int(r)
        if 1 <= i <= len(accs):
            return accs[i - 1]
    for a in accs:
        if a.get("phone") == r or a.get("phone") == "+" + r.lstrip("+"):
            return a
    return None

def accounts_table(accs):
    rows = []
    for a in accs:
        star = C(WARN) + "★" + RST if a.get("star") else " "
        two = (C(OK) + "on ✓" + RST if a.get("has_2fa")
               else C(WARN) + "off" + RST)
        rows.append(
            f"  {star} {C(BRAND) + BOLD}{a['phone']}{RST}"
            f"   {C(SECRET)}{a.get('username') or '—'}{RST}"
            f"   {DIM}dc{a.get('dc_id', '?')} · 🔐 {two}{RST}")
    return rows

# -------------------------------------------------------------- export core
def gen_key_token():
    b32 = base64.b32encode(secrets.token_bytes(32)).decode().rstrip("=")
    groups = [b32[i:i + 5] for i in range(0, len(b32), 5)]
    return "POLY-" + "-".join(groups)

def token_to_bytes(token):
    t = token.strip().upper().replace("POLY-", "").replace("-", "")
    t += "=" * (-len(t) % 8)
    return base64.b32decode(t)

def parse_key_token(token):
    """Parse an export key. Tolerates pasted whitespace/newlines/case,
    rejects anything that is not a plausible key — raises ValueError.
    A bad KEY string must NEVER be treated as a bad FILE."""
    t = re.sub(r"\s+", "", str(token or ""))
    t = t.upper().replace("POLY-", "").replace("-", "")
    t += "=" * (-len(t) % 8)
    try:
        raw = base64.b32decode(t)
    except Exception:
        raise ValueError("not a valid key string")
    if not (16 <= len(raw) <= 128):
        raise ValueError("key has the wrong size")
    return raw

def u32(n):
    return struct.pack(">I", n)

def build_bundle(manifest, salt, nonce, ct):
    return (MAGIC + bytes([BUNDLE_FMT]) +
            u32(len(manifest)) + manifest +
            u32(len(salt)) + salt +
            u32(len(nonce)) + nonce +
            u32(len(ct)) + ct)

def parse_bundle(data):
    class StructErr(Exception):
        pass
    def rd(off, n):
        if off + n > len(data):
            raise StructErr("truncated")
        return data[off:off + n]
    i = len(MAGIC) + 1
    mlen = struct.unpack(">I", rd(i, 4))[0]; i += 4
    manifest = rd(i, mlen); i += mlen
    slen = struct.unpack(">I", rd(i, 4))[0]; i += 4
    salt = rd(i, slen); i += slen
    nlen = struct.unpack(">I", rd(i, 4))[0]; i += 4
    nonce = rd(i, nlen); i += nlen
    clen = struct.unpack(">I", rd(i, 4))[0]; i += 4
    ct = rd(i, clen)
    if i + clen != len(data):
        raise StructErr("trailing bytes")
    if len(salt) != 32:
        raise StructErr("invalid salt length")
    if len(nonce) != 12:
        raise StructErr("invalid nonce length")
    if len(ct) < 16:
        raise StructErr("ciphertext is too short")
    try:
        json.loads(manifest.decode("utf-8"))
    except Exception as e:
        raise StructErr("invalid manifest") from e
    return StructErr, manifest, salt, nonce, ct

def export_payload(accs, vk):
    sessions = []
    for a in accs:
        sessions.append({"id": a["id"], "data":
                         base64.b64encode(load_session_bytes(vk, a["id"])).decode()})
    return json.dumps({"fmt": BUNDLE_FMT, "accounts": accs, "sessions": sessions},
                      sort_keys=True, separators=(",", ":")).encode()

def fingerprint(pt):
    return hashlib.sha256(pt).hexdigest()[:12]

def make_bundle(accs, vk, token):
    pt = export_payload(accs, vk)
    manifest = {
        "app": "polygram", "fmt": BUNDLE_FMT,
        "export_id": secrets.token_hex(8),
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "client_version": APP_VERSION,
        "accounts": len(accs),
        "phones": [a["phone"] for a in accs],
        "fingerprint": fingerprint(pt),
    }
    aad = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac("sha256", token_to_bytes(token), salt,
                             EXPORT_KDF_ITERS, 32)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(dk).encrypt(nonce, pt, aad)
    return build_bundle(aad, salt, nonce, ct), manifest, pt

# ------------------------------------------------------------- quarantine
def quarantine(data, name, reason):
    q = sub_dir("quarantine")
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name or "file")[:40]
    fp = os.path.join(q, f"{ts}-{safe}.rejected")
    with open(fp, "wb") as f:
        f.write(data)
    os.chmod(fp, 0o600)
    rp = os.path.join(q, f"{ts}-{safe}.report.txt")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"polygram import — HARD STOP\n"
                f"time      : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"file      : {name}\n"
                f"size      : {len(data)} bytes\n"
                f"sha256    : {hashlib.sha256(data).hexdigest()}\n"
                f"reason    : {reason}\n"
                f"action    : import cancelled, nothing loaded\n")
    notify("Polygram — import BLOCKED", reason)
    return fp

def hard_stop(reason, qfile=None):
    clear_line()
    lines = [
        "",
        "  import CANCELLED — nothing was loaded.",
        f"  reason : {reason}",
    ]
    if qfile:
        lines += ["", f"  file   : quarantined",
                  f"  report : {qfile}"]
    lines.append("")
    if ANIM:
        time.sleep(0.25)
    panel(icon("block") + "  Q U A R A N T I N E D", lines, heavy=True, color=DANGER)
    footer(DIM + "⏎ done" + RST)
    try:
        input()
    except EOFError:
        pass

# --------------------------------------------------------------- telegram
class LoginError(Exception):
    pass

def require_telethon():
    if not TELETHON_OK:
        print(DANGER + "  ✗ Telethon missing. In Termux:\n"
                     "      pkg install -y python\n"
                     "      pip install telethon\n" + RST +
              f"     ({TELETHON_ERR})")
        raise SystemExit(2)

def require_crypto():
    if not CRYPTO_OK:
        print(DANGER + "  ✗ cryptography missing. In Termux:\n"
                     "      pkg install -y python-cryptography\n"
                     "      # or: pip install cryptography\n" + RST +
              f"     ({CRYPTO_ERR})")
        raise SystemExit(2)

def make_client(cfg, session_bytes=None):
    d = sub_dir("tmp")
    fd, path = tempfile.mkstemp(prefix="pgsess-", suffix=".session", dir=d)
    if session_bytes:
        os.write(fd, session_bytes)
    os.close(fd)
    os.chmod(path, 0o600)
    client = TelegramClient(path, cfg["api_id"], cfg["api_hash"],
                            device_model=DEVICE_MODEL,
                            system_version="Termux",
                            app_version=APP_VERSION)
    return client, path

def close_client(client, path):
    try:
        client.disconnect()
    except Exception:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass

def guarded(fn):
    try:
        return fn()
    except _err.FloodWaitError as e:
        s = min(int(getattr(e, "seconds", 30) or 30), 60)
        line(WARN + f"  ⚠ Telegram rate limit — waiting {s}s…" + RST)
        time.sleep(s)
        return fn()
    except _err.PhoneMigrateError:
        time.sleep(1)
        return fn()

CODE_RE = re.compile(r"(?<!\d)(\d(?:-?\d){4,14})(?!\d)")

def extract_code(text):
    """Telegram spec: 5-7 digits, optionally with '-'."""
    for m in CODE_RE.finditer(text or ""):
        s = m.group(1).replace("-", "")
        if s.isdigit() and 5 <= len(s) <= 7:
            return s
    return None

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")

def sanitize(s, maxlen=120):
    if not isinstance(s, str):
        s = str(s)
    s = ANSI_RE.sub("", s)
    s = "".join(c for c in s if c == "\n" or 32 <= ord(c) < 0x7f or ord(c) > 0x7f)
    return s.replace("\n", " / ").strip()[:maxlen]

def code_channel(t):
    """Where the code is delivered — short enough for a 50-col phone."""
    return {
        "sms": "📲 SMS to the number",
        "call": "📞 a voice call — code is spoken",
        "flashcall": "📞 a flash call — code is spoken",
        "missedcall": "📞 a missed call — code is spoken",
        "email": "✉️ the account's email",
    }.get(t, "💬 'Telegram' chat (another device)")

def code_guidance(t, phone):
    """Plain-words guidance — every line ≤ ~45 chars (fits phone terminals)."""
    return {
        "sms": "check your SMS inbox — it arrives in seconds",
        "call": "answer the call — the code is spoken",
        "flashcall": ("let the flash call ring once and hang up —\n"
                     "it speaks the code"),
        "missedcall": ("let the missed call drop —\n"
                      "it speaks the code"),
        "email": "open the account's email (check spam too)",
    }.get(t, ("open Telegram where this account is logged in\n"
              "→ code is in the 'Telegram' chat — read it\n"
              "→ come back here and type it (s = SMS)"))

def sms_watcher(q, stop):
    def run():
        while not stop.is_set():
            try:
                r = subprocess.run(["termux-sms-list", "-l", "8"],
                                   capture_output=True, text=True, timeout=6)
                for ln in r.stdout.strip().splitlines():
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    if time.time() - d.get("timestamp", 0) < 360:
                        m = re.search(r"\b(\d{5})\b", d.get("body", ""))
                        if m:
                            q.put(("sms", m.group(1)))
                            return
            except Exception:
                pass
            time.sleep(3)
    import threading
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t

def do_2fa_recovery(client):
    # Emergency path: reset the 2FA password via the recovery email.
    if not RECOVERY_OK:
        raise LoginError("this Telethon build lacks 2FA recovery — "
                         "upgrade: pip install -U telethon")
    try:
        client(RequestPasswordRecoveryRequest())
    except _err.PasswordRecoveryNaError:
        raise LoginError("no recovery email set — enter the 2FA password "
                          "(add one in Telegram: Settings → 2-Step)")
    except _err.PasswordEmptyError:
        raise LoginError("2FA seems disabled — retry with the password")
    line(WARN + "  📧 recovery code sent to the recovery email" + RST)
    rc = ""
    for _ in range(3):
        rc = secret_input(C(BRAND) + "  🔑 recovery code (6 digits): " + RST)
        try:
            client(CheckRecoveryPasswordRequest(code=rc))
            break
        except _err.CodeInvalidError:
            line(DANGER + "  ✗ invalid recovery code" + RST)
        except _err.PasswordRecoveryExpiredError:
            line(DANGER + "  ✗ recovery code expired — requesting a new one" + RST)
            client(RequestPasswordRecoveryRequest())
    else:
        raise LoginError("recovery code rejected 3 times")
    try:
        client(RecoverPasswordRequest(code=rc))
    except Exception as e:
        raise LoginError("recovery failed: " + sanitize(str(e), 80))
    line(WARN + "  ⚠ 2FA password RESET — re-enable it in Telegram" + RST)
    line(WARN + "    app (Settings → 2-Step Verification)" + RST)
    if client.is_user_authorized():
        return client.get_me()
    raise LoginError("recovery complete — re-run add/login to finish")

def do_2fa(client):
    try:
        pw = client(GetPasswordRequest())
        hint = sanitize((pw.hint or "").strip(), 80)
    except Exception:
        hint = ""
    for attempt in (1, 2, 3):
        lines = ["  this account has 2-Step Verification enabled"]
        if hint:
            lines.append(f"  💡 hint : {C(SECRET)}“{hint}”{RST}")
        else:
            lines.append(DIM + "  hint : (none set)" + RST)
        lines.append("")
        panel(C(SECRET) + BOLD + "🔐  TWO-STEP VERIFICATION" + RST, lines)
        val = secret_input(C(BRAND) + f"  🔐 2FA password (try {attempt}/3, or [C] recovery code): " + RST)
        if not val:
            continue
        if val.lower() in ("c", "recovery", "rc"):
            return do_2fa_recovery(client)
        try:
            return guarded(lambda: client.sign_in(password=val))
        except _err.PasswordHashInvalidError:
            line(DANGER + "  ❌ wrong 2FA password — try again" + RST)
            time.sleep(0.5)
        except _err.FloodWaitError as e:
            s2 = min(int(getattr(e, "seconds", 30) or 30), 60)
            line(WARN + f"  ⚠ rate limit — waiting {s2}s" + RST)
            time.sleep(s2)
    return do_2fa_recovery(client)

def wait_code(client, phone, cfg, first_sent):
    """Interactive code entry (5-7 digits). Returns the signed-in User,
    or None if cancelled. Handles resend, force-SMS, expiry, SMS auto-capture."""
    import queue, threading
    sent = first_sent
    deadline = time.time() + max(60, min(int(getattr(sent, "expires_in", 60) or 60), 300))
    buf = []

    def print_chan():
        nt = getattr(sent, "next_type", None)
        line(C(BRAND) + BOLD + "  📩 code via " + code_channel(nt) + RST)
        for gl in code_guidance(nt, phone).split("\n"):
            line(WARN + "  💡 " + gl + RST)
        line(f"  ⌨️  {DIM}[r]{RST}esend   {DIM}[s]{RST}force-SMS   {DIM}[c]{RST}ancel")

    line(C(BRAND) + BOLD + "  📩  LOGIN CODE" + RST)
    line(f"  📞 for number  {C(BRAND) + BOLD}{phone}{RST}")
    line(DIM + "  ✔ sent to Telegram — code is on its way" + RST)
    print_chan()

    q = queue.Queue()
    stop = threading.Event()
    if cfg.get("sms_watch") and getattr(sent, "next_type", "sms") == "sms" \
            and shutil.which("termux-sms-list"):
        line(DIM + "  📲 also watching your SMS inbox (auto-capture)…" + RST)
        sms_watcher(q, stop)

    try:
        with cbreak_input():
            while True:
                now = time.time()
                if now >= deadline:
                    clear_line()
                    line(WARN + "  ⚠ code window ended — resending…" + RST)
                    buf = []
                    try:
                        sent = guarded(lambda: client.send_code_request(phone))
                    except _err.PhoneCodeEmptyError:
                        line(WARN + "  ⚠ Telegram paused codes for this number" + RST)
                        line(DIM + "     (too many attempts) — wait a minute," + RST)
                        line(DIM + "     then press [r] — or [s] to force SMS." + RST)
                        deadline = time.time() + 90
                        continue
                    except Exception as e:
                        raise LoginError("resend failed: " + sanitize(str(e), 80))
                    deadline = time.time() + max(60, min(int(getattr(sent, "expires_in", 60) or 60), 300))
                    print_chan()
                    continue
                left = int(deadline - now)
                cd = DANGER if left < 30 else (WARN if left < 60 else DIM)
                draw(live_line(f"  {spin(0)} ⏱ {cd}{mmss(left)}{RST}  "
                               f"✍️ {WHITE + BOLD}{' '.join(buf) or '▌'}{RST}"))
                r, _, _ = select.select([sys.stdin], [], [], 0.35)
                ch = ""
                if r:
                    ch = sys.stdin.read(1)
                    if ch == "":          # stdin closed (EOF)
                        raise LoginError("input closed — login aborted")
                if ch == "":
                    try:
                        while True:
                            kind, val = q.get_nowait()
                            if kind == "sms":
                                clear_line()
                                toast("code captured from SMS inbox", OK)
                                return finish_signin(client, phone, val)
                    except queue.Empty:
                        pass
                    continue
                if ch == "\x03":
                    clear_line()
                    raise KeyboardInterrupt
                if ch in "\r\n":
                    digits = "".join(buf).replace("-", "")
                    if not (digits.isdigit() and 5 <= len(digits) <= 7):
                        clear_line()
                        line(WARN + "  ⚠ enter the 5–7 digit code" + RST)
                        continue
                    clear_line()
                    try:
                        return finish_signin(client, phone, digits)
                    except LoginError:
                        raise
                    except _err.PhoneCodeInvalidError:
                        line(DANGER + "  ✗ invalid code — try again" + RST)
                        buf = []
                        continue
                    except _err.PhoneCodeExpiredError:
                        line(DANGER + "  ✗ code expired — try again" + RST)
                        buf = []
                        continue
                    except Exception as e:
                        line(DANGER + f"  ✗ {type(e).__name__}: {sanitize(str(e), 100)}" + RST)
                        buf = []
                        continue
                if ch in "0123456789-":
                    if len(buf) < 12:
                        buf.append(ch)
                elif ch in "\x7f\x08":
                    if buf:
                        buf.pop()
                elif ch.lower() == "r":
                    clear_line()
                    line(DIM + "  ⏳ resending code…" + RST)
                    try:
                        sent = guarded(lambda: client.send_code_request(phone))
                    except _err.PhoneCodeEmptyError:
                        line(WARN + "  ⚠ Telegram paused codes for this number" + RST)
                        line(DIM + "     — wait a minute, then press [r] again." + RST)
                        deadline = time.time() + 60
                        continue
                    except Exception as e:
                        raise LoginError("resend failed: " + sanitize(str(e), 80))
                    deadline = time.time() + max(60, min(int(getattr(sent, "expires_in", 60) or 60), 300))
                    buf = []
                    print_chan()
                elif ch.lower() == "s":
                    clear_line()
                    line(DIM + "  📲 forcing delivery via SMS…" + RST)
                    try:
                        sent = guarded(lambda: client.send_code_request(phone, force_sms=True))
                    except _err.PhoneCodeEmptyError:
                        line(WARN + "  ⚠ Telegram paused codes for this number" + RST)
                        line(DIM + "     — wait a minute, then try [r] or [s]." + RST)
                        deadline = time.time() + 60
                        continue
                    except Exception as e:
                        line(DANGER + "  ✗ " + sanitize(str(e), 80) + RST)
                        continue
                    deadline = time.time() + max(60, min(int(getattr(sent, "expires_in", 60) or 60), 300))
                    buf = []
                    print_chan()
                elif ch.lower() in "xcq":
                    clear_line()
                    return None
    finally:
        stop.set()
        clear_line()

def finish_signin(client, phone, code):
    try:
        return guarded(lambda: client.sign_in(phone, code=code))
    except _err.SessionPasswordNeededError:
        return do_2fa(client)

def login_user(client, phone, cfg, code=None, two_fa=None):
    """Full interactive (or direct) login. Returns the User."""
    client.connect()
    if client.is_user_authorized():
        return client.get_me()
    if code is not None:
        def try_sign():
            try:
                return guarded(lambda: client.sign_in(phone, code=code))
            except _err.SessionPasswordNeededError:
                if two_fa:
                    try:
                        return guarded(lambda: client.sign_in(password=two_fa))
                    except _err.PasswordHashInvalidError:
                        raise LoginError("2FA password rejected")
                if not sys.stdin.isatty():
                    raise LoginError("2FA enabled — non-interactive: pass --2fa")
                return do_2fa(client)
            except _err.PhoneCodeInvalidError:
                raise LoginError("code invalid")
            except _err.PhoneCodeExpiredError:
                raise LoginError("code expired")
        try:
            return try_sign()
        except _err.PhoneCodeEmptyError:
            # code supplied directly (--code) without a prior send_code_request
            try:
                client.send_code_request(phone)
            except _err.PhoneCodeEmptyError:
                pass
            return try_sign()
    sent = guarded(lambda: client.send_code_request(phone))
    user = wait_code(client, phone, cfg, sent)
    if user is None:
        raise LoginError("cancelled")
    return user

def load_and_connect(cfg, acc, vk):
    vb = load_session_bytes(vk, acc["id"])
    client, path = make_client(cfg, vb)
    client.connect()
    if not client.is_user_authorized():
        close_client(client, path)
        raise LoginError("this session is no longer valid (revoked?) — "
                         "use security → rotate, or re-add the account")
    return client, path

# ------------------------------------------------------------------- flows
def add_account(cfg, phone=None, code=None, two_fa=None, label=None):
    require_telethon()
    vk = ensure_unlock()
    preflight(cfg)
    accs = load_accounts()
    interactive = sys.stdin.isatty()
    if not interactive and not code:
        raise LoginError("non-interactive: --phone and --code required")
    if not phone:
        if not interactive:
            raise LoginError("non-interactive: --phone required")
        line(DIM + "  💡 number → OTP → 2FA (hint) → ✅ saved" + RST)
        line()
        while True:
            cc = ask(C(BRAND) + "  🌍 country code (e.g. 1 for US)" + RST)
            nn = ask(C(BRAND) + "  📱 number (no country code)" + RST)
            phone = "+" + re.sub(r"\D", "", cc) + re.sub(r"\D", "", nn)
            if not re.fullmatch(r"\+\d{8,15}", phone):
                line(DANGER + f"  ✗ invalid: {phone} (need 8–15 digits total)" + RST)
                continue
            line(f"  📞 full number being sent → {C(BRAND) + BOLD}{phone}{RST}")
            if confirm("  send the login request to this number? ", default=True):
                break
            line(DIM + "  — not right, re-enter it" + RST)
    elif not re.fullmatch(r"\+\d{8,15}", phone):
        raise LoginError(f"invalid phone number: {phone!r} (expected +country + digits)")
    for a in accs:
        if a["phone"] == phone:
            line(WARN + f"  ⚠ {phone} already in the vault as "
                       f"“{a.get('label')}” — adding a NEW session for it" + RST)
            if not interactive or confirm("  continue?"):
                break
            return
    client, path = make_client(cfg)
    try:
        try:
            user = login_user(client, phone, cfg, code=code, two_fa=two_fa)
        except Exception as e:
            raise LoginError(sanitize(str(e), 120))
        try:
            pw = client(GetPasswordRequest())
            has_2fa = bool(getattr(pw, "has_password", False))
        except Exception:
            has_2fa = None
        dc = getattr(client.session, "dc_id", 0)
        client.disconnect()
        data = open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    acc_id = next_id(accs)
    uname = user.username if getattr(user, "username", None) else None
    if label is None:
        label = ask(C(BRAND) + "  🏷 label for this account" + RST,
                    default=(uname or "Account " + acc_id[-4:])) \
            if sys.stdin.isatty() else (uname or "Account " + acc_id[-4:])
    meta = {
        "id": acc_id,
        "label": sanitize(label or acc_id, 64),
        "phone": phone,
        "username": uname,
        "user_id": int(user.id),
        "dc_id": dc,
        "has_2fa": has_2fa,
        "star": False,
        "added": time.strftime("%Y-%m-%d %H:%M"),
        "last_used": time.strftime("%Y-%m-%d %H:%M"),
    }
    store_session(vk, acc_id, data)
    accs.append(meta)
    save_accounts(accs)
    audit("add", f"phone={phone} id={acc_id}")
    notify("Polygram — account added", phone)
    success("✅  A C C O U N T   A D D E D  ✦", [
        f"  {C(BRAND) + BOLD}{phone}{RST}   {C(SECRET)}@{uname or '—'}{RST}   "
        f"{DIM}dc{dc}{RST} · 2FA:{C(OK) if has_2fa else C(WARN)}{'on' if has_2fa else 'off'}{RST}",
        f"  💾 session  {C(BRAND)}{acc_id}{RST}  (encrypted in vault)",
        "",
        DIM + "  💡 next: press 2 (Login) to relay a code" + RST,
    ])

def relay_wait(client, seconds=300):
    """Watch the 777000 login-notification chat for a new code.
    Returns (code, raw_text) or None (cancelled/timeout)."""
    import threading
    found_evt = threading.Event()
    found = {}

    def on_code(e):
        txt = e.message.message or ""
        c = extract_code(txt)
        if c and not found_evt.is_set():
            found["code"] = c
            found["text"] = txt
            found_evt.set()

    seen = set()
    try:
        for m in client.get_messages(LOGIN_NOTIFY_ID, limit=20) or []:
            seen.add(m.id)
    except Exception:
        pass
    client.add_event_handler(on_code, _ev.NewMessage(from_users=LOGIN_NOTIFY_ID))
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            left = int(deadline - time.time())
            cd = DANGER if left < 30 else (WARN if left < 60 else DIM)
            draw(live_line(f"  {spin(0)} 📡 waiting for the code… ⏱ {cd}{mmss(left)}{RST}"
                           f"   {DIM}[x] cancel{RST}"))
            if found_evt.wait(0.5):
                break
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                ch = sys.stdin.read(1)
                if ch.lower() in "xcq":
                    clear_line()
                    return None
            try:
                for m in client.get_messages(LOGIN_NOTIFY_ID, limit=5) or []:
                    if m.id in seen:
                        continue
                    seen.add(m.id)
                    c = extract_code(m.message or "")
                    if c:
                        found["code"] = c
                        found["text"] = m.message or ""
                        found_evt.set()
                        break
            except Exception:
                pass
        clear_line()
        if found_evt.is_set():
            return found["code"], found.get("text", "")
        return None
    finally:
        try:
            client.remove_event_handler(on_code)
        except Exception:
            pass

def wait_new_device(client, before, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            for a in client(GetAuthorizationsRequest()):
                if a.id not in before:
                    return a
        except Exception:
            pass
        time.sleep(2)
    return None

def login_relay(cfg, acc_ref=None):
    require_telethon()
    vk = ensure_unlock()
    accs = load_accounts()
    if not accs:
        raise LoginError("vault is empty — add an account first")
    if len(accs) == 1 or acc_ref is not None:
        acc = find_account(accs, acc_ref)
        if acc is None:
            raise LoginError("account not found")
    else:
        line()
        line(BOLD + "  L O G I N   R E L A Y" + RST)
        line()
        idx = choose("  relay login for:", [
            f"{a['phone']}   {DIM}{a.get('username') or '—'}{RST}" for a in accs])
        acc = accs[idx]
    preflight(cfg)
    line()
    client, path = make_client(cfg, load_session_bytes(vk, acc["id"]))
    try:
        client.connect()
        if not client.is_user_authorized():
            raise LoginError("session invalid (revoked?) — security → rotate")
        before = set()
        try:
            before = {a.id for a in client(GetAuthorizationsRequest())}
        except Exception:
            pass
        panel(C(BRAND) + BOLD + "📡  RELAY — " + acc["phone"] + RST, [
            "  On your NEW device:",
            f"   1️⃣ open Telegram",
            f"   2️⃣ enter   {WHITE + BOLD}{acc['phone']}{RST}",
            f"   3️⃣ the code comes {C(OK)}HERE{RST} — not by SMS",
            "",
            WARN + "  💡 the code usually appears here within ~30 s —" + RST,
            WARN + "     keep this window open, don't close Termux." + RST,
        ])
        found = relay_wait(client)
        if not found:
            fail("⚠ NO CODE RECEIVED", [
                "  nothing arrived in 5:00.",
                "  on the new device tap “I didn't get the code”",
                "  (it may switch to SMS) and try again.",
            ])
            return
        code, text = found
        lines = [
            f"  from    {WHITE + BOLD}{acc['phone']}{RST}",
            f"  device  {DIM}{sanitize(text, 90) or '—'}{RST}",
            "",
        ]
        if ANIM:
            panel(icon("zap") + "  L O G I N   C O D E", lines, heavy=True, color=SECRET)
            d = []
            for ch in code:
                d.append("   " + ch)
                print("".join(d).ljust(len(code) * 4 + 2), end="\r"); OUT.flush()
                time.sleep(0.12)
            print()
        else:
            panel(icon("zap") + "  L O G I N   C O D E",
                  lines + [f"        {WHITE + BOLD}{code}   {RST}"],
                  heavy=True, color=SECRET)
        line()
        line("  " + WARN + BOLD + "⌨️  type it into the Telegram app NOW" + RST)
        while True:
            k = ask("  [⏎] entered in app · [R] report/kill this code")
            if k.lower() == "r":
                if InvalidateSignInCodesRequest is None:
                    raise LoginError("telethon too old to invalidate codes — upgrade")
                try:
                    client(InvalidateSignInCodesRequest(codes=[code]))
                    toast("code invalidated — it can no longer be used", OK)
                    audit("report-code", f"acc={acc['id']}")
                except Exception as e:
                    line(DANGER + f"  ✗ {sanitize(str(e), 100)}" + RST)
                break
            new = wait_new_device(client, before)
            desc = ""
            if new is not None:
                desc = sanitize(getattr(new, "title", None) or new.device_model, 60) \
                    + (f" · {new.ip}" if getattr(new, "ip", None) else "")
            success("✅  L O G G E D   I N", [
                f"  {C(BRAND)}📱 {desc or 'new device (not detected yet — check Settings → Devices)'}{RST}",
                f"  {C(BRAND)}{acc['phone']}{RST} is now live on the new device",
                "",
                DIM + "  💡 the SIM is no longer needed" + RST,
            ])
            audit("relay", f"acc={acc['id']}")
            break
    finally:
        close_client(client, path)

def key_ceremony(token, interactive):
    line()
    masked = "•••• •••• •••• •••• •••• ••••"
    if not interactive:
        line(SECRET + BOLD + "  YOUR EXPORT KEY (save it — shown ONCE):" + RST)
        line(SECRET + BOLD + "  " + token + RST)
        line(DANGER + "  ⚠ copy it NOW — it is not stored anywhere." + RST)
        return True
    parts = token.split("-")
    mid = len(parts) // 2
    g1, g2 = "-".join(parts[:mid]), "-".join(parts[mid:])
    lines = [
        "",
        SECRET + BOLD + f"  {masked}" + RST,
        "",
        "  Save it in your PASSWORD MANAGER,",
        "  SEPARATE from the file. Both halves",
        "  together = the vault.",
        "",
    ]
    panel(C(SECRET) + BOLD + "🗝  Y O U R   E X P O R T   K E Y" + RST, lines, heavy=True, color=SECRET)
    while True:
        k = ask(C(BRAND) + "  🗝 [V]iew · [C]lipboard · [Q]R · [D]one" + RST)
        k = k.lower()
        if k in ("v", "view"):
            line(SECRET + BOLD + "  " + g1 + RST)
            line(SECRET + BOLD + "  " + g2 + RST)
        elif k in ("c", "copy"):
            if clipboard_set(token):
                line(DANGER + "  ⚠ on your clipboard — paste it into your"
                             "    password manager now." + RST)
                line(DIM + "    (Android clipboard is shared with other apps)" + RST)
            else:
                line(DANGER + "  ✗ clipboard unavailable — [V] to view the key" + RST)
        elif k in ("q", "qr"):
            _show_qr(token)
        elif k in ("d", "done", "y", "yes", ""):
            if not confirm("  I saved the key somewhere safe: "):
                continue
            return True

def _show_qr(token):
    try:
        import segno
        qr = segno.make(token)
        line(SECRET)
        qr.printer(dark="▓", light="  ")
        line(RST)
    except Exception:
        try:
            import qrcode
            qr = qrcode.make(token)
            import io
            buf = io.StringIO()
            qr.print_tty(modulo=("  ", "▓"))
        except Exception:
            line(WARN + "  ⚠ QR needs a package:  pip install segno" + RST)

def do_export(cfg, out=None, only=None):
    require_crypto()
    vk = ensure_unlock()
    accs = load_accounts()
    if only:
        a = find_account(accs, only)
        if a is None:
            raise LoginError("account not found")
        accs = [a]
    if not accs:
        raise LoginError("vault is empty — nothing to export")
    line()
    line(C(BRAND) + BOLD + "  📤  E X P O R T   V A U L T" + RST + DIM + "  (all accounts · one file · one key)" + RST)
    line()
    for a in accs:
        line(f"   ●  {a['phone']}   {DIM}{a.get('username') or '—'}{RST}")
    line()
    line(DIM + "  export is a pure read — no account is logged out" + RST)
    if sys.stdin.isatty() and not confirm("  export? ", default=True):
        line(DIM + "  aborted — nothing written" + RST)
        return
    token = gen_key_token()
    if not key_ceremony(token, sys.stdin.isatty()):
        raise LoginError("cancelled")
    bundle, manifest, pt = make_bundle(accs, vk, token)
    name = f"polygram-{time.strftime('%Y%m%d')}-{len(accs)}accts.pgs"
    dl = None if out else android_download_dir()
    if out:
        fp = os.path.abspath(out)
    elif dl:
        fp = os.path.join(dl, name)              # Android → Files app → Download
    else:
        fp = os.path.join(sub_dir("exports"), name)
    try:
        os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
    except OSError:
        pass
    tmp = fp + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bundle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, fp)
    local = ""
    try:                                          # local backup copy in the vault
        le = os.path.join(sub_dir("exports"), name)
        if os.path.abspath(le) != os.path.abspath(fp):
            shutil.copy2(fp, le)
            os.chmod(le, 0o600)
            local = f"\n  backup  : {le}"
    except OSError:
        pass
    audit("export", f"accounts={len(accs)} file={os.path.basename(fp)}")
    where = (WARN + "  📂 it's in your Files app → Download" + RST if dl and not out else "")
    success("✅  E X P O R T   C O M P L E T E", [
        f"  📁 file       {C(BRAND)}{os.path.basename(fp)}{RST}",
        f"  📦 size       {len(bundle)} bytes",
        f"  🔏 fingerprint {C(SECRET)}{manifest['fingerprint']}{RST}",
        f"  🗝 key        {DIM}the one you just saved (password manager){RST}",
        "",
        DIM + "  💡 keep the .pgs file and the key in TWO places" + RST,
    ])
    if where:
        line(where)
    line(f"  📂 path  {C(BRAND)}{fp}{RST}")
    if local:
        line(f"  💾 backup {DIM}{local.strip()[len('backup  :'):]}{RST}")
    if dl is None and os.path.isdir(os.path.expanduser("~/storage/shared")) \
            and not os.path.isdir(os.path.expanduser("~/storage/shared/Download")):
        line(storage_hint())

def read_bundle_input(file, clipboard):
    if clipboard:
        raw = clipboard_get()
        if raw is None:
            raise LoginError("clipboard unavailable (Termux:API?)")
        raw = raw.strip()
        try:
            return base64.b64decode(raw, validate=True), "(clipboard)"
        except Exception:
            return raw.encode(), "(clipboard)"
    if not file:
        raise LoginError("non-interactive: --file required")
    if not os.path.exists(file):
        raise LoginError(f"file not found: {file}")
    with open(file, "rb") as f:
        return f.read(), os.path.basename(file)

def snapshot_vault_backup():
    """Best-effort copy of the current vault (accounts + sessions) before a
    destructive import — a restore can always be undone from here."""
    bdir = sub_dir("last-restore-backup")
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(bdir, ts)
    try:
        os.makedirs(dst, exist_ok=True)
        if os.path.exists(accounts_path()):
            shutil.copy2(accounts_path(), os.path.join(dst, "accounts.json"))
        sdir = os.path.join(POLYGRAM_HOME, "sessions")
        if os.path.isdir(sdir):
            for f in os.listdir(sdir):
                if f.endswith(".enc"):
                    shutil.copy2(os.path.join(sdir, f), os.path.join(dst, f))
        snaps = sorted(d for d in os.listdir(bdir)
                       if os.path.isdir(os.path.join(bdir, d)))
        for old in snaps[:-3]:
            shutil.rmtree(os.path.join(bdir, old), ignore_errors=True)
        return dst
    except OSError:
        return None

def prompt_key():
    """The key is NEVER read from or written to a file — paste it,
    or take it from the clipboard (it should live in a password
    manager, not on this phone's storage)."""
    if sys.stdin.isatty():
        k = ask("  export key  (paste it, or [C]lipboard)")
        if k.lower() in ("c", "clipboard"):
            v = clipboard_get()
            if v is None:
                raise LoginError("clipboard unavailable")
            return v
        return k
    raise LoginError("non-interactive: the key is never a file — run "
                     "interactively and paste it (or --clipboard-key)")

def import_bundle(cfg, file=None, key=None, clipboard_key=False, verify=False):
    require_crypto()
    vk = ensure_unlock()
    data, srcname = read_bundle_input(file, clipboard_key)
    line()
    line(BOLD + f"  I M P O R T · {srcname}" + RST)
    line()
    # ---- L1 GATE
    reason = None
    if len(data) > MAX_BUNDLE:
        reason = f"gate: file too large ({len(data)} > {MAX_BUNDLE})"
    elif data[:len(MAGIC)] != MAGIC:
        reason = "gate: not a POLYGRAM bundle (bad magic bytes)"
    elif len(data) < len(MAGIC) + 1 or data[len(MAGIC)] != BUNDLE_FMT:
        reason = f"gate: unsupported format version {data[len(MAGIC)] if len(data) > len(MAGIC) else '?'}"
    if reason:
        qf = quarantine(data, srcname, reason)
        hard_stop(reason, qf)
        audit("import", f"file={srcname} reason={reason}", ok=False)
        return 2
    line(OK + "   🛡  Gate     ✓" + RST + DIM + "   magic · size · type ok" + RST)
    # ---- L2 DECRYPT
    if key is None:
        key = prompt_key()
    attempts = 0
    while True:
        try:
            StructErr, manifest_b, salt, nonce, ct = parse_bundle(data)
        except Exception as e:
            reason = f"structural error: {sanitize(str(e), 80)}"
            qf = quarantine(data, srcname, reason)
            hard_stop(reason, qf)
            audit("import", f"file={srcname} reason=structure", ok=False)
            return 2
        try:
            key_bytes = parse_key_token(key)
        except ValueError:
            # the KEY looks wrong — the file may be perfectly fine
            if not sys.stdin.isatty():
                raise LoginError("invalid key format — expected POLY-XXXXX-XXXXX-…")
            line(DANGER + "   ✗ that isn't a valid key — expected:" + RST)
            line(DIM + "     POLY-XXXXX-XXXXX-XXXXX-XXXXX-…" + RST)
            key = prompt_key()
            continue
        try:
            dk = hashlib.pbkdf2_hmac("sha256", key_bytes, salt,
                                     EXPORT_KDF_ITERS, 32)
            pt = AESGCM(dk).decrypt(nonce, ct, manifest_b)
            break
        except InvalidTag:
            attempts += 1
            if attempts >= 5:
                qf = quarantine(data, srcname, "decrypt: integrity failed after 5 "
                                               "attempts (wrong key or tampered file)")
                hard_stop("wrong key or tampered file (5 attempts)", qf)
                audit("import", f"file={srcname} reason=integrity", ok=False)
                return 2
            line(DANGER + f"   🚫 wrong key or tampered file  (attempt {attempts}/5)" + RST)
            if attempts == 3 and sys.stdin.isatty():
                line(WARN + "   3 failures — 30 s cool-down" + RST)
                time.sleep(30)
            if sys.stdin.isatty():
                key = prompt_key()
        except Exception as e:
            qf = quarantine(data, srcname, f"decrypt: structural error ({e})")
            hard_stop(f"structural error: {sanitize(str(e), 80)}", qf)
            audit("import", f"file={srcname} reason=structure", ok=False)
            return 2
    line(OK + "   🔓  Decrypt  ✓" + RST + DIM + "   AES-256-GCM ok" + RST)
    # ---- L3 READ
    try:
        obj = json.loads(pt.decode())
        if obj.get("fmt") != BUNDLE_FMT:
            raise ValueError("payload fmt mismatch")
        in_accounts = obj["accounts"]
        in_sessions = obj["sessions"]
        if len(in_accounts) != len(in_sessions):
            raise ValueError("account/session count mismatch")
        manifest = json.loads(manifest_b.decode())
        if manifest.get("fingerprint") != fingerprint(pt):
            raise ValueError("fingerprint mismatch (file altered?)")
        for a, s in zip(in_accounts, in_sessions):
            a["label"] = sanitize(str(a.get("label", "")), 64) or "Imported"
            if not re.fullmatch(r"\+\d{8,15}", str(a.get("phone", ""))):
                raise ValueError("bad phone in payload")
            if not (isinstance(a.get("user_id"), int) and 0 < a["user_id"] < 2 ** 62):
                raise ValueError("bad user_id in payload")
            if not (isinstance(a.get("dc_id"), int) and 0 <= a["dc_id"] <= 9):
                raise ValueError("bad dc in payload")
            raw = base64.b64decode(s["data"], validate=True)
            if not (64 <= len(raw) <= MAX_BUNDLE):
                raise ValueError("session payload out of bounds")
            a["_raw"] = raw
        line(OK + f"   📖  Read     ✓" + RST + DIM + f"   {len(in_accounts)} records · "
                                              f"fingerprint {manifest['fingerprint']} ✓" + RST)
    except Exception as e:
        qf = quarantine(data, srcname, f"read: malformed payload ({e})")
        hard_stop(f"malformed payload: {sanitize(str(e), 80)}", qf)
        audit("import", f"file={srcname} reason=read", ok=False)
        return 2
    # ---- L4 CONFIRM
    line()
    panel(C(BRAND) + BOLD + "📥  READY TO IMPORT" + RST, [
        f"  {DIM}{manifest.get('exported_at', '?')} · exported with Polygram "
        f"{manifest.get('client_version', '?')}{RST}",
        "",
    ] + [f"   {i}  {a['phone']}   {DIM}{a.get('username') or '—'}{RST}   "
         f"{DIM}dc{a['dc_id']}{RST}" for i, a in enumerate(in_accounts, 1)])
    cur = load_accounts()
    mode = "restore"
    if cur:
        if sys.stdin.isatty():
            line(WARN + f"  ⚖ your vault already has {len(cur)} account(s)" + RST)
            line("   1  🔄 RESTORE — the vault becomes this bundle")
            line("   2  ➕  MERGE — keep current, add/replace matches")
            mv = ask("  import mode", default="1").lower()
            if mv in ("2", "m", "merge"):
                mode = "merge"
            elif mv in ("q", "c", "cancel"):
                line(DIM + "  aborted — nothing changed" + RST)
                return 0
    actions = {}
    if mode == "merge":
        for a in in_accounts:
            dup = next((c for c in cur if c["phone"] == a["phone"]), None)
            if dup:
                line(WARN + f"  ⚠ {a['phone']} already in vault as “{dup.get('label')}”" + RST)
                if sys.stdin.isatty():
                    v = ask("     [S]kip · [R]eplace · [C]opy-as-new", default="s").lower()
                    actions[a["phone"]] = {"s": "skip", "r": "replace", "c": "copy"}.get(v[0], "skip")
                else:
                    actions[a["phone"]] = "skip"
                line(DIM + f"     → {actions[a['phone']]}" + RST)
    if sys.stdin.isatty() and not type_yes("  import (commit)?"):
        line(DIM + "  aborted — nothing changed" + RST)
        return 0
    # ---- COMMIT
    try:
        backup_dir = snapshot_vault_backup() if cur else None
        imported = []
        if mode == "restore":
            # the vault BECOMES this bundle (overwrites current data)
            sdir = os.path.join(POLYGRAM_HOME, "sessions")
            for f in os.listdir(sdir):
                if f.endswith(".enc"):
                    try:
                        os.unlink(os.path.join(sdir, f))
                    except OSError:
                        pass
            for a in in_accounts:
                meta = {
                    "id": a["id"],
                    "label": a.get("label") or a["id"],
                    "phone": a["phone"],
                    "username": sanitize(str(a.get("username") or ""), 64) or None,
                    "user_id": a["user_id"],
                    "dc_id": a.get("dc_id", 0),
                    "has_2fa": bool(a.get("has_2fa")),
                    "star": bool(a.get("star")),
                    "added": a.get("added", "?"),
                    "last_used": time.strftime("%Y-%m-%d %H:%M"),
                }
                store_session(vk, a["id"], a["_raw"])
                imported.append(meta)
            save_accounts(imported)
        else:
            for a in in_accounts:
                act = actions.get(a["phone"], "add")
                if act == "skip":
                    continue
                dup = next((c for c in load_accounts() if c["phone"] == a["phone"]), None)
                if act == "replace" and dup:
                    acc_id = dup["id"]
                    meta = dict(dup)
                else:
                    accs_now = load_accounts()
                    acc_id = next_id(accs_now)
                    meta = {"id": acc_id, "star": False,
                            "added": time.strftime("%Y-%m-%d %H:%M")}
                meta.update({
                    "label": a.get("label") or meta.get("label") or acc_id,
                    "phone": a["phone"],
                    "username": sanitize(str(a.get("username") or ""), 64) or None,
                    "user_id": a["user_id"],
                    "dc_id": a["dc_id"],
                    "has_2fa": bool(a.get("has_2fa")),
                    "last_used": time.strftime("%Y-%m-%d %H:%M"),
                })
                store_session(vk, acc_id, a["_raw"])
                accs_now = load_accounts()
                accs_now = [c for c in accs_now if c["id"] != acc_id]
                accs_now.append(meta)
                save_accounts(accs_now)
                imported.append(meta)
        audit("import", f"file={srcname} mode={mode} accounts={len(imported)}")
        rows = [f"   ✓  {m['phone']}   {DIM}{m['id']}{'  (replaced)' if actions.get(m['phone']) == 'replace' else ''}{RST}"
                for m in imported] or ["   (all duplicates — skipped)"]
        extra = ([DIM + "  💾 previous vault auto-backed up (undo-able)" + RST]
                 if (mode == "restore" and backup_dir) else [])
        success("✅  I M P O R T   C O M P L E T E",
                [f"  {C(OK)}{len(imported)} account(s) restored — logged-in state, ready to use{RST}"]
                + rows + extra + [""]
                + (["  [V]erify all against Telegram now (online)"] if verify else []))
        if verify and imported:
            for m in imported:
                st = check_live(cfg, vk, m)
                if st is True:
                    line(f"   ✓  {m['phone']}   {OK}authorized{RST}")
                else:
                    line(f"   ✗  {m['phone']}   {DANGER}{st}{RST}")
        notify("Polygram — import complete", f"{len(imported)} account(s)")
        return 0
    except Exception as e:
        line(DANGER + f"  ✗ commit failed: {sanitize(str(e), 100)}" + RST)
        audit("import", f"file={srcname} commit-fail", ok=False)
        return 1

def check_live(cfg, vk, acc):
    require_telethon()
    client, path = make_client(cfg, load_session_bytes(vk, acc["id"]))
    try:
        client.connect()
        return client.is_user_authorized()
    except _err.AuthKeyUnregisteredError:
        return "revoked — re-add"
    except _err.FloodWaitError:
        return "rate-limited — try later"
    except Exception as e:
        return f"error: {sanitize(str(e), 60)}"
    finally:
        close_client(client, path)

def rotate_account(cfg, acc_ref=None):
    require_telethon()
    vk = ensure_unlock()
    accs = load_accounts()
    if not accs:
        raise LoginError("vault is empty")
    acc = find_account(accs, acc_ref)
    if acc is None:
        acc = None
        idx = choose("  rotate which account?", [a["phone"] for a in accs])
        acc = accs[idx]
    line()
    panel(icon("security") + "  ROTATE — " + acc["phone"], [
        "  Re-login makes a NEW auth key — every old export",
        "  bundle for this account stops working.",
        "  You will need the OTP once (relay can help).",
    ])
    if not type_yes("  rotate?"):
        line(DIM + "  cancelled" + RST)
        return
    client, path = make_client(cfg, load_session_bytes(vk, acc["id"]))
    try:
        client.connect()
        if not client.is_user_authorized():
            raise LoginError("session invalid — cannot rotate")
        client.sign_out()
        line(DIM + "  old session gone — logging back in (new key)…" + RST)
        user = login_user(client, acc["phone"], cfg)
        try:
            pw = client(GetPasswordRequest())
            has_2fa = bool(getattr(pw, "has_password", False))
        except Exception:
            has_2fa = acc.get("has_2fa")
        dc = getattr(client.session, "dc_id", 0)
        client.disconnect()
        data = open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    store_session(vk, acc["id"], data)
    accs = load_accounts()
    for i, a in enumerate(accs):
        if a["id"] == acc["id"]:
            accs[i] = dict(a, username=user.username, user_id=int(user.id),
                           has_2fa=has_2fa, dc_id=dc,
                           last_used=time.strftime("%Y-%m-%d %H:%M"))
    save_accounts(accs)
    audit("rotate", f"acc={acc['id']}")
    success("✓  R O T A T E D", [
        f"  {acc['phone']} — new auth key live",
        f"  all older exports for this account are now DEAD",
    ])

def report_code(cfg, acc_ref=None, code=None):
    require_telethon()
    vk = ensure_unlock()
    accs = load_accounts()
    if not accs:
        raise LoginError("vault is empty")
    acc = find_account(accs, acc_ref)
    if acc is None:
        idx = choose("  which account requested the code?", [a["phone"] for a in accs])
        acc = accs[idx]
    if code is None:
        code = ask("  the code you did NOT request (5–7 digits)")
    code = code.replace("-", "").strip()
    if not (code.isdigit() and 5 <= len(code) <= 7):
        raise LoginError("code must be 5–7 digits")
    if InvalidateSignInCodesRequest is None:
        raise LoginError("Telethon too old for invalidation — upgrade")
    client, path = make_client(cfg, load_session_bytes(vk, acc["id"]))
    try:
        client.connect()
        client(InvalidateSignInCodesRequest(codes=[code]))
        audit("report-code", f"acc={acc['id']}")
        success("✓  C O D E   K I L L E D", [f"  {code} can no longer be used"])
    finally:
        close_client(client, path)

# ----------------------------------------------------------------- screens
def preflight(cfg):
    """Visible pre-flight check before any network flow (add / login)."""
    line()
    line(C(BRAND) + BOLD + "  🔎  PRE-FLIGHT CHECK" + RST)
    ok_id = isinstance(cfg.get("api_id"), int) and cfg["api_id"] > 0
    h = str(cfg.get("api_hash") or "")
    ok_hash = bool(re.fullmatch(r"[0-9a-fA-F]{32}", h))
    line(f"   {'✔' if ok_id else '✗'}  api_id     {C(BRAND)}{cfg.get('api_id')}{RST}")
    if ok_hash:
        line(f"   ✔  api_hash   {DIM}{h[:4]}…{h[-4:]}{RST}")
    else:
        line(f"   ✗  api_hash   {C(DANGER)}missing / invalid{RST}")
    line(f"   ✔  vault      {C(OK)}unlocked{RST}")
    if TELETHON_OK:
        line(f"   ✔  telethon   {C(OK)}{__import__('telethon').__version__}{RST}")
    else:
        line(f"   ✗  telethon   {C(DANGER)}missing{RST}")
    if not (ok_id and ok_hash):
        raise LoginError("api credentials look wrong — open settings (8) → 2")
    line(DIM + "  ✔ all good — going to Telegram…" + RST)

def pick_account(cfg, prompt):
    accs = load_accounts()
    if not accs:
        raise LoginError("vault is empty — add an account first")
    if len(accs) == 1:
        return accs[0], accs
    idx = choose(prompt, [f"{a['phone']}   {DIM}{a.get('username') or '—'}{RST}"
                          for a in accs])
    return accs[idx], accs

def accounts_screen(cfg):
    vk = ensure_unlock()
    while True:
        clear()
        header(cfg)
        accs = load_accounts()
        if not accs:
            line(DIM + "   (no accounts yet — " + icon("add") + " add is menu item 1)" + RST)
        for i, a in enumerate(accounts_table(accs), 1):
            line(f"   {i}  " + a)
        line()
        try:
            ch = input(DIM + "  [1-N · r ren · s star · d dev · x del · q]: " + RST).strip().lower()
        except EOFError:
            return
        if ch == "q" or ch == "":
            return
        accs = load_accounts()
        if ch.isdigit() and 1 <= int(ch) <= len(accs):
            a = accs[int(ch) - 1]
            panel(C(BRAND) + BOLD + "👤  " + a["phone"] + RST, [
                f"  🏷 label    {a.get('label')}",
                f"  🆔 id       {C(BRAND)}{a['id']}{RST}    "
                f"@user  {C(SECRET)}{a.get('username') or '—'}{RST}",
                f"  🌐 dc       {a.get('dc_id')}    🔐 2FA  "
                f"{C(OK) if a.get('has_2fa') else C(WARN)}"
                f"{'on' if a.get('has_2fa') else 'off'}{RST}",
                f"  📅 added    {DIM}{a.get('added', '?')}{RST}    "
                f"last used  {DIM}{a.get('last_used', '?')}{RST}",
            ])
            continue
        if not accs:
            continue
        target = None
        if ch in ("r", "s", "e", "d", "x"):
            i2 = ask("  which account (1-" + str(len(accs)) + ")")
            if i2.isdigit() and 1 <= int(i2) <= len(accs):
                target = accs[int(i2) - 1]
        if ch == "r" and target:
            nl = ask("  new label", default=target.get("label"))
            target["label"] = sanitize(nl, 64)
            save_accounts(accs)
            audit("rename", f"acc={target['id']}")
            toast("label updated")
        elif ch == "s" and target:
            target["star"] = not target.get("star")
            save_accounts(accs)
            toast("★ toggled")
        elif ch == "e" and target:
            do_export(cfg, only=target["id"])
        elif ch == "d" and target:
            devices_screen(cfg, target)
        elif ch == "x" and target:
            panel(icon("block") + "  DELETE — " + target["phone"], [
                "  removes the LOCAL session from this vault.",
                "  (may also log the account out everywhere)",
            ])
            if type_yes("  delete?"):
                logout_all = confirm("  also log out on all devices?", default=False)
                vb = None
                if logout_all:
                    try:
                        vb = load_session_bytes(vk, target["id"])
                    except Exception:
                        vb = None
                try:
                    os.unlink(os.path.join(POLYGRAM_HOME, "sessions", target["id"] + ".enc"))
                except OSError:
                    pass
                save_accounts([a for a in accs if a["id"] != target["id"]])
                audit("remove", f"acc={target['id']}")
                if logout_all and vb:
                    try:
                        client, path = make_client(cfg, vb)
                        client.connect()
                        for auth in client(GetAuthorizationsRequest()):
                            try:
                                client(ResetAuthorizationRequest(
                                    id=auth.id, device_id=auth.device_id,
                                    device_model=auth.device_model))
                            except Exception:
                                pass
                        close_client(client, path)
                        toast("account removed + logged out everywhere", WARN)
                    except Exception:
                        toast("account removed (remote logout failed)", WARN)
                else:
                    toast("account removed", WARN)
            else:
                line(DIM + "  cancelled" + RST)

def devices_screen(cfg, acc=None):
    require_telethon()
    vk = ensure_unlock()
    if acc is None:
        acc, _ = pick_account(cfg, "  devices of which account?")
    client, path = make_client(cfg, load_session_bytes(vk, acc["id"]))
    try:
        client.connect()
        auths = list(client(GetAuthorizationsRequest()))
        clear()
        header(cfg)
        line(C(BRAND) + BOLD + f"  📱  D E V I C E S · {acc['phone']}" + RST
             + DIM + f"   {len(auths)} session(s)" + RST)
        line()
        skipped_ours = False
        for i, a in enumerate(auths, 1):
            model = sanitize(getattr(a, "title", None) or a.device_model or "?", 40)
            loc = " · ".join(x for x in (a.country, a.city) if getattr(a, x, None))
            ours = (not skipped_ours and a.device_model == DEVICE_MODEL)
            if ours:
                skipped_ours = True
            line(f"   {i}  {DIM}{'●' if ours else '○'}{RST} {BOLD}{model}{RST}"
                 f"   {DIM}{a.ip or ''} {loc}{RST}"
                 f"{'   ' + WARN + '📌 this app' + RST if ours else ''}")
        line()
        while True:
            try:
                ch = input(DIM + "  [1-N view · T terminate · X all-but-this · q]: " + RST).strip().lower()
            except EOFError:
                return
            if ch == "q" or ch == "":
                return
            if ch == "x":
                if not type_yes("  terminate ALL sessions (except this app)?"):
                    continue
                killed = 0
                for a in auths:
                    if a.device_model == DEVICE_MODEL and killed == 0:
                        continue
                    try:
                        client(ResetAuthorizationRequest(id=a.id,
                                                         device_id=a.device_id,
                                                         device_model=a.device_model))
                        killed += 1
                    except Exception as e:
                        line(DANGER + f"  ✗ {sanitize(str(e), 80)}" + RST)
                audit("terminate-all", f"acc={acc['id']} n={killed}")
                toast(f"{killed} session(s) terminated", WARN)
                line(DIM + "  (press enter)" + RST); input()
                return
            if ch == "t":
                i2 = ask("  terminate which (number)?")
                if not (i2.isdigit() and 1 <= int(i2) <= len(auths)):
                    continue
                a = auths[int(i2) - 1]
                if a.device_model == DEVICE_MODEL and not skipped_ours:
                    line(WARN + "  ⚠ that is this app's own session" + RST)
                    if not confirm("  terminate it?", default=False):
                        continue
                if not type_yes(f"  terminate “{sanitize(a.device_model or '', 40)}”?"):
                    continue
                try:
                    client(ResetAuthorizationRequest(id=a.id,
                                                     device_id=a.device_id,
                                                     device_model=a.device_model))
                    audit("terminate", f"acc={acc['id']} dev={a.id}")
                    toast("terminated")
                except Exception as e:
                    line(DANGER + f"  ✗ {sanitize(str(e), 80)}" + RST)
    finally:
        close_client(client, path)

def security_screen(cfg):
    while True:
        clear()
        header(cfg)
        line(C(BRAND) + BOLD + "  🛡  S E C U R I T Y" + RST)
        line()
        line("   1   " + icon("security") + "  Audit log")
        nq = 0
        try:
            nq = len([f for f in os.listdir(os.path.join(POLYGRAM_HOME, "quarantine"))
                      if f.endswith(".rejected")])
        except OSError:
            pass
        line("   2   " + icon("block") + "  Quarantine          "
             + (DANGER + f"{nq} file(s)" + RST if nq else DIM + "0 files" + RST))
        line("   3   ⟳  Rotate sessions     kill old exports")
        line("   4   🚩 Report a code       kill unwanted code")
        line("   5   🗑  Erase vault         forgot passphrase?")
        line()
        try:
            ch = input(DIM + "  [1-5 · q back]: " + RST).strip()
        except EOFError:
            return
        if ch == "q" or ch == "":
            return
        if ch == "1":
            try:
                lines = open(os.path.join(POLYGRAM_HOME, "audit.log"),
                             encoding="utf-8").read().splitlines()[-25:]
            except OSError:
                lines = []
            panel("AUDIT LOG (last 25)", [DIM + l + RST for l in (lines or ["(empty)"])])
            pause()
        elif ch == "2":
            q = os.path.join(POLYGRAM_HOME, "quarantine")
            files = sorted([f for f in os.listdir(q) if f.endswith(".rejected")]) if os.path.isdir(q) else []
            panel("QUARANTINE", [f"  {f}" for f in files] or ["  (empty — good)"])
            pause()
        elif ch == "3":
            rotate_account(cfg)
            pause()
        elif ch == "4":
            report_code(cfg)
            pause()
        elif ch == "5":
            wipe_vault()
            pause()

def settings_screen(cfg):
    while True:
        clear()
        header(cfg)
        line(C(BRAND) + BOLD + "  ⚙  S E T T I N G S" + RST)
        line()
        masked_hash = (cfg.get("api_hash", "")[:4] + "…" + cfg.get("api_hash", "")[-4:])
        line(f"  🔑 api credentials   {C(BRAND) + BOLD}{cfg['api_id']}{RST} · {DIM}{masked_hash}{RST}")
        line(f"  🔔 notifications      {C(OK) if cfg.get('notify') else C(WARN)}"
             f"{'on' if cfg.get('notify') else 'off'}{RST}   {DIM}(Termux:API){RST}")
        line(f"  📩 SMS auto-OTP       {C(OK) if cfg.get('sms_watch') else C(WARN)}"
             f"{'on' if cfg.get('sms_watch') else 'off'}{RST}")
        line(f"  ⏱ vault auto-lock    {C(BRAND)}{UNLOCK_TTL // 60} min{RST} idle")
        line(f"  📁 vault path         {C(BRAND)}{POLYGRAM_HOME}{RST}")
        tv = __import__("telethon").__version__ if TELETHON_OK else "MISSING"
        line(f"  ℹ  about              Polygram v{APP_VERSION} · Telethon {tv}")
        line()
        line("   1  change vault passphrase")
        line("   2  change api credentials")
        line("   3  toggle notifications")
        line("   4  toggle SMS auto-OTP")
        line("   5  about / help")
        line()
        try:
            ch = input(DIM + "  [1-5 · q back]: " + RST).strip()
        except EOFError:
            return
        if ch == "q" or ch == "":
            return
        if ch == "1":
            old = secret_input("  current passphrase: ")
            for _ in range(3):
                np1 = secret_input("  new passphrase (min 12 chars): ")
                if len(np1) < 12:
                    line(WARN + "  ⚠ too short" + RST)
                    continue
                m, lab = strength(np1)
                line(f"  strength {C(WARN if lab == 'weak' else OK)}{m} {lab}{RST}")
                np2 = secret_input("  confirm new passphrase: ")
                if np1 != np2:
                    line(DANGER + "  ✗ mismatch" + RST)
                    continue
                rekey_vault(old, np1)
                toast("passphrase changed — all sessions re-encrypted", OK)
                break
        elif ch == "2":
            line("   [?] opens my.telegram.org")
            loop = True
            while loop:
                aid = ask(C(BRAND) + "  🔢 api_id (number, or '?')" + RST)
                if aid == "?":
                    open_url(MY_TELEGRAM_URL)
                    continue
                if not aid.isdigit() or not (4 <= len(aid) <= 8):
                    line(DANGER + "  ✗ api_id must be 4–8 digits" + RST)
                    continue
                ah = ask(C(BRAND) + "  🔢 api_hash (32 hex chars, or '?')" + RST)
                if ah == "?":
                    open_url(MY_TELEGRAM_URL)
                    continue
                if not re.fullmatch(r"[0-9a-fA-F]{32}", ah):
                    line(DANGER + "  ✗ api_hash must be 32 hex chars" + RST)
                    continue
                cfg["api_id"] = int(aid)
                cfg["api_hash"] = ah
                save_cfg(cfg)
                audit("config", "api credentials changed")
                toast("api credentials saved", OK)
                loop = False
        elif ch == "3":
            cfg["notify"] = not cfg.get("notify")
            save_cfg(cfg)
            toast(f"notifications { 'on' if cfg['notify'] else 'off' }")
        elif ch == "4":
            cfg["sms_watch"] = not cfg.get("sms_watch")
            save_cfg(cfg)
            line(DIM + "  (needs Termux:API + SMS permission)" + RST)
            toast(f"SMS auto-OTP { 'on' if cfg['sms_watch'] else 'off' }")
        elif ch == "5":
            panel("ABOUT", [
                f"  {BANNER_ART[2].strip()}",
                "",
                f"  Polygram v{APP_VERSION} — {TAGLINE}",
                "  Every account. One terminal.",
                f"  vault: {POLYGRAM_HOME}",
            ])
            pause()

def vault_setup():
    """Ask for + create the vault passphrase (first run, or after erase)."""
    panel(C(SECRET) + BOLD + "🔐  VAULT MASTER PASSPHRASE" + RST, [
        "  One passphrase protects ALL stored sessions.",
        "  (min 12 chars — this is your front door)",
        "",
        WARN + "  💡 save it in your password manager — with the" + RST,
        WARN + "     export key it's what keeps your accounts safe" + RST,
    ])
    for _ in range(5):
        p1 = secret_input(C(SECRET) + "  🔐 set passphrase: " + RST)
        if len(p1) < 12:
            line(DANGER + "  ✗ min 12 characters" + RST)
            continue
        m, lab = strength(p1)
        line(f"  strength  {C(WARN if lab == 'weak' else OK)}{m} {lab}{RST}")
        p2 = secret_input(C(SECRET) + "  🔐 confirm passphrase: " + RST)
        if p1 != p2:
            line(DANGER + "  ✗ mismatch — start again" + RST)
            continue
        break
    else:
        raise LoginError("setup cancelled")
    vault_init(p1)

def wipe_vault(assume_yes=False):
    """Erase EVERYTHING the vault holds (forgot-passphrase recovery).
    Keeps api credentials (config.json) and .pgs export backups."""
    require_crypto()
    line()
    panel(C(DANGER) + BOLD + "🗑  E R A S E   V A U L T" + RST, [
        "  Deletes EVERYTHING this app stored:",
        "   • all sessions (all accounts)  • vault key",
        "   • accounts list  • quarantine  • audit log",
        "",
        "  Keeps: api credentials + .pgs export backups.",
        "  Cannot be undone. Telegram is NOT touched.",
    ])
    if not sys.stdin.isatty():
        if not assume_yes:
            raise LoginError("non-interactive: re-run with --yes to confirm")
    else:
        v = ask(C(DANGER) + "  type ERASE to confirm" + RST).upper()
        if v != "ERASE":
            line(DIM + "  cancelled — nothing deleted" + RST)
            return
    home = POLYGRAM_HOME
    for name in ("vault.meta", "vault.sentinel", "accounts.json",
                 "accounts.json.bak", "audit.log"):
        try:
            os.unlink(os.path.join(home, name))
        except OSError:
            pass
    for d in ("sessions", "quarantine"):
        dp = os.path.join(home, d)
        if os.path.isdir(dp):
            for f in os.listdir(dp):
                try:
                    os.unlink(os.path.join(dp, f))
                except OSError:
                    pass
    UNLOCK["key"] = None
    audit("erase", "vault wiped")
    success("🗑  V A U L T   E R A S E D", [
        "  all sessions and key material are gone.",
        "",
        "  💡 next: run `polygram` — it will ask for a NEW",
        "     passphrase and you're set (api creds kept).",
    ])

def first_run():
    require_crypto()
    clear()
    banner()
    line()
    panel(icon("star4") + "  W E L C O M E   T O   P O L Y G R A M", [
        "",
        "  Your vault for every Telegram account:",
        "   · add accounts      (number → OTP → 2FA)",
        "   · relay login codes to any new device",
        "   · export/import the whole vault (armored)",
        "",
        "  🚀 setup takes ~2 minutes:",
        "    🔑 api_id/api_hash → 🔐 passphrase → ready",
    ])
    line()
    panel(C(BRAND) + BOLD + "🔑  ① API CREDENTIALS" + RST, [
        "  talks to Telegram with YOUR api_id + api_hash",
        "  (free, takes 2 minutes at my.telegram.org).",
        "",
        "  (press '?' → I'll open the website for you)",
    ])
    loop = True
    while loop:
        aid = ask(C(BRAND) + "  🔢 api_id (number, or '?')" + RST)
        if aid == "?":
            open_url(MY_TELEGRAM_URL)
            panel(C(BRAND) + BOLD + "🌐  GET YOUR CREDENTIALS" + RST, [
                "  Opening browser → my.telegram.org",
                "",
                "   1. log in with your Telegram number",
                "   2. tap   API development tools",
                "   3. fill the short form → Create application",
                "   4. copy   api_id   (number)",
                "              api_hash   (32 characters)",
                "",
                "  Keep that page open while you paste them below.",
            ])
            continue
        if not aid.isdigit() or not (4 <= len(aid) <= 8):
            line(DANGER + "  ✗ api_id must be 4–8 digits" + RST)
            continue
        ah = ask(C(BRAND) + "  🔢 api_hash (32 hex chars, or '?')" + RST)
        if ah == "?":
            open_url(MY_TELEGRAM_URL)
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{32}", ah):
            line(DANGER + "  ✗ api_hash must be 32 hex chars" + RST)
            continue
        loop = False
    line()
    vault_setup()
    notify_on = False
    sms_on = False
    if shutil.which("termux-notify") or shutil.which("termux-sms-list"):
        line()
        panel("③ OPTIONAL EXTRAS (Termux:API)", [
            "  ● notifications on success/alerts",
            "  ● auto-read OTP from SMS inbox (SMS permission)",
        ])
        notify_on = confirm("  enable notifications? ", default=True)
        sms_on = confirm("  enable SMS auto-OTP? ", default=False)
    cfg = {"api_id": int(aid), "api_hash": ah,
           "notify": bool(notify_on), "sms_watch": bool(sms_on)}
    ensure_dirs()
    save_cfg(cfg)
    audit("setup", "first run complete")
    success("✦  R E A D Y  ✦", [
        "  💾 vault created, credentials saved.",
        "",
        f"  {C(BRAND)}➕  add your first account{RST}      (menu 1)",
        f"  {C(BRAND)}📥  or import a vault file{RST}     (menu 5 — works with zero accounts)",
        "",
        DIM + "  💡 setup is done — nothing else to configure" + RST,
    ])

# -------------------------------------------------------------------- main
def main_tui(cfg):
    clear()
    banner()
    while True:
        try:
            line()
            header(cfg)
            accs = load_accounts()
            items = [
                ("add", "Add Account", "number → OTP → 2FA"),
                ("login", "Login", "relay code to a new device"),
                ("accounts", "Accounts", f"{len(accs)} stored"),
                ("export", "Export Vault", "all accounts → .pgs + key"),
                ("import", "Import", ".pgs + key → vault"),
                ("devices", "Devices & Sessions", "who is logged in where"),
                ("security", "Security", "audit · quarantine · rotate"),
                ("settings", "Settings", "passphrase · api · extras"),
                ("exit", "Exit", "goodbye 👋"),
            ]
            for i, (key, label, sub) in enumerate(items, 1):
                line(f"   {C(BRAND) + BOLD}{i}{RST}  {icon(key)}  "
                     f"{BOLD}{label}{RST}   {DIM}{sub}{RST}")
            line()
            if not accs:
                line(WARN + "   💡 no accounts — press 1 to add, or 5 to import" + RST)
                line()
            footer("⏎ open  1-9 pick  ? about  q quit")
            ch = input(WHITE + BOLD + "  ▸ " + RST).strip().lower()
        except (KeyboardInterrupt, EOFError):
            break
        if ch in ("q", "9", "exit"):
            if sys.stdin.isatty() and not confirm("  leave Polygram? "):
                continue
            break
        if ch == "?":
            panel("ABOUT", [f"  Polygram v{APP_VERSION} — {TAGLINE}",
                            "  Every account. One terminal."])
            pause()
            continue
        try:
            if ch in ("1", "add"):
                add_account(cfg); pause()
            elif ch in ("2", "login"):
                login_relay(cfg); pause()
            elif ch in ("3", "accounts"):
                accounts_screen(cfg)
            elif ch in ("4", "export"):
                do_export(cfg); pause()
            elif ch in ("5", "import"):
                r = None
                while r is None:
                    line()
                    line(C(BRAND) + BOLD + "  📥  IMPORT" + RST)
                    line(WARN + "  💡 the file and its key must be in two" + RST)
                    line(WARN + "     different places" + RST)
                    line("  📁 .pgs files found:")
                    cands = []
                    dirs = [os.path.join(POLYGRAM_HOME, "exports"),
                            os.path.expanduser("~/storage/shared/Polygram")]
                    dl = android_download_dir()
                    if dl:
                        dirs.append(dl)
                    for d in dirs:
                        if os.path.isdir(d):
                            cands += [os.path.join(d, f) for f in
                                      sorted(os.listdir(d)) if f.endswith(".pgs")]
                    for i, c in enumerate(cands, 1):
                        line(f"   {i}  {c}")
                    k = ask("  pick a file (or path / [C]lipboard / q)", default="")
                    if k.lower() in ("q", ""):
                        return
                    if k.lower() == "c":
                        r = import_bundle(cfg, clipboard_key=True)
                    elif k.isdigit() and 1 <= int(k) <= len(cands):
                        r = import_bundle(cfg, file=cands[int(k) - 1], verify=True)
                    else:
                        r = import_bundle(cfg, file=k, verify=True)
                if r == 0:
                    pause()
            elif ch in ("6", "devices"):
                devices_screen(cfg); pause()
            elif ch in ("7", "security"):
                security_screen(cfg)
            elif ch in ("8", "settings"):
                settings_screen(cfg)
        except LoginError as e:
            fail("✗ " + sanitize(str(e), 80), [])
            pause()
        except Exception as e:
            fail("✗ " + sanitize(str(e), 80), [DIM + "  (see above for details)" + RST])
            pause()
    line()
    line(DIM + f"  ▛ POLYGRAM ▞   vault safe {icon('lock')}" + RST)
    if ANIM:
        s = "  Goodbye 👋"
        for c in s:
            OUT.write(c); OUT.flush(); time.sleep(0.04)
        OUT.write("\n")
    else:
        line("  Goodbye 👋")

def selftest():
    """Offline self-test: deps, vault round-trip, export/import, tamper."""
    import tempfile as _tf, shutil as _sh
    print(f"Polygram v{APP_VERSION} self-test")
    ok = lambda m: print(f"  ✓ {m}")
    bad = lambda m: (print(f"  ✗ {m}"), sys.exit(1))
    if not CRYPTO_OK:
        bad(f"cryptography missing ({CRYPTO_ERR}) — pkg install python-cryptography")
    ok("cryptography loaded")
    if TELETHON_OK:
        v = __import__("telethon").__version__
        ok(f"telethon loaded ({v})")
        for name in ("GetPasswordRequest", "GetAuthorizationsRequest",
                     "ResetAuthorizationRequest", "InvalidateSignInCodesRequest"):
            if name == "InvalidateSignInCodesRequest" and InvalidateSignInCodesRequest is None:
                print("  ⚠ sign-in-code API missing — upgrade telethon")
    else:
        print("  ⚠ telethon missing — pip install telethon")
    tmp = _tf.mkdtemp(prefix="pg-selftest-")
    tmp2 = None
    global POLYGRAM_HOME
    old_home = POLYGRAM_HOME
    try:
        POLYGRAM_HOME = tmp
        ensure_dirs()
        save_cfg({"api_id": 1, "api_hash": "0" * 32, "notify": False, "sms_watch": False})
        vault_init("selftest-passphrase-123")
        ok("vault created (passphrase KDF + sentinel)")
        if not verify_sentinel(vault_key("selftest-passphrase-123")):
            bad("sentinel verify failed")
        if verify_sentinel(vault_key("wrong-passphrase-123456")):
            bad("wrong passphrase verified?!")
        ok("sentinel rejects wrong passphrase")
        UNLOCK.update(key=vault_key("selftest-passphrase-123"), at=time.time())
        vk = UNLOCK["key"]
        fake_session = secrets.token_bytes(256)
        store_session(vk, "acct-0001", fake_session)
        accs = load_accounts()
        accs.append({"id": "acct-0001", "label": "Self Test", "phone": "+15550000001",
                     "username": "selftest", "user_id": 111, "dc_id": 2,
                     "has_2fa": True, "star": False,
                     "added": "t", "last_used": "t"})
        save_accounts(accs)
        ok("session stored encrypted (AES-256-GCM)")
        if load_session_bytes(vault_key("selftest-passphrase-123"), "acct-0001") != fake_session:
            bad("session round-trip mismatch")
        ok("session decrypt round-trip matches")
        token = gen_key_token()
        bundle, manifest, pt = make_bundle(load_accounts(), vk, token)
        ok(f"bundle built ({len(bundle)} B, fp {manifest['fingerprint']})")
        # import into a FRESH vault
        tmp2 = _tf.mkdtemp(prefix="pg-selftest2-")
        POLYGRAM_HOME = tmp2
        ensure_dirs()
        save_cfg({"api_id": 1, "api_hash": "0" * 32, "notify": False, "sms_watch": False})
        vault_init("other-vault-passphrase-1")
        UNLOCK.update(key=vault_key("other-vault-passphrase-1"), at=time.time())
        with open(os.path.join(tmp, "test.pgs"), "wb") as f:
            f.write(bundle)
        rc = import_bundle(load_cfg(), file=os.path.join(tmp, "test.pgs"),
                           key=token, verify=False)
        if rc != 0:
            bad(f"import rc={rc}")
        accs2 = load_accounts()
        if len(accs2) != 1 or load_session_bytes(UNLOCK["key"], "acct-0001") != fake_session:
            bad("imported vault mismatch")
        ok("whole-vault import into fresh vault matches")
        # tamper: flip one byte → must hard-stop + quarantine
        POLYGRAM_HOME = tmp2
        data = bytearray(bundle)
        data[-1] ^= 1
        with open(os.path.join(tmp, "tampered.pgs"), "wb") as f:
            f.write(bytes(data))
        rc = import_bundle(load_cfg(), file=os.path.join(tmp, "tampered.pgs"),
                           key=token, verify=False)
        if rc != 2:
            bad(f"tampered import rc={rc} (expected 2)")
        q = [x for x in os.listdir(os.path.join(tmp2, "quarantine")) if x.endswith(".rejected")]
        if not q:
            bad("no quarantined file for tampered bundle")
        ok("tampered byte → hard-stop + quarantine")
        # wrong key → must fail
        bad2 = os.path.join(tmp, "wrong.pgs")
        with open(bad2, "wb") as f:
            f.write(bundle)
        rc = import_bundle(load_cfg(), file=bad2,
                           key=gen_key_token(), verify=False)
        if rc != 2:
            bad(f"wrong-key import rc={rc} (expected 2)")
        ok("wrong key → rejected")
        print()
        print("  ✦ SELF-TEST PASSED — install is healthy ✦")
        return 0
    finally:
        POLYGRAM_HOME = old_home
        _sh.rmtree(tmp, ignore_errors=True)
        if tmp2:
            _sh.rmtree(tmp2, ignore_errors=True)

def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(
        prog="polygram",
        description=f"{APP_NAME} v{APP_VERSION} — {TAGLINE}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""commands:
  (none)     interactive app
  add        add an account (number → OTP → 2FA)
  login      relay a login code to a new device
  export     export the whole vault (one .pgs + one key)
  import     import a .pgs (gate → decrypt → read → confirm)
  accounts   list / rename / star / delete
  devices    list & terminate sessions of an account
  security   audit log · quarantine · rotate · report code
  settings   passphrase · api creds · extras
  rotate     re-login an account (kills old exports)
  report     invalidate a login code you did NOT request
  erase      wipe the whole vault (forgot passphrase) — starts over
  --selftest verify install + vault round-trip
""")
    p.add_argument("command", nargs="?", default=None)
    p.add_argument("--phone", help="add: phone number (+xx…)")
    p.add_argument("--code", help="add: login code (non-interactive)")
    p.add_argument("--2fa", dest="two_fa", help="add: 2FA password (non-interactive)")
    p.add_argument("--label", help="add: account label")
    p.add_argument("--account", help="account id / index / phone")
    p.add_argument("--out", help="export: output path")
    p.add_argument("--file", help="import: bundle path")
    p.add_argument("--clipboard-key", action="store_true", help="import: key from clipboard")
    p.add_argument("--verify", action="store_true", help="import: verify sessions online")
    p.add_argument("--selftest", action="store_true", help="verify install + vault round-trip")
    p.add_argument("--passphrase", help="vault passphrase (non-interactive)")
    p.add_argument("--no-anim", action="store_true")
    p.add_argument("--ascii", action="store_true")
    p.add_argument("--version", action="store_true")
    p.add_argument("--yes", action="store_true",
                   help="skip confirmations (non-interactive, e.g. erase)")
    args = p.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} v{APP_VERSION} — {TAGLINE}")
        return 0
    if args.selftest:
        return selftest()
    global NONTTY_PASS
    NONTTY_PASS = args.passphrase

    set_ui(anim=not args.no_anim, ascii_mode=args.ascii)
    ensure_dirs()
    cfg = load_cfg()
    if cfg is None:
        if not sys.stdin.isatty():
            print("  ✗ no config yet — run `polygram` first (asks for"
                  "    api_id/api_hash + vault passphrase)")
            return 2
        first_run()
        cfg = load_cfg()
    cmd = args.command
    if cmd is None:
        if not sys.stdin.isatty():
            print("  ✗ no terminal — use a command (see --help)")
            return 2
        main_tui(cfg)
        return 0
    try:
        if cmd == "add":
            add_account(cfg, phone=args.phone, code=args.code,
                        two_fa=args.two_fa, label=args.label)
        elif cmd == "login":
            login_relay(cfg, acc_ref=args.account)
        elif cmd == "export":
            do_export(cfg, out=args.out, only=args.account)
        elif cmd == "import":
            return import_bundle(cfg, file=args.file,
                                 clipboard_key=args.clipboard_key, verify=args.verify)
        elif cmd == "accounts":
            accounts_screen(cfg)
        elif cmd == "devices":
            devices_screen(cfg, find_account(load_accounts(), args.account))
        elif cmd == "security":
            security_screen(cfg)
        elif cmd == "settings":
            settings_screen(cfg)
        elif cmd == "rotate":
            rotate_account(cfg, acc_ref=args.account)
        elif cmd == "report":
            report_code(cfg, acc_ref=args.account)
        elif cmd == "erase":
            wipe_vault(assume_yes=args.yes)
        else:
            p.print_help()
            return 2
        return 0
    except LoginError as e:
        print(DANGER + f"  ✗ {sanitize(str(e), 120)}" + RST)
        return 1
    except KeyboardInterrupt:
        print()
        print(DIM + "  ⎋ interrupted" + RST)
        return 130

if __name__ == "__main__":
    sys.exit(main())
