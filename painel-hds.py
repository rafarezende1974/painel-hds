#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel de HDs — @ginhocarvalho
Aplicativo local de gestão de arquivos em HDs e SSDs externos.
Somente biblioteca padrão Python. Sem internet, sem envio externo.
Banco SQLite em ./data/catalog.db (fora dos drives analisados).
Somente leitura nos drives de origem: nunca apaga, move, formata ou sobrescreve.
"""
import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import socket
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

VERSION = "1.0.1"
PORT = 8765
HOST = "127.0.0.1"
# Caminhos: funcionam como script .py e como .exe congelado (PyInstaller).
# No .exe, os arquivos da interface ficam em sys._MEIPASS (pasta temporária)
# e o catálogo SQLite fica ao lado do .exe, em data\ — nunca na pasta temporária.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    STATIC_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "static")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    STATIC_DIR = os.path.join(APP_DIR, "static")
DATA_DIR = os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "catalog.db")
PID_FILE = os.path.join(DATA_DIR, ".painel-hds.pid")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------- util
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def now_local():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def fmt_bytes(n):
    if n is None:
        return "—"
    try:
        n = int(n)
    except Exception:
        return "—"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{n} B"
            return f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024.0
    return f"{n:.1f} TB"

def safe_rel(path):
    """Normaliza caminho relativo e bloqueia fuga com .."""
    p = (path or "").replace("\\", "/").strip("/")
    parts = [x for x in p.split("/") if x not in ("", ".", "..")]
    return "/".join(parts)

def norm_name(s):
    return unicodedata.normalize("NFKC", (s or "").strip().lower())

# ---------------------------------------------------------------- DB
def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS drives(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      persistent_id TEXT UNIQUE,
      device TEXT, mountpoint TEXT, volume_label TEXT,
      name_catalog TEXT, physical_label TEXT, purpose TEXT, location TEXT,
      fstype TEXT, total_bytes INTEGER, used_bytes INTEGER, free_bytes INTEGER,
      disk_id TEXT, media_type TEXT DEFAULT 'Tipo não identificado',
      source_type TEXT DEFAULT 'volume',
      is_ambiguous INTEGER DEFAULT 0, ambiguity_reason TEXT,
      last_seen TEXT, created_at TEXT, updated_at TEXT, last_scan_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS scans(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      drive_id INTEGER NOT NULL REFERENCES drives(id),
      started_at TEXT, finished_at TEXT,
      status TEXT DEFAULT 'running',
      files_count INTEGER DEFAULT 0, dirs_count INTEGER DEFAULT 0,
      total_files_bytes INTEGER DEFAULT 0,
      errors_json TEXT DEFAULT '[]', warnings_json TEXT DEFAULT '[]',
      incomplete INTEGER DEFAULT 0, note TEXT
    );
    CREATE TABLE IF NOT EXISTS entries(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      drive_id INTEGER NOT NULL, scan_id INTEGER NOT NULL,
      rel_path TEXT NOT NULL, parent_path TEXT NOT NULL DEFAULT '',
      name TEXT NOT NULL, is_dir INTEGER DEFAULT 0,
      size INTEGER DEFAULT 0, mtime TEXT, inode INTEGER, nlink INTEGER DEFAULT 1,
      is_symlink INTEGER DEFAULT 0, is_hardlink_copy INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_entries_drive_scan ON entries(drive_id, scan_id);
    CREATE INDEX IF NOT EXISTS idx_entries_name_size ON entries(name, size);
    CREATE INDEX IF NOT EXISTS idx_entries_size ON entries(size);
    CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(drive_id, scan_id, parent_path);
    CREATE TABLE IF NOT EXISTS file_hashes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      drive_id INTEGER NOT NULL, rel_path TEXT NOT NULL, size INTEGER,
      sha256 TEXT, mtime_at_hash TEXT, verified_at TEXT, status TEXT,
      note TEXT,
      UNIQUE(drive_id, rel_path)
    );
    CREATE TABLE IF NOT EXISTS verify_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'idle',
      total_files INTEGER DEFAULT 0, hashed_files INTEGER DEFAULT 0,
      total_bytes INTEGER DEFAULT 0, hashed_bytes INTEGER DEFAULT 0,
      note TEXT
    );
    """)
    c.commit()
    c.close()

# ---------------------------------------------------------------- detecção real do SO
def _parse_size(v):
    try:
        return int(v)
    except Exception:
        return None

def detect_linux():
    """Retorna volumes reais via lsblk + /proc/mounts + disk_usage."""
    out = []
    mounts = {}
    try:
        with open("/proc/mounts", encoding="utf-8", errors="ignore") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3:
                    mounts[p[1]] = {"device": p[0], "fstype": p[2]}
    except Exception:
        pass
    try:
        r = subprocess.run(["lsblk", "-J", "-b", "-o",
                            "NAME,UUID,PARTUUID,FSTYPE,SIZE,TYPE,MOUNTPOINT,ROTA,TRAN,MODEL,SERIAL,PKNAME,LABEL"],
                           capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout or "{}")
        def walk(devs, parent_disk=None):
            for d in devs or []:
                disk = parent_disk or d.get("name")
                # só partições / discos com sistema de arquivos
                if d.get("fstype"):
                    mp = d.get("mountpoint")
                    total = _parse_size(d.get("size"))
                    used = free = None
                    if mp and os.path.isdir(mp):
                        try:
                            u = shutil.disk_usage(mp)
                            total = u.total; used = u.used; free = u.free
                        except Exception:
                            pass
                    tran = (d.get("tran") or "").lower()
                    rota = d.get("rota")
                    # Tipo só quando há informação confiável
                    if tran in ("usb", "sata", "ata", "nvme", "scsi") and rota is not None:
                        media = "HD" if rota else "SSD"
                    else:
                        media = "Tipo não identificado"
                    is_ext = (
                        tran == "usb"
                        or (mp or "").startswith(("/media/", "/mnt/", "/run/media/"))
                        or "usb" in (d.get("model") or "").lower()
                    )
                    uuid = d.get("uuid") or d.get("partuuid")
                    pid = uuid or None
                    ambiguous = 0; reason = ""
                    if not uuid:
                        # identidade de reserva: serial+tamanho+fstype; sem serial fica ambígua
                        serial = d.get("serial") or ""
                        if serial:
                            pid = "reserva:" + hashlib.sha256(
                                f"{serial}|{d.get('size')}|{d.get('fstype')}".encode()).hexdigest()[:32]
                        else:
                            pid = "sem-uuid:" + hashlib.sha256(
                                f"{d.get('name')}|{d.get('size')}|{d.get('fstype')}".encode()).hexdigest()[:32]
                            ambiguous = 1
                            reason = "Sem UUID nem número de série visível — a identidade pode mudar se a letra ou a ordem de conexão mudar. Confira o rótulo físico antes de catalogar."
                    out.append({
                        "device": "/dev/" + str(d.get("name")),
                        "persistent_id": pid, "uuid": uuid,
                        "mountpoint": mp, "fstype": d.get("fstype"),
                        "volume_label": d.get("label") or "",
                        "total_bytes": total, "used_bytes": used, "free_bytes": free,
                        "disk_id": disk, "media_type": media,
                        "is_external": bool(is_ext), "transport": tran,
                        "model": d.get("model") or "", "serial": d.get("serial") or "",
                        "is_ambiguous": ambiguous, "ambiguity_reason": reason,
                        "source_type": "volume",
                    })
                walk(d.get("children"), disk)
        walk(data.get("blockdevices"))
    except Exception as e:
        out.append({"_error": f"lsblk indisponível: {e}"})
    # montagens sem entrada no lsblk (ex.: overlay, tmpfs) — listar só como contexto, não como drive externo
    return out

