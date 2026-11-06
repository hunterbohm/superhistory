"""An encrypted copy of the knowledge base, its config and its install id in one file, and the restore of it.

The file, format 1. Numbers are big-endian. A later Superhistory reads this layout by the format number, so a change
to anything below makes a new number and the old one stays readable.

    0    20  the text "SUPERHISTORY BACKUP" and a line feed
    20    4  the format number, 1
    24    4  the PBKDF2 iteration count, N
    28   16  a random salt for the check keys
    44   32  key check: HMAC-SHA256 of bytes 0 to 43
    76   32  tag: HMAC-SHA256 of bytes 0 to 75 followed by the ciphertext
    108   .  the ciphertext

Both HMAC keys are PBKDF2-HMAC-SHA256(passphrase, salt, N), 32 bytes. The ciphertext is what
`openssl enc -aes-256-ctr -pbkdf2 -md sha256 -iter N` makes of the payload, with openssl's own random salt in its first
16 bytes ("Salted__" and the salt). The passphrase is the NFKC form of what was typed, in UTF-8, so a Mac and a Linux
keyboard agree. The payload is a gzip-compressed tar holding, in this order, manifest.json (a Manifest), config.toml and
install-id when there are such files, and store.sqlite, a SQLite file with its journal merged in.

The tag is checked before anything is decrypted, so a wrong passphrase, a damaged file and a cut-off file each stop with
a message and no file written. The key check tells a wrong passphrase from a damaged body.

Without Superhistory: skip 108 bytes, then `openssl enc -d` with the same options, `gunzip`, `tar x`.

No Python package is used: SQLite's backup API, hashlib, hmac, gzip and tar come with Python, and the cipher is the
`openssl` command that a Mac and most Linux systems have. The passphrase reaches openssl through a pipe, never its
command line.
"""
import contextlib
import dataclasses
import getpass
import gzip
import hashlib
import hmac
import io
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zlib
from pathlib import Path

from kb import __version__, ids, store

MAGIC = b"SUPERHISTORY BACKUP\n"
FORMAT = 1
ITERATIONS = 600_000
MAX_ITERATIONS = 20_000_000
HEADER = struct.Struct("!20sII16s")
DIGEST = 32
CHECKED = HEADER.size + DIGEST  # the header and its key check: what the tag covers before the ciphertext
BODY = CHECKED + DIGEST  # where the ciphertext starts
CIPHER = "aes-256-ctr"
MIN_PASSPHRASE = 12
MAX_PASSPHRASE = 1000
SMALL = 1 << 20
CHUNK = 1 << 20
ASIDE = ".before-restore"
MANIFEST, CONFIG, INSTALL, STORE = "manifest.json", "config.toml", "install-id", "store.sqlite"


class BackupError(Exception):
    """A plain message for the user. Nothing was changed when it is raised, unless the message says otherwise."""


@dataclasses.dataclass(frozen=True)
class Manifest:
    format: int
    created: str
    version: str
    items: int
    rows: int
    store_bytes: int
    store_sha256: str

    @classmethod
    def parse(cls, raw):
        try:
            data = json.loads(raw.decode("utf-8"))
            made = cls(**{f.name: data[f.name] for f in dataclasses.fields(cls)})
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BackupError("the backup's list of contents cannot be read") from None
        ints = (made.format, made.items, made.rows, made.store_bytes)
        if not all(type(n) is int for n in ints) or not all(type(s) is str for s in (made.created, made.version,
                                                                                    made.store_sha256)):
            raise BackupError("the backup's list of contents cannot be read")
        return made


@dataclasses.dataclass(frozen=True)
class Contents:
    manifest: Manifest
    config: "bytes | None"
    install_id: "bytes | None"


def passphrase_bytes(text):
    """The bytes that stand for a passphrase, the same on every computer."""
    if "\n" in text or "\r" in text or "\0" in text:
        raise BackupError("a passphrase cannot hold a line break")
    raw = unicodedata.normalize("NFKC", text).encode("utf-8")
    if not raw:
        raise BackupError("the passphrase is empty")
    if len(raw) > MAX_PASSPHRASE:
        raise BackupError(f"the passphrase is longer than {MAX_PASSPHRASE} bytes")
    return raw


def choose_passphrase(text):
    """A passphrase to make a backup with. The length is the only rule: it is what protects the file from a guesser who
    has it."""
    if len(unicodedata.normalize("NFKC", text)) < MIN_PASSPHRASE:
        raise BackupError(f"use a passphrase of at least {MIN_PASSPHRASE} characters: anyone who gets the file can try "
                          f"guesses at it without limit")
    return passphrase_bytes(text)


