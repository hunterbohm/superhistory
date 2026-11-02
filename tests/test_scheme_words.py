"""A word for an HTTP auth scheme in an ordinary note's path, "Basic Statistics/notes.md" or "Ring Bearer
responsibilities/notes.md", is no credential: the text mask reads it as words, so the note's id, its stored path and
anything that checks an id with holds_secret() keep it. A real credential after the same words is masked as main masks
it. Main's mask is this branch's with that one reading switched off (kb.mask._scheme_words), so the comparison with it
is exact."""
import contextlib
import io
import os
import random
import string
import tempfile
import unittest
from unittest import mock

from kb import cli, mask as mask_module, store
from kb.mask import holds_secret, mask, mask_with_origin, secret_in_id

INSTALL = "c-aaaaaaaaaaaaa"
SCHEMES = ("Basic", "Bearer", "Token", "Digest", "Negotiate", "OAuth", "Bot", "ApiKey", "Hoba", "Mutual", "Key")
WORDS = ("Statistics", "statistics", "notes", "Training", "Income", "Experiment", "duties", "responsibilities", "list",
         "Bonds", "History", "Economics", "plan", "Weekly", "Contracts", "Reading", "summary", "Ideas", "meeting",
         "Agenda", "Recipes", "Cooking", "garden", "Shopping", "minutes", "Research", "draft", "Final", "review")


def as_on_main(text):
    """main's mask: this branch's with scheme words read as a credential, as main reads them."""
    with mock.patch.object(mask_module, "_scheme_words", lambda *_: False):
        return mask(text)


def note_path(r):
    """An ordinary note's path that holds an auth-scheme word: folders and a file name of words."""
    def name():
        words = [r.choice(WORDS) for _ in range(r.randint(1, 3))]
        words.insert(r.randint(0, len(words)), r.choice(SCHEMES))
        return r.choice((" ", "_", "-")).join(words)
    parts = [name() for _ in range(r.randint(1, 3))]
    return "/".join(parts) + r.choice((".md", ".txt", ".org", "/notes.md", "/README.md"))


def credential(r):
    """A credential as one follows Basic or Bearer: base64 of user:password, or a random token."""
    import base64
    alnum = string.ascii_letters + string.digits
    token = "".join(r.choice(alnum) for _ in range(r.randint(20, 40))) + r.choice(string.digits)
    return r.choice((("Basic", base64.b64encode(f"sam:walrus{r.randint(10, 99)}secret".encode()).decode()),
                     ("Bearer", token), ("Bearer", "eyJ" + token + "." + token[::-1] + "." + token[:12]),
                     ("Basic", base64.b64encode(f"deploy:{token}".encode()).decode())))


def lowercase_token(r):
    """A credential of lower-case letters alone, as the first review wrote them: one run, groups joined by "-" or
    "/", or a run with a number after a dot."""
    letters = "".join(r.choice(string.ascii_lowercase) for _ in range(r.randint(16, 40)))
    how = r.random()
    if how < 0.4:
        return letters
    if how < 0.8:
        size = r.randint(3, 8)
        return r.choice("-/").join(letters[i:i + size] for i in range(0, len(letters), size))
    return f"{letters}.{r.randint(1, 99999)}"


def basic_credential(r):
    """base64 of "user:password" as a Basic header holds it, the user and password in ASCII, Latin-1 or UTF-8, with
    its padding or without."""
    import base64
    alphabet = r.choice((string.ascii_letters + string.digits, "abcäöüßéèªº¿", "ążłéтест"))
    user = "".join(r.choice(alphabet) for _ in range(r.randint(1, 8)))
    password = "".join(r.choice(alphabet + "?/!") for _ in range(r.randint(1, 16)))
    encoding = "latin-1" if all(ord(c) < 256 for c in user + password) and r.random() < 0.5 else "utf-8"
    encoded = base64.b64encode(f"{user}:{password}".encode(encoding)).decode()
    return encoded.rstrip("=") if r.random() < 0.5 else encoded


def single_byte_login(r):
    """base64 of "user:password" in a single-byte code page, Windows-1252, Windows-1251, IBM 437 or Latin-1, from any
    of its printable characters, padded or not."""
    import base64
    encoding = r.choice(("cp1252", "cp1251", "cp437", "latin-1"))
    printable = [c for c in (bytes([b]).decode(encoding, "ignore") for b in range(32, 256)) if c and c.isprintable()
                 and c != ":"]
    login = "".join(r.choice(printable) for _ in range(r.randint(1, 6))) + ":" + "".join(
        r.choice(printable) for _ in range(r.randint(1, 12)))
    encoded = base64.b64encode(login.encode(encoding)).decode()
    return encoded.rstrip("=") if r.random() < 0.5 else encoded