def detect_windows():
    try:
        ps = (
            "Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem } | "
            "Select-Object DriveLetter, FileSystem, FileSystemLabel, Size, SizeRemaining, UniqueId, DriveType | ConvertTo-Json"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        out = []
        for v in data:
            uid = v.get("UniqueId") or ""
            pid = uid if uid else None
            amb = 0; reason = ""
            if not pid:
                pid = "sem-uuid:" + hashlib.sha256(str(v).encode()).hexdigest()[:32]
                amb = 1; reason = "Sem UniqueId visível — identidade ambígua."
            total = v.get("Size"); free = v.get("SizeRemaining")
            out.append({
                "device": (str(v.get("DriveLetter")) + ":") if v.get("DriveLetter") else "",
                "persistent_id": pid, "uuid": uid,
                "mountpoint": (str(v.get("DriveLetter")) + ":\\") if v.get("DriveLetter") else "",
                "fstype": v.get("FileSystem") or "",
                "volume_label": v.get("FileSystemLabel") or "",
                "total_bytes": total, "used_bytes": (total - free) if total and free is not None else None,
                "free_bytes": free, "disk_id": uid or str(v.get("DriveLetter")),
                "media_type": "Tipo não identificado",
                "is_external": v.get("DriveType") in (2, "Removable"),
                "transport": "", "model": "", "serial": "",
                "is_ambiguous": amb, "ambiguity_reason": reason, "source_type": "volume",
            })
        return out
    except Exception as e:
        return [{"_error": f"PowerShell indisponível: {e}"}]

def detect_macos():
    try:
        r = subprocess.run(["diskutil", "list", "-plist"], capture_output=True, text=True, timeout=15)
        return [{"_note": "macOS: use `diskutil info /Volumes/<nome>` para ver o Volume UUID; a detecção lista os volumes montados em /Volumes.",
                 "_raw_hint": "diskutil list executado", "os": "Darwin"}]
    except Exception as e:
        return [{"_error": f"diskutil indisponível: {e}"}]

def detect_system_drives():
    system = platform.system()
    if system == "Linux":
        vols = detect_linux()
    elif system == "Windows":
        vols = detect_windows()
    elif system == "Darwin":
        vols = detect_macos()
    else:
        vols = [{"_error": f"Sistema não reconhecido: {system}"}]
    return {"os": system, "release": platform.release(), "volumes": vols}

def live_status_for(persistent_id, mountpoint):
    """Conectado agora? Confere se o ponto de montagem existe e responde."""
    if not mountpoint:
        return False
    try:
        return os.path.isdir(mountpoint) and os.access(mountpoint, os.R_OK)
    except Exception:
        return False

# ---------------------------------------------------------------- catálogo
def drive_row_to_dict(r, live_mounts=None):
    d = dict(r)
    mp = d.get("mountpoint")
    connected = live_status_for(d.get("persistent_id"), mp)
    # se o volume foi detectado agora pelo SO, o mountpoint atual vale mais
    if live_mounts and d.get("persistent_id") in live_mounts:
        lm = live_mounts[d["persistent_id"]]
        if lm.get("mountpoint"):
            d["mountpoint_live"] = lm["mountpoint"]
            connected = True
            if lm.get("total_bytes"):
                d["total_bytes_live"] = lm["total_bytes"]
                d["used_bytes_live"] = lm.get("used_bytes")
                d["free_bytes_live"] = lm.get("free_bytes")
    d["connected"] = connected
    # última atualização do inventário
    if d.get("last_scan_id"):
        c = db()
        s = c.execute("SELECT finished_at, status FROM scans WHERE id=?", (d["last_scan_id"],)).fetchone()
        c.close()
        d["last_inventory_at"] = s["finished_at"] if s else None
    else:
        d["last_inventory_at"] = None
    return d

def list_drives():
    try:
        vols = detect_system_drives().get("volumes", [])
        live = {v.get("persistent_id"): v for v in vols if isinstance(v, dict) and v.get("persistent_id")}
    except Exception:
        live = {}
    c = db()
    rows = c.execute("SELECT * FROM drives ORDER BY name_catalog COLLATE NOCASE").fetchall()
    c.close()
    return [drive_row_to_dict(r, live) for r in rows]

def get_drive(drive_id):
    c = db()
    r = c.execute("SELECT * FROM drives WHERE id=?", (drive_id,)).fetchone()
    c.close()
    return r

# ---------------------------------------------------------------- inventário (leitura rápida, só metadados)
scan_state = {}   # scan_id -> dict progress
scan_cancel = {}  # scan_id -> bool
scan_lock = threading.Lock()

def start_scan(drive_id):
    dr = get_drive(drive_id)
    if not dr:
        return None, "Drive não encontrado no catálogo."
    mp = dr["mountpoint"]
    if not mp or not os.path.isdir(mp):
        return None, "Drive desconectado. Reconecte o volume correto e tente de novo — o último inventário válido continua disponível para pesquisa."
    c = db()
    cur = c.execute(
        "INSERT INTO scans(drive_id, started_at, status) VALUES(?,?, 'running')",
        (drive_id, now_iso()))
    scan_id = cur.lastrowid
    c.commit(); c.close()
    scan_cancel[scan_id] = False
    scan_state[scan_id] = {"scan_id": scan_id, "drive_id": drive_id, "status": "running",
                           "files": 0, "dirs": 0, "bytes": 0, "current_path": "", "errors": [], "started_at": now_iso()}
    t = threading.Thread(target=_scan_worker, args=(drive_id, scan_id, mp), daemon=True)
    t.start()
    return scan_id, None

def _scan_worker(drive_id, scan_id, mountpoint):
    errors = []
    warnings = []
    seen_inodes = {}
    files = dirs = total_b = seen = 0
    batch = []
    # O check de "não atravessar para outro volume" usa st_dev, que só é confiável
    # em POSIX (Linux/macOS). No Windows ele não distingue volumes de forma segura
    # e já fez uma leitura inteira voltar vazia num HD real — por isso, no Windows
    # o check fica desligado (as letras de unidade já delimitam os volumes).
    root_dev = None
    if os.name == "posix":
        try:
            root_dev = os.stat(mountpoint).st_dev
        except Exception:
            pass
    c = db()
    try:
        stack = [(mountpoint, "")]
        while stack:
            if scan_cancel.get(scan_id):
                raise InterruptedError("interrompido pelo usuário")
            abs_dir, rel_dir = stack.pop()
            try:
                with os.scandir(abs_dir) as it:
                    entries = list(it)
            except (PermissionError, OSError) as e:
                errors.append(f"Sem acesso a {rel_dir or '/'}: {e}")
                continue
            for e in entries:
                if scan_cancel.get(scan_id):
                    raise InterruptedError("interrompido pelo usuário")
                seen += 1
                try:
                    rel = (rel_dir + "/" + e.name).strip("/") if rel_dir else e.name
                    scan_state[scan_id]["current_path"] = rel
                    st = e.stat(follow_symlinks=False)
                    is_link = e.is_symlink()
                    if is_link:
                        # nunca seguir link simbólico para fora do volume
                        try:
                            target = os.path.realpath(os.path.join(abs_dir, e.name))
                            inside = os.path.commonpath([target, mountpoint]) == mountpoint if os.path.exists(target) else False
                        except Exception:
                            inside = False
                        if not inside:
                            warnings.append(f"Link simbólico ignorado (aponta para fora do volume): {rel}")
                            batch.append((drive_id, scan_id, rel, rel_dir, e.name, 0, 0,
                                          datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                                          st.st_ino, st.st_nlink, 1, 0))
                            files += 1
                            continue
                    is_dir = e.is_dir(follow_symlinks=False)
                    # não atravessar para outro dispositivo (outro volume montado dentro)
                    if root_dev is not None and st.st_dev != root_dev and not is_link:
                        warnings.append(f"Outro volume montado dentro ignorado: {rel}")
                        continue
                    if is_dir:
                        dirs += 1
                        batch.append((drive_id, scan_id, rel, rel_dir, e.name, 1, 0,
                                      datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                                      st.st_ino, st.st_nlink, 1 if is_link else 0, 0))
                        if not is_link:
                            stack.append((os.path.join(abs_dir, e.name), rel))
                    else:
                        # hard link: mesmo inode no mesmo drive não é cópia física independente
                        key = (st.st_dev, st.st_ino)
                        is_hl_copy = 1 if key in seen_inodes else 0
                        if key not in seen_inodes:
                            seen_inodes[key] = rel
                            total_b += st.st_size
                        files += 1
                        batch.append((drive_id, scan_id, rel, rel_dir, e.name, 0, st.st_size,
                                      datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                                      st.st_ino, st.st_nlink, 1 if is_link else 0, is_hl_copy))
                    scan_state[scan_id].update({"files": files, "dirs": dirs, "bytes": total_b,
                                                "errors": errors[-5:]})
                    if len(batch) >= 500:
                        c.executemany(
                            "INSERT INTO entries(drive_id,scan_id,rel_path,parent_path,name,is_dir,size,mtime,inode,nlink,is_symlink,is_hardlink_copy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            batch)
                        c.commit(); batch = []
                except InterruptedError:
                    raise
                except (PermissionError, OSError) as e:
                    errors.append(f"Erro em {rel_dir or '/'}: {e}")
        if batch:
            c.executemany(
                "INSERT INTO entries(drive_id,scan_id,rel_path,parent_path,name,is_dir,size,mtime,inode,nlink,is_symlink,is_hardlink_copy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                batch)
            c.commit()
        # capacidade real do volume
        used_now = 0
        try:
            u = shutil.disk_usage(mountpoint)
            used_now = u.used
            c.execute("UPDATE drives SET total_bytes=?, used_bytes=?, free_bytes=?, last_seen=?, updated_at=? WHERE id=?",
                      (u.total, u.used, u.free, now_iso(), now_iso(), drive_id))
        except Exception:
            pass
        # guarda: 0 arquivos e 0 pastas num VOLUME (não numa pasta comum) que
        # informa mais de 1 GB usado não é um inventário válido — é uma leitura
        # que falhou em silêncio. Não promove esse inventário vazio: o último
        # inventário válido (se houver) continua valendo e o usuário é avisado
        # para reler o drive. (Pasta comum vazia não entra aqui: o uso medido
        # seria o do disco inteiro, não o da pasta.)
        if files == 0 and dirs == 0 and used_now > 1024**3 and os.path.ismount(mountpoint):
            c.execute("DELETE FROM entries WHERE scan_id=?", (scan_id,))
            c.execute("UPDATE scans SET finished_at=?, status='failed', files_count=?, dirs_count=?, total_files_bytes=?, errors_json=?, warnings_json=?, incomplete=1, note=? WHERE id=?",
                      (now_iso(), files, dirs, total_b, json.dumps(errors, ensure_ascii=False),
                       json.dumps(warnings, ensure_ascii=False),
                       "Nenhum arquivo ou pasta foi encontrado, embora o drive informe espaço usado. A leitura não foi registrada — clique em 'Reler este drive' para tentar de novo.", scan_id))
            c.commit()
            scan_state[scan_id].update({"status": "failed", "files": files, "dirs": dirs, "bytes": total_b,
                                        "errors": errors, "warnings": warnings, "finished_at": now_iso()})
        else:
            # conclusão: só agora o novo inventário vira o atual
            c.execute("UPDATE scans SET finished_at=?, status='completed', files_count=?, dirs_count=?, total_files_bytes=?, errors_json=?, warnings_json=?, incomplete=? WHERE id=?",
                      (now_iso(), files, dirs, total_b, json.dumps(errors, ensure_ascii=False),
                       json.dumps(warnings, ensure_ascii=False), 1 if (errors or warnings) else 0, scan_id))
            c.execute("UPDATE drives SET last_scan_id=? WHERE id=?", (scan_id, drive_id))
            c.commit()
            scan_state[scan_id].update({"status": "completed", "files": files, "dirs": dirs, "bytes": total_b,
                                        "errors": errors, "warnings": warnings, "finished_at": now_iso()})
    except InterruptedError:
        c.execute("UPDATE scans SET finished_at=?, status='interrupted', files_count=?, dirs_count=?, total_files_bytes=?, errors_json=?, warnings_json=?, incomplete=1, note='Interrompido pelo usuário — último inventário válido mantido.' WHERE id=?",
                  (now_iso(), files, dirs, total_b, json.dumps(errors, ensure_ascii=False),
                   json.dumps(warnings, ensure_ascii=False), scan_id))
        c.execute("DELETE FROM entries WHERE scan_id=?", (scan_id,))
        c.commit()
        scan_state[scan_id].update({"status": "interrupted", "files": files, "dirs": dirs, "bytes": total_b,
                                    "errors": errors, "finished_at": now_iso()})
    except Exception:
        traceback.print_exc()
        c.execute("UPDATE scans SET finished_at=?, status='failed', files_count=?, dirs_count=?, total_files_bytes=?, errors_json=?, incomplete=1, note=? WHERE id=?",
                  (now_iso(), files, dirs, total_b, json.dumps(errors + [traceback.format_exc()[-500:]], ensure_ascii=False), scan_id))
        c.execute("DELETE FROM entries WHERE scan_id=?", (scan_id,))
        c.commit()
        scan_state[scan_id].update({"status": "failed", "finished_at": now_iso()})
    finally:
        c.close()


# ---------------------------------------------------------------- duplicatas candidatas
def current_scan_ids():
    c = db()
    rows = c.execute("SELECT id, last_scan_id FROM drives WHERE last_scan_id IS NOT NULL").fetchall()
    c.close()
    return [r["last_scan_id"] for r in rows]

def candidate_groups(query=""):
    """Agrupa por (nome normalizado, tamanho). Ordena pela maior economia potencial."""
    scans = current_scan_ids()
    if not scans:
        return [], []
    q = "SELECT e.*, d.name_catalog, d.physical_label, d.mountpoint FROM entries e JOIN drives d ON d.id=e.drive_id WHERE e.scan_id IN (%s) AND e.is_dir=0 AND e.size>0 AND e.is_hardlink_copy=0" % ",".join("?" * len(scans))
    c = db()
    rows = c.execute(q, scans).fetchall()
    c.close()
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        key = (norm_name(r["name"]), r["size"])
        groups[key].append(dict(r))
    out = []
    pair_counts = defaultdict(int)
    for (name, size), items in groups.items():
        if len(items) < 2:
            continue
        if query:
            qq = query.lower()
            if not any(qq in (it["name"] or "").lower() or qq in (it["rel_path"] or "").lower()
                       or qq in (it["name_catalog"] or "").lower() or qq in (it["physical_label"] or "").lower()
                       for it in items):
                continue
        saving = (len(items) - 1) * size
        drives_in = sorted({it["drive_id"] for it in items})
        for i in range(len(drives_in)):
            for j in range(i + 1, len(drives_in)):
                pair_counts[(drives_in[i], drives_in[j])] += 1
        out.append({"name": items[0]["name"], "size": size, "count": len(items),
                    "potential_saving": saving, "copies": items})
    out.sort(key=lambda g: g["potential_saving"], reverse=True)
    # pares de drives com mais correspondências
    c = db()
    names = {r["id"]: (r["name_catalog"] or r["volume_label"] or f"Drive {r['id']}") for r in c.execute("SELECT id, name_catalog, volume_label FROM drives").fetchall()}
    labels = {r["id"]: r["physical_label"] for r in c.execute("SELECT id, physical_label FROM drives").fetchall()}
    c.close()
    pairs = [{"drive_a": a, "drive_b": b, "name_a": names.get(a, str(a)), "name_b": names.get(b, str(b)),
              "label_a": labels.get(a) or "", "label_b": labels.get(b) or "", "matches": n}
             for (a, b), n in sorted(pair_counts.items(), key=lambda kv: kv[1], reverse=True)]
    return out, pairs

# ---------------------------------------------------------------- confirmação por conteúdo (SHA-256)
verify_state = {"status": "idle", "total_files": 0, "hashed_files": 0, "total_bytes": 0,
                "hashed_bytes": 0, "current": "", "run_id": None, "note": ""}
verify_pause = threading.Event()  # set = pausado
verify_stop = threading.Event()
verify_lock = threading.Lock()

def size_groups_for_verify():
    scans = current_scan_ids()
    if not scans:
        return []
    c = db()
    rows = c.execute(
        "SELECT size, COUNT(*) n FROM entries WHERE scan_id IN (%s) AND is_dir=0 AND size>0 AND is_hardlink_copy=0 GROUP BY size HAVING n>=2 ORDER BY size DESC" % ",".join("?" * len(scans)),
        scans).fetchall()
    sizes = [r["size"] for r in rows]
    if not sizes:
        c.close(); return []
    files = c.execute(
        "SELECT e.*, d.mountpoint, d.name_catalog FROM entries e JOIN drives d ON d.id=e.drive_id WHERE e.scan_id IN (%s) AND e.is_dir=0 AND e.size IN (%s) AND e.is_hardlink_copy=0 ORDER BY e.size DESC" % (",".join("?" * len(scans)), ",".join("?" * len(sizes))),
        scans + sizes).fetchall()
    c.close()
    return [dict(r) for r in files]

def _sha256_file(path, stop_check):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if stop_check():
                raise InterruptedError("pausado/interrompido")
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def start_verification():
    with verify_lock:
        if verify_state["status"] == "running":
            return False, "A verificação já está em andamento."
        files = size_groups_for_verify()
        if not files:
            return False, "Nenhum grupo de tamanhos repetidos para verificar. Primeiro faça o inventário de pelo menos dois drives (ou de um drive com arquivos repetidos)."
        total_bytes = sum(f["size"] for f in files)
        c = db()
        cur = c.execute("INSERT INTO verify_runs(started_at, status, total_files, total_bytes) VALUES(?, 'running', ?, ?)",
                        (now_iso(), len(files), total_bytes))
        run_id = cur.lastrowid
        c.commit(); c.close()
        verify_stop.clear(); verify_pause.clear()
        verify_state.update({"status": "running", "total_files": len(files), "hashed_files": 0,
                             "total_bytes": total_bytes, "hashed_bytes": 0, "current": "", "run_id": run_id, "note": ""})
        threading.Thread(target=_verify_worker, args=(files, run_id), daemon=True).start()
        return True, None

def _verify_worker(files, run_id):
    hashed = hashed_bytes = 0
    c = db()
    try:
        for f in files:
            if verify_stop.is_set():
                break
            while verify_pause.is_set():
                verify_state["status"] = "paused"
                time.sleep(0.5)
                if verify_stop.is_set():
                    break
            if verify_stop.is_set():
                break
            verify_state["status"] = "running"
            verify_state["current"] = f"{f['name_catalog']} — {f['rel_path']}"
            mp = f["mountpoint"]
            ap = os.path.join(mp, *f["rel_path"].split("/")) if mp else ""
            if not mp or not os.path.isfile(ap):
                c.execute("INSERT OR REPLACE INTO file_hashes(drive_id, rel_path, size, verified_at, status, note) VALUES(?,?,?,?, 'missing', 'Drive desconectado ou arquivo ausente durante a leitura.')",
                          (f["drive_id"], f["rel_path"], f["size"], now_iso()))
                c.commit()
                verify_state["note"] = "Drive desconectado durante a leitura — verificação pausada com os resultados já concluídos preservados."
                verify_pause.set()
                continue
            # detecta arquivo alterado desde o inventário
            try:
                st = os.stat(ap)
                inv_mtime = f["mtime"]
                cur_mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                if st.st_size != f["size"] or (inv_mtime and cur_mtime[:19] != inv_mtime[:19]):
                    c.execute("INSERT OR REPLACE INTO file_hashes(drive_id, rel_path, size, verified_at, status, note) VALUES(?,?,?,?, 'changed', 'Arquivo alterado desde o inventário — hash descartado.')",
                              (f["drive_id"], f["rel_path"], f["size"], now_iso()))
                    c.commit()
                    continue
            except FileNotFoundError:
                continue
            try:
                digest = _sha256_file(ap, lambda: verify_stop.is_set())
            except InterruptedError:
                break
            except (PermissionError, OSError) as e:
                c.execute("INSERT OR REPLACE INTO file_hashes(drive_id, rel_path, size, verified_at, status, note) VALUES(?,?,?,?, 'error', ?)",
                          (f["drive_id"], f["rel_path"], f["size"], now_iso(), str(e)[:200]))
                c.commit()
                continue
            c.execute("INSERT OR REPLACE INTO file_hashes(drive_id, rel_path, size, sha256, mtime_at_hash, verified_at, status) VALUES(?,?,?,?,?,?, 'ok')",
                      (f["drive_id"], f["rel_path"], f["size"], digest, cur_mtime, now_iso()))
            c.commit()
            hashed += 1; hashed_bytes += f["size"]
            verify_state.update({"hashed_files": hashed, "hashed_bytes": hashed_bytes})
        status = "cancelled" if verify_stop.is_set() else "completed"
        c.execute("UPDATE verify_runs SET finished_at=?, status=?, hashed_files=?, hashed_bytes=? WHERE id=?",
                  (now_iso(), status, hashed, hashed_bytes, run_id))
        c.commit()
        verify_state.update({"status": status, "hashed_files": hashed, "hashed_bytes": hashed_bytes, "current": ""})
    except Exception:
        traceback.print_exc()
        c.execute("UPDATE verify_runs SET finished_at=?, status='failed' WHERE id=?", (now_iso(), run_id))
        c.commit()
        verify_state.update({"status": "failed"})
    finally:
        c.close()

def confirmed_groups():
    c = db()
    rows = c.execute(
        "SELECT h.*, d.name_catalog, d.physical_label FROM file_hashes h JOIN drives d ON d.id=h.drive_id WHERE h.status='ok' AND h.sha256 IS NOT NULL").fetchall()
    c.close()
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        g[(r["sha256"], r["size"])].append(dict(r))
    out = []
    for (sha, size), items in g.items():
        if len(items) >= 2:
            out.append({"sha256": sha, "size": size, "count": len(items),
                        "potential_saving": (len(items) - 1) * size, "copies": items})
    out.sort(key=lambda x: x["potential_saving"], reverse=True)
    return out

# ---------------------------------------------------------------- planejamento (simulação, sem mover nada)
def plan_freeing(source_id, margin_percent=10):
    try:
        margin_percent = max(0, min(50, float(margin_percent)))
    except Exception:
        margin_percent = 10
    c = db()
    src = c.execute("SELECT * FROM drives WHERE id=?", (source_id,)).fetchone()
    if not src:
        c.close(); return None, "Drive para liberar não encontrado."
    others = c.execute("SELECT * FROM drives WHERE id!=? ORDER BY total_bytes DESC", (source_id,)).fetchall()
    if not src["last_scan_id"]:
        c.close(); return None, "Faça primeiro o inventário do drive que você quer liberar."
    # pastas de nível 1 do drive de origem (projetos/pastas juntos, sem dividir)
    tops = c.execute(
        "SELECT parent_path, name, SUM(size) s, COUNT(*) n FROM entries WHERE drive_id=? AND scan_id=? AND is_dir=0 AND is_hardlink_copy=0 GROUP BY parent_path, name",
        (source_id, src["last_scan_id"])).fetchall()
    # agrega por pasta de nível 1
    from collections import defaultdict
    folders = defaultdict(lambda: {"size": 0, "files": 0})
    loose = {"size": 0, "files": 0}
    for r in c.execute("SELECT rel_path, size FROM entries WHERE drive_id=? AND scan_id=? AND is_dir=0 AND is_hardlink_copy=0",
                       (source_id, src["last_scan_id"])).fetchall():
        parts = r["rel_path"].split("/")
        if len(parts) == 1:
            loose["size"] += r["size"]; loose["files"] += 1
        else:
            folders[parts[0]]["size"] += r["size"]; folders[parts[0]]["files"] += 1
    items = [{"name": k, "size": v["size"], "files": v["files"], "kind": "pasta"} for k, v in folders.items()]
    if loose["files"]:
        items.append({"name": "(arquivos soltos na raiz)", "size": loose["size"], "files": loose["files"], "kind": "arquivos soltos"})
    items.sort(key=lambda x: x["size"], reverse=True)
    # destinos: só drives com espaço livre conhecido; prioriza os maiores
    dests = []
    for o in others:
        total = o["total_bytes"] or 0
        free = o["free_bytes"]
        if free is None and total:
            # sem leitura de espaço livre, estima pelo inventário
            used_inv = c.execute("SELECT COALESCE(SUM(size),0) s FROM entries WHERE drive_id=? AND scan_id=? AND is_dir=0",
                                 (o["id"], o["last_scan_id"])).fetchone()["s"] if o["last_scan_id"] else 0
            free = max(0, total - used_inv)
        if total and free is not None:
            margin_bytes = int(total * margin_percent / 100)
            dests.append({"id": o["id"], "name": o["name_catalog"] or o["volume_label"] or f"Drive {o['id']}",
                          "label": o["physical_label"] or "", "total": total, "free": free,
                          "margin_bytes": margin_bytes, "usable": max(0, free - margin_bytes),
                          "allocated": 0, "plan": []})
    c.close()
    dests.sort(key=lambda d: d["total"], reverse=True)
    allocations, unplaced = [], []
    for it in items:
        placed = False
        for d in dests:  # maior drive primeiro; cabe na folga com margem?
            if it["size"] <= d["usable"] - d["allocated"]:
                d["allocated"] += it["size"]
                d["plan"].append(it["name"])
                allocations.append({"item": it["name"], "size": it["size"], "files": it["files"],
                                    "dest_id": d["id"], "dest_name": d["name"]})
                placed = True
                break
        if not placed:
            unplaced.append(it)
    for d in dests:
        used = d["total"] - d["free"]
        d["used_after"] = used + d["allocated"]
        d["free_after"] = d["total"] - d["used_after"]
        d["pct_after"] = round(d["used_after"] / d["total"] * 100, 1) if d["total"] else 0
    return {"source": {"id": src["id"], "name": src["name_catalog"] or src["volume_label"] or f"Drive {src['id']}",
                       "total": src["total_bytes"]},
            "margin_percent": margin_percent,
            "items": items, "allocations": allocations, "unplaced": unplaced,
            "destinations": dests,
            "note": "Simulação apenas — nada foi movido, copiado ou apagado. Candidatos a duplicata NÃO foram descontados como economia confirmada."}, None

# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, ctype="application/json"):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8") if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:
            return {}

    def _static(self, path):
        p = os.path.join(STATIC_DIR, path.lstrip("/")) if path != "/" else os.path.join(STATIC_DIR, "index.html")
        if not os.path.isfile(p):
            p = os.path.join(STATIC_DIR, "index.html")
        ext = os.path.splitext(p)[1].lower()
        ct = {"": "text/html", ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
              ".png": "image/png", ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
        data = open(p, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ct + ("; charset=utf-8" if ct.startswith("text") or ct.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        q = lambda k, d="": (qs.get(k, [d])[0])
        try:
            if path in ("/", "/index.html") or path.startswith("/static/") or path.startswith("/app"):
                self._static("index.html" if path in ("/", "/index.html") else path.replace("/static/", ""))
                return
            if path == "/api/health":
                self._send(200, {"ok": True, "version": VERSION, "os": platform.system(),
                                 "db": DB_PATH, "port": PORT}); return
            if path == "/api/system/drives":
                self._send(200, detect_system_drives()); return
            if path == "/api/drives":
                self._send(200, {"drives": list_drives()}); return
            if path.startswith("/api/drives/") and path.endswith("/scan/status"):
                did = int(path.split("/")[3])
                c = db()
                s = c.execute("SELECT * FROM scans WHERE drive_id=? ORDER BY id DESC LIMIT 1", (did,)).fetchone()
                c.close()
                live = scan_state.get(s["id"]) if s else None
                self._send(200, {"scan": dict(s) if s else None, "live": live}); return
            if path.startswith("/api/drives/") and path.endswith("/browse"):
                did = int(path.split("/")[3]); rel = safe_rel(q("path"))
                dr = get_drive(did)
                if not dr or not dr["last_scan_id"]:
                    self._send(200, {"items": [], "path": rel, "has_inventory": False}); return
                c = db()
                rows = c.execute("SELECT * FROM entries WHERE drive_id=? AND scan_id=? AND parent_path=? ORDER BY is_dir DESC, name COLLATE NOCASE",
                                 (did, dr["last_scan_id"], rel)).fetchall()
                # tamanho de cada subpasta (soma recursiva por prefixo)
                items = []
                for r in rows:
                    d = dict(r)
                    if r["is_dir"]:
                        tot = c.execute("SELECT COALESCE(SUM(size),0) s, COUNT(*) n FROM entries WHERE drive_id=? AND scan_id=? AND is_dir=0 AND is_hardlink_copy=0 AND (rel_path=? OR rel_path LIKE ?)",
                                        (did, dr["last_scan_id"], r["rel_path"], r["rel_path"] + "/%")).fetchone()
                        d["folder_size"] = tot["s"]; d["folder_files"] = tot["n"]
                    items.append(d)
                # treemap: pastas de nível 1 com tamanho
                c.close()
                self._send(200, {"items": items, "path": rel, "has_inventory": True,
                                 "drive": {"id": dr["id"], "name": dr["name_catalog"], "label": dr["physical_label"]}}); return
            if path.startswith("/api/drives/") and path.endswith("/treemap"):
                did = int(path.split("/")[3])
                dr = get_drive(did)
                if not dr or not dr["last_scan_id"]:
                    self._send(200, {"folders": []}); return
                c = db()
                rows = c.execute("SELECT rel_path, size FROM entries WHERE drive_id=? AND scan_id=? AND is_dir=0 AND is_hardlink_copy=0",
                                 (did, dr["last_scan_id"])).fetchall()
                c.close()
                from collections import defaultdict
                agg = defaultdict(int)
                for r in rows:
                    agg[r["rel_path"].split("/")[0]] += r["size"]
                folders = sorted([{"name": k, "size": v} for k, v in agg.items()], key=lambda x: x["size"], reverse=True)[:14]
                self._send(200, {"folders": folders}); return
            if path == "/api/search":
                qq, did = q("q"), q("drive_id")
                scans = current_scan_ids()
                if not qq or not scans:
                    self._send(200, {"results": []}); return
                c = db()
                sql = ("SELECT e.*, d.name_catalog, d.physical_label FROM entries e JOIN drives d ON d.id=e.drive_id "
                       "WHERE e.scan_id IN (%s) AND (e.name LIKE ? OR e.rel_path LIKE ?) " % ",".join("?" * len(scans)))
                params = scans + [f"%{qq}%", f"%{qq}%"]
                if did:
                    sql += " AND e.drive_id=?"; params.append(int(did))
                sql += " ORDER BY e.is_dir DESC, e.size DESC LIMIT 300"
                rows = [dict(r) for r in c.execute(sql, params).fetchall()]
                c.close()
                self._send(200, {"results": rows}); return
            if path == "/api/duplicates":
                groups, pairs = candidate_groups(q("q"))
                self._send(200, {"groups": groups, "pairs": pairs,
                                 "total_potential": sum(g["potential_saving"] for g in groups)}); return
            if path == "/api/verify/status":
                files = size_groups_for_verify()
                total = sum(f["size"] for f in files)
                self._send(200, {"state": verify_state, "queue_files": len(files), "queue_bytes": total,
                                 "confirmed": confirmed_groups()}); return
            if path == "/api/plan":
                res, err = plan_freeing(int(q("source_id")) if q("source_id") else 0, q("margin_percent", "10"))
                if err:
                    self._send(400, {"error": err}); return
                self._send(200, res); return
            if path == "/api/export":
                kind, fmt, did = q("kind", "inventory"), q("format", "csv"), q("drive_id")
                data, filename = export_data(kind, fmt, did)
                if data is None:
                    self._send(400, {"error": "Nada para exportar — faça primeiro um inventário."}); return
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                body = data if isinstance(data, bytes) else data.encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/api/stats":
                drives = list_drives()
                # evita somar 2x capacidades compartilhadas: soma por disco (disk_id)
                seen_disk, total, used, free = {}, 0, 0, 0
                for d in drives:
                    key = d.get("disk_id") or f"drive-{d['id']}"
                    tb = d.get("total_bytes_live") or d.get("total_bytes") or 0
                    if key in seen_disk:
                        continue
                    seen_disk[key] = tb
                    total += tb
                    used += d.get("used_bytes_live") if d.get("used_bytes_live") is not None else (d.get("used_bytes") or 0)
                    free += d.get("free_bytes_live") if d.get("free_bytes_live") is not None else (d.get("free_bytes") or 0)
                c = db()
                fc = c.execute("SELECT COUNT(*) n FROM entries WHERE scan_id IN (SELECT last_scan_id FROM drives WHERE last_scan_id IS NOT NULL) AND is_dir=0").fetchone()["n"]
                c.close()
                groups, _ = candidate_groups()
                self._send(200, {"total_bytes": total, "used_bytes": used, "free_bytes": free,
                                 "drives_count": len(drives),
                                 "connected_count": sum(1 for d in drives if d.get("connected")),
                                 "files_count": fc,
                                 "duplicate_groups": len(groups),
                                 "potential_saving": sum(g["potential_saving"] for g in groups)}); return
            self._send(404, {"error": "Rota não encontrada"})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path); path = u.path
        body = self._json()
        try:
            if path == "/api/drives/detect":
                self._send(200, detect_system_drives()); return
            if path == "/api/drives/register":
                v = body
                pid = (v.get("persistent_id") or "").strip()
                if not pid:
                    self._send(400, {"error": "Sem identidade persistente (UUID). Não dá para catalogar sem ela."}); return
                c = db()
                ex = c.execute("SELECT id FROM drives WHERE persistent_id=?", (pid,)).fetchone()
                if ex:
                    c.close()
                    self._send(409, {"error": "Este volume já está catalogado.", "drive_id": ex["id"]}); return
                cur = c.execute(
                    "INSERT INTO drives(persistent_id, device, mountpoint, volume_label, name_catalog, physical_label, purpose, location, fstype, total_bytes, used_bytes, free_bytes, disk_id, media_type, source_type, is_ambiguous, ambiguity_reason, last_seen, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, v.get("device"), v.get("mountpoint"), v.get("volume_label") or "",
                     v.get("name") or v.get("volume_label") or v.get("mountpoint") or "Drive sem nome",
                     v.get("physical_label") or "", v.get("purpose") or "", v.get("location") or "",
                     v.get("fstype") or "", v.get("total_bytes"), v.get("used_bytes"), v.get("free_bytes"),
                     v.get("disk_id") or pid, v.get("media_type") or "Tipo não identificado",
                     v.get("source_type") or "volume", 1 if v.get("is_ambiguous") else 0,
                     v.get("ambiguity_reason") or "", now_iso(), now_iso(), now_iso()))
                did = cur.lastrowid; c.commit(); c.close()
                self._send(201, {"drive_id": did}); return
            if path == "/api/drives/catalog-folder":
                p = (body.get("path") or "").strip()
                if not p or not os.path.isdir(p):
                    self._send(400, {"error": "Informe uma pasta válida que exista neste computador."}); return
                ap = os.path.realpath(p)
                pid = "pasta:" + hashlib.sha256(ap.encode()).hexdigest()[:32]
                try:
                    _disk_id = "dev:%s" % os.stat(ap).st_dev
                except Exception:
                    _disk_id = pid
                c = db()
                ex = c.execute("SELECT id FROM drives WHERE persistent_id=?", (pid,)).fetchone()
                if ex:
                    c.close(); self._send(409, {"error": "Esta pasta já está catalogada.", "drive_id": ex["id"]}); return
                try:
                    u2 = shutil.disk_usage(ap)
                    total, used, free = u2.total, u2.used, u2.free
                except Exception:
                    total = used = free = None
                cur = c.execute(
                    "INSERT INTO drives(persistent_id, device, mountpoint, volume_label, name_catalog, physical_label, purpose, location, fstype, total_bytes, used_bytes, free_bytes, disk_id, media_type, source_type, is_ambiguous, last_seen, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, "pasta-local", ap, os.path.basename(ap) or ap,
                     body.get("name") or os.path.basename(ap) or ap, body.get("physical_label") or "",
                     body.get("purpose") or "", body.get("location") or "", "", total, used, free,
                     _disk_id, "Tipo não identificado", "pasta-local", 0, now_iso(), now_iso(), now_iso()))
                did = cur.lastrowid; c.commit(); c.close()
                self._send(201, {"drive_id": did, "note": "Pasta catalogada como volume manual — útil para testar e para pastas de rede/montagens manuais. Para um HD/SSD, prefira registrar pelo volume detectado (com UUID)."}); return
            if path.startswith("/api/drives/") and path.endswith("/scan"):
                did = int(path.split("/")[3])
                sid, err = start_scan(did)
                if err:
                    self._send(409, {"error": err}); return
                self._send(202, {"scan_id": sid, "message": "Leitura iniciada. Você pode acompanhar o progresso; quando terminar, o painel avisa e aí sim ejete o drive pelo sistema."}); return
            if path.startswith("/api/drives/") and path.endswith("/scan/cancel"):
                did = int(path.split("/")[3])
                c = db()
                s = c.execute("SELECT id FROM scans WHERE drive_id=? AND status='running' ORDER BY id DESC LIMIT 1", (did,)).fetchone()
                c.close()
                if s:
                    scan_cancel[s["id"]] = True
                self._send(200, {"ok": True, "message": "Interrupção solicitada — o último inventário válido será mantido."}); return
            if path.startswith("/api/drives/") and path.endswith("/open"):
                did = int(path.split("/")[3])
                dr = get_drive(did)
                if not dr or not dr["mountpoint"]:
                    self._send(400, {"error": "Drive sem caminho de montagem."}); return
                mp = dr["mountpoint"]
                if not os.path.isdir(mp):
                    self._send(409, {"error": "Drive desconectado."}); return
                try:
                    sysname = platform.system()
                    if sysname == "Linux":
                        subprocess.Popen(["xdg-open", mp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    elif sysname == "Windows":
                        os.startfile(mp)  # noqa
                    elif sysname == "Darwin":
                        subprocess.Popen(["open", mp])
                    self._send(200, {"ok": True, "path": mp}); return
                except Exception as e:
                    self._send(200, {"ok": True, "path": mp, "note": f"Não consegui abrir o gerenciador automaticamente ({e}). Abra manualmente: {mp}"}); return
            if path == "/api/verify/start":
                ok, err = start_verification()
                if not ok:
                    self._send(409, {"error": err}); return
                self._send(202, {"ok": True, "message": "Verificação por SHA-256 iniciada. Ela lê o conteúdo dos arquivos — pode demorar em drives grandes. Dá para pausar e retomar."}); return
            if path == "/api/verify/pause":
                if verify_state["status"] == "running":
                    verify_pause.set(); verify_state["status"] = "paused"
                    self._send(200, {"ok": True}); return
                self._send(409, {"error": "Nenhuma verificação em andamento para pausar."}); return
            if path == "/api/verify/resume":
                if verify_state["status"] == "paused":
                    verify_pause.clear(); verify_state["status"] = "running"
                    self._send(200, {"ok": True}); return
                self._send(409, {"error": "Nenhuma verificação pausada para retomar."}); return
            if path == "/api/verify/cancel":
                if verify_state["status"] in ("running", "paused"):
                    verify_stop.set(); verify_pause.clear()
                    self._send(200, {"ok": True, "message": "Verificação interrompida — resultados já concluídos foram preservados."}); return
                self._send(409, {"error": "Nenhuma verificação em andamento para interromper."}); return
            self._send(404, {"error": "Rota não encontrada"})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def do_PUT(self):
        u = urlparse(self.path); path = u.path
        body = self._json()
        try:
            if path.startswith("/api/drives/"):
                did = int(path.split("/")[3])
                # edição só do catálogo — nunca renomeia o volume nem toca nos arquivos
                allowed = ("name_catalog", "physical_label", "purpose", "location")
                sets, vals = [], []
                for k in allowed:
                    if k in body:
                        sets.append(f"{k}=?"); vals.append(str(body[k])[:200])
                if not sets:
                    self._send(400, {"error": "Nada para alterar."}); return
                vals.append(now_iso()); vals.append(did)
                c = db()
                c.execute(f"UPDATE drives SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
                c.commit(); c.close()
                self._send(200, {"ok": True}); return
            self._send(404, {"error": "Rota não encontrada"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        u = urlparse(self.path); path = u.path
        try:
            if path.startswith("/api/drives/"):
                did = int(path.split("/")[3])
                c = db()
                c.execute("DELETE FROM entries WHERE drive_id=?", (did,))
                c.execute("DELETE FROM file_hashes WHERE drive_id=?", (did,))
                c.execute("DELETE FROM scans WHERE drive_id=?", (did,))
                c.execute("DELETE FROM drives WHERE id=?", (did,))
                c.commit(); c.close()
                self._send(200, {"ok": True, "message": "Drive removido do catálogo. Nenhum arquivo foi apagado."}); return
            self._send(404, {"error": "Rota não encontrada"})
        except Exception as e:
            self._send(500, {"error": str(e)})

def export_data(kind, fmt, drive_id):
    import csv, io
    c = db()
    rows, headers = [], []
    if kind == "duplicates":
        groups, _ = candidate_groups()
        for g in groups:
            for cp in g["copies"]:
                rows.append({"arquivo": cp["name"], "tamanho_bytes": g["size"],
                             "economia_potencial_grupo_bytes": g["potential_saving"],
                             "drive": cp["name_catalog"], "etiqueta": cp["physical_label"] or "",
                             "caminho": cp["rel_path"]})
        headers = ["arquivo", "tamanho_bytes", "economia_potencial_grupo_bytes", "drive", "etiqueta", "caminho"]
    elif kind == "verified":
        for g in confirmed_groups():
            for cp in g["copies"]:
                rows.append({"arquivo": cp["rel_path"], "tamanho_bytes": g["size"], "sha256": g["sha256"],
                             "drive": cp["name_catalog"], "etiqueta": cp["physical_label"] or ""})
        headers = ["arquivo", "tamanho_bytes", "sha256", "drive", "etiqueta"]
    else:
        scans = current_scan_ids()
        if not scans:
            c.close(); return None, ""
        sql = "SELECT e.rel_path, e.name, e.is_dir, e.size, e.mtime, d.name_catalog, d.physical_label FROM entries e JOIN drives d ON d.id=e.drive_id WHERE e.scan_id IN (%s)" % ",".join("?" * len(scans))
        params = list(scans)
        if drive_id:
            sql += " AND e.drive_id=?"; params.append(int(drive_id))
        for r in c.execute(sql, params).fetchall():
            rows.append({"caminho": r["rel_path"], "nome": r["name"], "pasta": bool(r["is_dir"]),
                         "tamanho_bytes": r["size"], "modificado_em": r["mtime"],
                         "drive": r["name_catalog"], "etiqueta": r["physical_label"] or ""})
        headers = ["caminho", "nome", "pasta", "tamanho_bytes", "modificado_em", "drive", "etiqueta"]
    c.close()
    if not rows:
        return None, ""
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2), f"painel-hds-{kind}.json"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader(); w.writerows(rows)
    return buf.getvalue(), f"painel-hds-{kind}.csv"

# ---------------------------------------------------------------- main / anti-duplicata
def port_in_use():
    s = socket.socket()
    try:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()

def _open_browser_when_ready(url):
    """Só para o .exe: espera o servidor responder e abre o painel no navegador."""
    for _ in range(40):
        try:
            s = socket.socket()
            s.settimeout(0.5)
            if s.connect_ex((HOST, PORT)) == 0:
                s.close()
                webbrowser.open(url)
                return
            s.close()
        except Exception:
            pass
        time.sleep(0.5)

def main():
    init_db()
    url = f"http://{HOST}:{PORT}"
    if port_in_use():
        print(f"Painel de HDs já está rodando em {url} — abrindo essa instância em vez de criar outra.")
        print("Para encerrar: feche a janela do terminal ou use Ctrl+C no processo original.")
        if FROZEN:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return 0
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    if FROZEN:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Painel de HDs v{VERSION} — {url}")
    print(f"Catálogo SQLite: {DB_PATH}")
    print("Somente local. Nada é enviado para a internet. Ctrl+C para encerrar.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando o painel. O catálogo foi salvo e pode ser reaberto depois.")
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