def read_passphrase(file, making, ask=None, stdin=None):
    """The passphrase from `file` (a path, or "-" for standard input) or, with none, asked for on the terminal, twice
    when `making` a backup. It is never taken from the command line or the environment, where other programs can read
    it."""
    stdin = sys.stdin if stdin is None else stdin
    ask = ask or getpass.getpass
    if file:
        try:
            if file == "-":
                text = stdin.read()
            else:
                with open(file, "rb") as fh:
                    text = fh.read(MAX_PASSPHRASE * 4).decode("utf-8")
        except OSError as exc:
            raise BackupError(f"could not read the passphrase file {file}: {exc.strerror or exc}") from None
        except UnicodeDecodeError:
            raise BackupError(f"the passphrase file {file} is not UTF-8 text") from None
        text = text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith("\n") else text
    elif stdin.isatty():
        text = ask("Passphrase: ")
        if making and ask("Passphrase again: ") != text:
            raise BackupError("the two passphrases differ, so no backup was made")
    else:
        raise BackupError("there is no terminal to ask for the passphrase on. Put it in a file only you can read and "
                          "add --passphrase-file FILE (or - to read it from standard input).")
    return choose_passphrase(text) if making else passphrase_bytes(text)


def openssl_path():
    found = shutil.which("openssl")
    if not found:
        raise BackupError("backups use the `openssl` command, which was not found. A Mac has it. On Linux, install the "
                          "openssl package.")
    try:
        with _Openssl(found, False, b"probe-passphrase", 1000, subprocess.PIPE, subprocess.PIPE) as probe:
            probe.proc.stdin.write(b"probe")
            probe.proc.stdin.close()
            probe.proc.stdout.read()
            probe.finish()
    except (BackupError, OSError) as exc:
        raise BackupError(f"{found} cannot do what backups need ({exc}). It must be OpenSSL 1.1.1 or LibreSSL 3.1 or "
                          f"newer.") from None
    return found


class _Openssl:
    """`openssl enc` or `enc -d` as a child process. The passphrase goes in through a pipe the child reads and nothing
    else can open."""

    def __init__(self, command, decrypt, passphrase, iterations, stdin, stdout):
        self.command, self.decrypt, self.iterations = command, decrypt, iterations
        self.passphrase, self.stdin, self.stdout = passphrase, stdin, stdout
        self.proc = self.err = None

    def __enter__(self):
        read, write = os.pipe()
        try:
            os.write(write, self.passphrase + b"\n")
        finally:
            os.close(write)
        try:
            self.err = tempfile.TemporaryFile()
            args = [self.command, "enc", f"-{CIPHER}", "-pbkdf2", "-md", "sha256", "-iter", str(self.iterations),
                    "-pass", f"fd:{read}"] + (["-d"] if self.decrypt else [])
            self.proc = subprocess.Popen(args, stdin=self.stdin, stdout=self.stdout, stderr=self.err,
                                         pass_fds=(read,))
        finally:
            os.close(read)
        return self

    def finish(self):
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe and not pipe.closed:
                pipe.close()
        code = self.proc.wait()
        if code:
            self.err.seek(0)
            said = self.err.read().decode("utf-8", "replace").strip().splitlines()
            raise BackupError(f"openssl stopped with exit code {code}" + (f": {said[-1][:200]}" if said else ""))

    def __exit__(self, *_):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()  # our own child, started above
            self.proc.wait()
        for pipe in (self.proc.stdin, self.proc.stdout) if self.proc else ():
            if pipe and not pipe.closed:
                pipe.close()
        if self.err:
            self.err.close()