class SchemeWordTest(unittest.TestCase):
    def test_the_audit_paths_are_ordinary(self):
        for path in ("Basic Statistics/notes.md", "Ring Bearer responsibilities/notes.md", "Ring Bearer duties/list.md",
                     "Basic Income/Experiment Notes/summary.md", "Bearer Bonds/History/notes.md"):
            with self.subTest(path=path):
                self.assertEqual(mask(path), path)
                self.assertFalse(holds_secret(f"{INSTALL}:{path}"))

    def test_ordinary_note_paths_with_scheme_words_are_kept(self):
        for seed in range(2000):
            path = note_path(random.Random(f"path:{seed}"))
            with self.subTest(path=path):
                self.assertEqual(mask(path), path)
                self.assertFalse(holds_secret(f"{INSTALL}:{path}"))
                self.assertFalse(secret_in_id(path))
                self.assertEqual(mask(f"see {path} for the plan"), f"see {path} for the plan")

    def test_a_real_credential_next_to_them_is_masked(self):
        for seed in range(2000):
            r = random.Random(f"credential:{seed}")
            scheme, secret = credential(r)
            path = note_path(r)
            for text in (f"{path} {scheme} {secret}", f"Authorization: {scheme} {secret}\n{path}",
                         f"curl -H '{scheme} {secret}' # {path}", f"{scheme} {secret}"):
                with self.subTest(seed=seed, text=text):
                    masked = mask(text)
                    self.assertNotIn(secret[:12], masked)
                    if path in text:
                        self.assertIn(path, masked, "the path next to it stays")
                    self.assertTrue(holds_secret(text))

    def test_the_first_reviews_credentials_are_masked_as_on_main(self):
        for text in ("Authorization: Bearer eiaoumqnavxozpiuafeldogy", "Bearer aubx-ecjq-ivkx-opzy",
                     "Bearer eiaoumqnavxozpiuafeldogy.1234", "Basic YWE/YWE/YWE/Omc", "Basic qjqhabqkqdpj/iyvt//i",
                     "Bearer aubx/ecjq/ivkx/opzy", "Authorization: Basic Statistics/notes.md"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), as_on_main(text))

    def test_the_second_reviews_credentials_are_masked_as_on_main(self):
        for text in ("Bearer fcedccbafeaedfbdfadcecaadddcabab see notes.md", "Basic YTowyi//lutaeboj.md",
                     "Basic YTowyi//lutaeboj stored in notes.md", "Bearer face/bead/cafe/deed see notes.md",
                     "Bearer fcedccbafeaedfbdfadcecaadddcabab stored in docs/notes.md"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), as_on_main(text))

    def test_the_third_reviews_credentials_are_masked_as_on_main(self):
        for text in ("Bearer dapi" + "facedeca" * 4 + " stored in docs/notes.md", "Bearer " + "face" * 8 + "/notes.md",
                     "Bearer dapi" + "facedeca" * 4 + ".md", "Bearer " + "decafe" * 5 + "/Weekly notes.md"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), as_on_main(text))

    def test_hex_bodies_after_a_prefix_are_masked_as_on_main(self):
        # A provider's token is often a short prefix and a hex body; one whose body happens to hold no digit is still
        # a token, in a run of its own or before a path.
        for seed in range(2000):
            r = random.Random(f"hex-body:{seed}")
            prefix = r.choice(("", "dapi", "sk_", "pat-", "key", "tok_", "".join(r.choice(string.ascii_lowercase)
                                                                                for _ in range(r.randint(2, 5)))))
            token = prefix + "".join(r.choice("abcdef") for _ in range(r.randint(16, 48)))
            for tail in (" stored in docs/notes.md", "/notes.md", ".md", " See Docs/notes.md"):
                text = f"Bearer {token}{tail}"
                with self.subTest(text=text):
                    self.assertEqual(mask(text), as_on_main(text))

    def test_credentials_with_a_file_named_after_them_are_masked_as_on_main(self):
        # A token followed by prose that names a file is no path: the words after it are not its folders.
        for seed in range(2000):
            r = random.Random(f"tail:{seed}")
            token = r.choice((lambda: "".join(r.choice("abcdef") for _ in range(r.randint(16, 40))),
                              lambda: lowercase_token(r)))()
            secret = r.choice((lambda: "Bearer " + token, lambda: "Basic " + single_byte_login(r)))()
            for tail in (" see notes.md", " stored in docs/notes.md", ".md" if secret.startswith("Basic") else ""):
                text = secret + tail
                with self.subTest(text=text):
                    self.assertEqual(mask(text), as_on_main(text))

    def test_lower_case_and_basic_credentials_are_masked_as_on_main(self):
        for seed in range(2000):
            r = random.Random(f"lower:{seed}")
            secret = r.choice((lambda: "Bearer " + lowercase_token(r), lambda: "Basic " + basic_credential(r)))()
            for text in (secret, f"Authorization: {secret}", f"curl -H 'Authorization: {secret}' x", f"see {secret}"):
                with self.subTest(text=text):
                    self.assertEqual(mask(text), as_on_main(text))

    def test_a_field_after_a_kept_path_reads_as_it_reads_alone(self):
        # Main's mask took the path's words, and with them the field's name after them, so it never read the value.
        for field in ("Cookie: chocolate chip recipe", "password: hunter22x", "api_key: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1"):
            for path in ("Basic Statistics/notes.md", "Ring Bearer responsibilities/notes.md"):
                text = f"{path}: {field}"
                with self.subTest(text=text):
                    self.assertEqual(mask(text), f"{path}: {mask(field)}")

    def test_main_hides_nothing_this_keeps_but_scheme_words(self):
        # Every character main's mask hides, this one hides, but in a run of words after Basic or Bearer.
        alnum = string.ascii_letters + string.digits
        for seed in range(1500):
            r = random.Random(f"mix:{seed}")
            pieces = []
            for _ in range(r.randint(2, 8)):
                pick = r.random()
                if pick < 0.25:
                    pieces.append(note_path(r))
                elif pick < 0.35:
                    pieces.append(f"{note_path(r)}: " + r.choice(("Cookie: chocolate chip recipe", "password: walrus2x",
                                                                 "api_key: q8Zr2Lm7Xk4Vp9Nt3Bw6Hc1", "plain words")))
                elif pick < 0.7:
                    pieces.append(" ".join(credential(r)))
                else:
                    pieces.append(r.choice(("password=", "api_key: ", "token is ")) + "".join(
                        r.choice(alnum) for _ in range(14)) + "7")
            text = r.choice((" ", "\n", " | ")).join(pieces)
            with self.subTest(seed=seed):
                ours, kept = mask_with_origin(text)
                with mock.patch.object(mask_module, "_scheme_words", lambda *_: False):
                    theirs, theirs_kept = mask_with_origin(text)
                shown_here = set(i for i in kept if i >= 0) - set(i for i in theirs_kept if i >= 0)
                # What main shows and this hides: a field's value whose name main hid with the words before it, so
                # it never read the value, which it masks wherever it reads the name.
                hidden_here = set(i for i in theirs_kept if i >= 0) - set(i for i in kept if i >= 0)
                self.assertEqual(hidden_here - values_named_where_main_hid(text, set(theirs_kept)), set(),
                                 f"hides more than main: {ours!r} vs {theirs!r}")
                # Each run kept starts inside such words; it may run on past them, through what main took with
                # them, as the rest glued to the placeholder it put there.
                for start, end in runs(shown_here):
                    self.assertTrue(any(mask_module._scheme_words(m) for m in scheme_matches(text)
                                        if m.start() <= start < m.end()), f"keeps {text[start:end]!r}")

    def test_a_note_with_a_scheme_word_in_its_path_is_stored_with_its_path_and_found(self):
        with tempfile.TemporaryDirectory() as home:
            notes = os.path.join(home, "notes", "Basic Statistics")
            os.makedirs(notes)
            with open(os.path.join(notes, "notes.md"), "w", encoding="utf-8") as fh:
                fh.write("the pelican regression lecture\n")
            with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as fh:
                fh.write(f'machine = "studio"\n[sources.notes]\npath = "{os.path.join(home, "notes")}"\n')
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"KB_HOME": home}), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["ingest", "--no-summaries"]), 0)
                self.assertEqual(cli.main(["search", "pelican"]), 0)
            self.assertIn("Basic Statistics", out.getvalue())
            db = store.connect(os.path.join(home, "data", "kb.sqlite"))
            try:
                (sid, meta), = db.execute("SELECT source_id, metadata FROM docs").fetchall()
            finally:
                db.close()
            self.assertTrue(sid.endswith(":Basic Statistics/notes.md"), sid)
            self.assertIn("Basic Statistics/notes.md", meta, "the stored path is the file's")


def values_named_where_main_hid(text, main_shown):
    """The positions of each field's value, as the mask reads a cookie's or a secret's name, whose name main hid."""
    out = set()
    fields = [(m.span(1), (m.end(1), m.end())) for m in mask_module.PREFIXED_SECRETS[0].finditer(text)]
    for pattern in (mask_module.KEYED_SECRET, mask_module.ENV_KEY_SECRET, mask_module.JSON_SECRET):
        fields += [(m.span(1), m.span(3)) for m in pattern.finditer(text)]
    for (name_start, name_end), (start, end) in fields:
        if any(i not in main_shown for i in range(name_start, name_end)):
            out.update(range(start, end))
    return out


def runs(positions):
    out = []
    for i in sorted(positions):
        if out and out[-1][1] == i:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return [tuple(run) for run in out]


def scheme_matches(text):
    return [m for pattern in (mask_module.BASIC, mask_module.BEARER) for m in pattern.finditer(text)]


if __name__ == "__main__":
    unittest.main()
