"""Item ids across computers. Every stored source_id starts with the prefix of the install that read it, so two
computers never write the same item, and each one prunes, summarizes and syncs only its own items.

The prefix is an install id of 64 random bits, made once per install. It is not the host name, and nothing about
the computer or person goes into it. Items this install reads on another computer over SSH are still its own, under
"<install id>@<host>:", where host is the name given in [hosts.NAME], not its SSH address.

A whole source_id is private: after the prefix comes the source's own id, which can hold a file path, a mail
account or a chat handle such as a phone number. Anything that carries items off this computer protects source_id
as it protects the text.
"""
import base64
import os
import re
import tempfile

INSTALL_ID = re.compile(r"^c-[a-z2-7]{13}$")
FILE_NAME = "install-id"


def new_install_id():
    return "c-" + base64.b32encode(os.urandom(8)).decode("ascii").rstrip("=").lower()


def install_id(paths):
    """This install's id, read from the file beside the config, made on first use."""
    path = paths.config_file.parent / FILE_NAME
    try:
        found = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        found = _make(path)
    if not INSTALL_ID.match(found):
        raise ValueError(f"{path} does not hold an install id; delete it to make a new one")
    return found


def _make(path):
    """Writes a new id to a private temp file and links it into place, so no run reads a half-written file, and two
    first runs at once both keep the one that landed first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{FILE_NAME}-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(new_install_id() + "\n")
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)
    return path.read_text(encoding="ascii").strip()


def prefix(install, host=None):
    return f"{install}:" if host is None else f"{install}@{host}:"


def owner(source_id):
    """The install id that wrote this item, or None for an id with no install prefix."""
    head = source_id.split(":", 1)[0].split("@", 1)[0]
    return head if INSTALL_ID.match(head) else None


def native(source_id):
    return source_id.split(":", 1)[1] if ":" in source_id else source_id
