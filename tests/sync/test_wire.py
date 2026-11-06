import hashlib
import hmac
import unittest
import unittest.mock

import kb
from kb.sync import keys, pack


class WireNamesTest(unittest.TestCase):
    """Peers on other versions and the relay read these bytes as they are, whatever the product is called."""

    def test_the_names_the_protocol_is_built_from_do_not_follow_the_product_name(self):
        self.assertEqual((pack.NS_PACK, pack.NS_ROSTER, pack.NS_ENVELOPE),
                         ("varve-sync-pack", "varve-sync-roster", "varve-sync-envelope"))
        self.assertEqual(keys.derive(b"secret", "label"),
                         hmac.new(b"secret", b"varve-sync/v1/label", hashlib.sha256).digest())
        self.assertEqual(keys.WIRE_NAME, "varve")

    def test_the_wire_name_is_not_read_from_the_product_name(self):
        with unittest.mock.patch.object(kb, "NAME", "Superhistory"):
            self.assertEqual(keys.derive(b"secret", "label"),
                             hmac.new(b"secret", b"varve-sync/v1/label", hashlib.sha256).digest())


if __name__ == "__main__":
    unittest.main()
