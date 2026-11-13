import hashlib
import hmac
import unittest

from kb.sync import keys, pack


class WireNamesTest(unittest.TestCase):
    """The relay and every computer read these bytes as they are. A rename that changes them turns this red, because
    the relay has to change with it and every sync group starts over."""

    def test_the_names_the_protocol_is_built_from(self):
        self.assertEqual((pack.NS_PACK, pack.NS_ROSTER, pack.NS_ENVELOPE),
                         ("superhistory-sync-pack", "superhistory-sync-roster", "superhistory-sync-envelope"))
        self.assertEqual(keys.derive(b"secret", "label"),
                         hmac.new(b"secret", b"superhistory-sync/v1/label", hashlib.sha256).digest())
        self.assertEqual(keys.RENEW_NAMESPACE, "superhistory-renew@v1")


if __name__ == "__main__":
    unittest.main()