def _key(passphrase, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", passphrase, salt, iterations, DIGEST)


def _tag(key, head, fh=None):
    mac = hmac.new(key, head, hashlib.sha256)
    while fh is not None:
        block = fh.read(CHUNK)
        if not block:
            break
        mac.update(block)
    return mac.digest()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(db_path, out):
    """A consistent copy of the store at `db_path` in the new file `out`, made with SQLite's backup API: it reads one
    moment of the store, so an ingest writing meanwhile is neither blocked nor half in the copy. Returns (items, rows)."""
    try:
        src = store.peek(db_path)
        dst = sqlite3.connect(out)
    except sqlite3.Error as exc:
        raise BackupError(f"could not open the knowledge base at {db_path} ({exc})") from None
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # one file, nothing left in a journal
        found = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if found != "ok":
            raise BackupError(f"the knowledge base at {db_path} is damaged ({found}), and a backup of it could not be "
                              f"restored, so none was made")
        return _counts(dst)
    except sqlite3.Error as exc:
        raise BackupError(f"could not copy the knowledge base at {db_path} ({exc})") from None
    finally:
        dst.close()
        src.close()


def _counts(db):
    return db.execute("SELECT count(*) FROM (SELECT DISTINCT source, source_id FROM docs)").fetchone()[0], \
        db.execute("SELECT count(*) FROM docs").fetchone()[0]


def _member(name, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, 0o600, 0  # no user name, no time: only the contents
    return info


def _optional(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(SMALL + 1)
    except FileNotFoundError:
        return None


def make(paths, dest, passphrase, overwrite=False, iterations=None):
    """Writes the backup to `dest` and checks the file by reading all of it back. It appears under its name only once it
    is whole, and a file already there is replaced only with `overwrite`. Returns the Manifest."""
    iterations = ITERATIONS if iterations is None else iterations
    command = openssl_path()
    target = os.path.realpath(paths.db)
    if not os.path.exists(target):
        raise BackupError(f"there is no knowledge base at {paths.db}, so there is nothing to back up")
    live = {os.path.realpath(p) for p in (target, f"{target}-wal", f"{target}-shm", paths.config_file,
                                          paths.config_file.parent / ids.FILE_NAME)}
    if os.path.realpath(dest) in live:
        raise BackupError(f"{dest} is one of your live Superhistory files. Name another file for the backup.")
    if os.path.lexists(dest) and not overwrite:
        raise BackupError(f"{dest} exists. Name another file, or add --force to replace it.")
    config = _optional(paths.config_file)
    install = _optional(paths.config_file.parent / ids.FILE_NAME)
    for name, data in ((CONFIG, config), (INSTALL, install)):
        if data is not None and len(data) > SMALL:
            raise BackupError(f"{name} is larger than {SMALL:,} bytes, which a config never is")
    os.makedirs(paths.data_dir, exist_ok=True)
    partial = dest.with_name(f".{dest.name}.partial-{os.getpid()}")
    made = False
    work = tempfile.mkdtemp(dir=paths.data_dir, prefix=".backup-")  # private to the user, removed below
    try:
        copy = os.path.join(work, STORE)
        items, rows = snapshot(target, copy)
        manifest = Manifest(FORMAT, store.now_iso(), __version__, items, rows, os.path.getsize(copy), _sha256(copy))
        try:
            fd = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            raise BackupError(f"could not write {dest.parent}: {exc.strerror or exc}") from None
        made = True
        with os.fdopen(fd, "r+b", buffering=0) as out:
            _seal(command, out, copy, manifest, config, install, passphrase, iterations)
            os.fsync(out.fileno())
        read(partial, passphrase)  # a backup counts once it has been read back whole
        if os.path.lexists(dest) and not overwrite:
            raise BackupError(f"{dest} appeared while the backup was made. Name another file, or add --force.")
        os.replace(partial, dest)
        return manifest
    except OSError as exc:
        raise BackupError(f"could not write the backup: {exc.strerror or exc}") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if made:  # a file of that name that this run did not make is not this run's to remove
            with contextlib.suppress(FileNotFoundError):
                os.remove(partial)


def _seal(command, out, copy, manifest, config, install, passphrase, iterations):
    salt = os.urandom(16)
    head = HEADER.pack(MAGIC, FORMAT, iterations, salt)
    key = _key(passphrase, salt, iterations)
    out.write(head + _tag(key, head) + b"\0" * DIGEST)
    with _Openssl(command, False, passphrase, iterations, subprocess.PIPE, out) as sealing:
        try:
            with gzip.GzipFile(fileobj=sealing.proc.stdin, mode="wb", compresslevel=6, mtime=0) as gz, \
                    tarfile.open(fileobj=gz, mode="w|") as tar:
                raw = json.dumps(dataclasses.asdict(manifest), sort_keys=True).encode("utf-8")
                tar.addfile(_member(MANIFEST, len(raw)), io.BytesIO(raw))
                for name, data in ((CONFIG, config), (INSTALL, install)):
                    if data is not None:
                        tar.addfile(_member(name, len(data)), io.BytesIO(data))
                with open(copy, "rb") as fh:
                    tar.addfile(_member(STORE, manifest.store_bytes), fh)
        except BrokenPipeError:
            pass  # openssl stopped; finish() says why
        sealing.proc.stdin.close()
        sealing.finish()
    out.seek(0)
    mac = hmac.new(key, out.read(CHECKED), hashlib.sha256)
    out.seek(BODY)
    for block in iter(lambda: out.read(CHUNK), b""):
        mac.update(block)
    out.seek(CHECKED)
    out.write(mac.digest())


def _opened(path, fh):
    """The header of the backup in `fh`, as (iterations, salt), after the checks that need no passphrase."""
    data = fh.read(BODY)
    if not data.startswith(MAGIC[:len(data)]) or not data:
        raise BackupError(f"{path} is not a Superhistory backup")
    if len(data) < BODY:
        raise BackupError(f"{path} is cut short, so it is not a whole backup")
    _, number, iterations, salt = HEADER.unpack(data[:HEADER.size])
    if number > FORMAT:
        raise BackupError(f"{path} is a format {number} backup, made by a newer Superhistory than this one, which reads "
                          f"up to format {FORMAT}. Update Superhistory, then restore it.")
    if number < 1 or not 1 <= iterations <= MAX_ITERATIONS:
        raise BackupError(f"{path} is damaged: its first bytes are not a backup's")
    return data, iterations, salt


def read(path, passphrase, store_to=None):
    """Opens the backup at `path`, checking all of it first. With `store_to`, the store in it is written there, a new
    file. Returns the Contents. Raises BackupError, naming the file, for a wrong passphrase, a damaged file, or a
    file that is not a backup."""
    if store_to is not None and os.path.lexists(store_to):
        raise BackupError(f"{store_to} is in the way")
    try:
        with open(path, "rb", buffering=0) as fh:
            data, iterations, salt = _opened(path, fh)
            key = _key(passphrase, salt, iterations)
            head = data[:HEADER.size]
            if not hmac.compare_digest(_tag(key, head), data[HEADER.size:CHECKED]):
                raise BackupError(f"that passphrase does not open {path}, or its first bytes are damaged. Nothing was "
                                  f"changed. A passphrase that is lost cannot be got back: no one can open the file.")
            if not hmac.compare_digest(_tag(key, data[:CHECKED], fh), data[CHECKED:BODY]):
                raise BackupError(f"{path} is damaged or cut short: it does not match its own check. Nothing was "
                                  f"changed.")
            fh.seek(BODY)
            with _Openssl(openssl_path(), True, passphrase, iterations, fh, subprocess.PIPE) as opening:
                try:
                    contents = _unpack(opening.proc.stdout, store_to)
                    while opening.proc.stdout.read(CHUNK):
                        pass
                    opening.finish()
                except BaseException:
                    if store_to is not None:
                        with contextlib.suppress(FileNotFoundError):
                            os.remove(store_to)
                    raise
            return contents
    except FileNotFoundError:
        raise BackupError(f"there is no file {path}") from None
    except OSError as exc:
        raise BackupError(f"could not read {path}: {exc.strerror or exc}") from None


def _unpack(stream, store_to):
    unreadable = "passed its check but holds something this Superhistory cannot read"
    try:
        with gzip.GzipFile(fileobj=stream, mode="rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
            manifest = config = install = None
            seen = False
            for member in tar:
                if manifest is None:
                    if member.name != MANIFEST or member.size > SMALL:
                        raise BackupError(f"the backup {unreadable}")
                    manifest = Manifest.parse(tar.extractfile(member).read())
                    if manifest.format != FORMAT:
                        raise BackupError(f"the backup {unreadable}")
                elif member.name in (CONFIG, INSTALL):
                    if member.size > SMALL:
                        raise BackupError(f"the backup {unreadable}")
                    data = tar.extractfile(member).read()
                    config, install = (data, install) if member.name == CONFIG else (config, data)
                elif member.name == STORE:
                    _copy_store(tar.extractfile(member), member.size, manifest, store_to)
                    seen = True
                    break
            if not seen:
                raise BackupError(f"the backup {unreadable}")
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise BackupError(f"the backup {unreadable} ({type(exc).__name__})") from None
    if install is not None and not ids.INSTALL_ID.fullmatch(install.decode("ascii", "replace").strip()):
        raise BackupError(f"the backup {unreadable}")
    return Contents(manifest, config, install)


def _copy_store(source, size, manifest, store_to):
    digest, seen = hashlib.sha256(), 0
    sink = os.fdopen(os.open(store_to, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") if store_to else None
    try:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
            seen += len(block)
            if sink:
                sink.write(block)
    finally:
        if sink:
            sink.close()
    if size != manifest.store_bytes or seen != size or digest.hexdigest() != manifest.store_sha256:
        raise BackupError("the backup passed its check but its store does not match its list of contents")


def would_replace(paths):
    """What a restore would replace: the files now at the store's, the config's and the install id's places."""
    target = os.path.realpath(paths.db)
    return [Path(p) for p in (target, f"{target}-wal", f"{target}-shm", paths.config_file,
                               paths.config_file.parent / ids.FILE_NAME) if os.path.lexists(p)]


def _describe_existing(paths):
    target = os.path.realpath(paths.db)
    found = []
    if os.path.lexists(target):
        try:
            with contextlib.closing(store.peek(target)) as db:
                items, _ = _counts(db)
            found.append(f"the knowledge base at {paths.db} ({items:,} items)")
        except sqlite3.Error:
            found.append(f"the knowledge base at {paths.db}")
    for label, path in (("config", paths.config_file), ("install id", paths.config_file.parent / ids.FILE_NAME)):
        if os.path.lexists(path):
            found.append(f"the {label} at {path}")
    return found


def _check_store(path, manifest, whole):
    """Whether the store at `path` is the one the manifest describes: sound, and holding its items and rows."""
    try:
        with contextlib.closing(store.peek(path)) as db:
            found = db.execute("PRAGMA integrity_check" if whole else "PRAGMA quick_check").fetchone()[0]
            if found != "ok":
                raise BackupError(f"the restored knowledge base is not sound ({found})")
            if whole:
                db.execute("INSERT INTO docs_fts(docs_fts) VALUES('integrity-check')")
            if _counts(db) != (manifest.items, manifest.rows):
                raise BackupError("the restored knowledge base does not hold the items the backup lists")
    except sqlite3.Error as exc:
        raise BackupError(f"the restored knowledge base cannot be read ({exc})") from None


def restore(paths, src, passphrase, agree):
    """Puts the backup at `src` in place of the store, config and install id here. The backup is opened and its store
    checked first, with nothing here touched. Then `agree(what)` is asked about what is there now, and if it says no,
    nothing is changed and None is returned. What is replaced is set aside, and goes only once the restored store has
    been checked where it now stands; if any step fails, it is put back. Returns the Contents."""
    target = os.path.realpath(paths.db)
    config, install = paths.config_file, paths.config_file.parent / ids.FILE_NAME
    with store.exclusive(paths.lock) as held:
        if not held:
            raise BackupError("an ingest is running; try again when it finishes")
        left = [f"{p}{ASIDE}" for p in (target, f"{target}-wal", f"{target}-shm", config, install)
                if os.path.lexists(f"{p}{ASIDE}")]
        if left:
            raise BackupError(f"an earlier restore left what it replaced aside: {', '.join(left)}. Move or remove "
                              f"{'it' if len(left) == 1 else 'them'}, then restore again.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # A folder of its own, beside the store so the final move is a rename: no name a user could have given a
        # backup or a config is ever taken or deleted.
        staging = tempfile.mkdtemp(dir=os.path.dirname(target), prefix=".restore-")
        fresh = os.path.join(staging, STORE)
        try:
            contents = read(src, passphrase, store_to=fresh)
            _check_store(fresh, contents.manifest, whole=True)
            existing = _describe_existing(paths)
            if existing and not agree(existing, contents.manifest):
                return None
            _swap(paths, target, fresh, contents)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return contents


def _swap(paths, target, fresh, contents):
    config, install = paths.config_file, paths.config_file.parent / ids.FILE_NAME
    names = [f"{target}-wal", f"{target}-shm", target, str(config), str(install)]
    asides, placed = [], []
    try:
        # The WAL files go first, so a WAL file at the store's name is never read into the restored store.
        for name in names:
            if os.path.lexists(name):
                os.replace(name, name + ASIDE)
                asides.append(name)
        os.replace(fresh, target)
        placed.append(target)
        for name, data in ((config, contents.config), (install, contents.install_id)):
            if data is not None:
                os.makedirs(name.parent, exist_ok=True)
                handle, new = tempfile.mkstemp(dir=name.parent, prefix=".restore-")
                try:
                    with os.fdopen(handle, "wb") as fh:
                        fh.write(data)
                    os.replace(new, name)
                except BaseException:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(new)
                    raise
                placed.append(str(name))
        _check_store(target, contents.manifest, whole=False)
    except BaseException:
        for name in reversed(placed):
            with contextlib.suppress(FileNotFoundError):
                os.remove(name)
        if target in placed:  # only then can a WAL file here be the restored store's own, not the old store's
            for suffix in ("-wal", "-shm"):
                if f"{target}{suffix}" not in asides:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(f"{target}{suffix}")
        for name in reversed(asides):
            os.replace(name + ASIDE, name)
        raise
    for name in asides:
        os.remove(name + ASIDE)
