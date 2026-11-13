import os
import tempfile
import unittest
import uuid

from kb.sync import keys, pack, records

DEV_A = str(uuid.uuid4())
DEV_B = str(uuid.uuid4())


def header(**over):
    h = {"epoch": 1, "author": DEV_A, "dseq": 1, "prev": "", "roster_version": 1, "roster_hash": "0" * 64,
         "snapshot": None}
    h.update(over)
    return h


class PackFormatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ssh = {}
        for dev in (DEV_A, DEV_B):
            path = os.path.join(cls.tmp.name, dev)
            keys.make_device_keys(path + ".age", path + ".ssh")
            cls.ssh[dev] = path + ".ssh"
        cls.identity = keys.new_identity()
        cls.recipient = keys.recipient_of(cls.identity)
        rec = records.Record("item", "notes", "c-aaaaaaaaaaaaa:x", 7, {"ts": "t", "title": "T", "meta": {"hash": "h"},
                                                                       "rows": ["hello"]})
        cls.lines = [records.to_json(rec)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def entry(self, dev):
        return {"device_id": dev, "ssh_key": keys.ssh_public(self.ssh[dev])}

    def test_pack_framing_round_trip(self):
        plain = pack.build(header(), self.lines, self.ssh[DEV_A])
        blob = pack.seal(plain, self.recipient)
        self.assertNotIn(b"hello", blob)
        pk = pack.parse(keys.open_with([self.identity], blob))
        self.assertEqual(pk.header, header())
        self.assertEqual([records.to_json(r) for r in pk.records], self.lines)
        self.assertEqual(pk.plain, plain)
        self.assertTrue(pack.verify(pk, self.entry(DEV_A)))
        self.assertIn(len(blob) - 16 * 1024, range(0, 400), "a small pack seals to the 16 KiB bucket plus age's header")

    def test_truncated_pack_is_refused(self):
        blob = pack.seal(pack.build(header(), self.lines, self.ssh[DEV_A]), self.recipient)
        with self.assertRaises(keys.Refused):
            keys.open_with([self.identity], blob[: len(blob) // 2])
        padded = pack.pad(pack.build(header(), self.lines, self.ssh[DEV_A]), pack.bucket)
        with self.assertRaises(keys.Refused):
            pack.parse(padded[:40])
        bent = bytearray(padded)
        bent[-1] = 1
        with self.assertRaises(keys.Refused):
            pack.parse(bytes(bent))

    def test_signature_from_another_device_is_refused(self):
        forged = pack.parse(pack.pad(pack.build(header(author=DEV_A), self.lines, self.ssh[DEV_B]), pack.bucket))
        self.assertFalse(pack.verify(forged, self.entry(DEV_A)))
        honest = pack.parse(pack.pad(pack.build(header(), self.lines, self.ssh[DEV_A]), pack.bucket))
        self.assertTrue(pack.verify(honest, self.entry(DEV_A)))

    def test_buckets(self):
        self.assertEqual(pack.bucket(10), 16 << 10)
        self.assertEqual(pack.bucket((16 << 10) + 1), 32 << 10)
        self.assertEqual(pack.bucket(1 << 20), 1 << 20)
        self.assertEqual(pack.bucket((1 << 20) + 1), 2 << 20)
        self.assertEqual(pack.small_bucket(4097), 8192)


class CodesTest(unittest.TestCase):
    def test_codes_round_trip_and_tolerate_typing(self):
        code, secret = keys.new_recovery_code()
        self.assertRegex(code, r"^VRC1(-[0-9A-Z]{4}){8}$")
        self.assertEqual(keys.parse_recovery_code(code.lower().replace("-", " ")), secret)
        pcode, psecret = keys.new_pairing_code()
        self.assertRegex(pcode, r"^VP1(-[0-9A-Z]{4}){6}-[0-9A-Z]{2}$")
        self.assertEqual(keys.parse_pairing_code(pcode), psecret)
        with self.assertRaises(ValueError):
            keys.parse_pairing_code(pcode[:-1])

    def test_recovery_code_becomes_an_age_key(self):
        _, secret = keys.new_recovery_code()
        identity, tag_key = keys.recovery_keys(secret)
        self.assertRegex(keys.recipient_of(identity), r"^age1[a-z0-9]{58}$")
        self.assertEqual(len(tag_key), 32)

    def test_words(self):
        self.assertEqual(len(set(keys.WORDS)), 256)

    def test_plugin_recipient_never_reaches_age(self):
        with self.assertRaises(keys.Refused):
            keys.seal(["age1yubikey1" + "q" * 59], b"x")


if __name__ == "__main__":
    unittest.main()
