"""The relay gets only a sync token, which the app refuses, and the app gets only the other token, which the relay refuses.
So whoever runs the relay never holds a token that opens the app."""
import contextlib
import os
import io
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kb import cli
from kb.paths import HOME_ENV
from kb.sync import keys, login, relay
from kb.sync.client import SyncClient, SyncError

from .support import World


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.bearers = []
        self.world.fake.before_hooks.append(
            lambda method, path, headers: self.bearers.append((path, headers.get("authorization", ""))))

    def tearDown(self):
        self.world.close()

    def saved(self, computer):
        return keys.load_json(login.Paths(computer.paths).login)

    def sent_to(self, prefix):
        return {bearer.removeprefix("Bearer ") for path, bearer in self.bearers if path.startswith(prefix)}

    def test_the_relay_never_gets_the_token_the_app_takes(self):
        (one,), _ = self.world.group("computer 1")
        self.bearers.clear()  # from here on only the device's tokens go
        one.item("notes", "n1", "a made-up note")
        one.sync()
        saved = self.saved(one)
        self.assertNotEqual(saved["app_token"], saved["sync_token"])
        self.assertEqual(self.sent_to("/sync/"), {saved["sync_token"]}, "the relay gets the sync token and nothing else")
        self.assertEqual(self.sent_to("/v1/"), {saved["app_token"]}, "the app gets the other token")
        self.assertEqual(self.world.fake.request("GET", "/v1/devices", headers={
            "authorization": f"Bearer {saved['sync_token']}"})[0], 401, "the app refuses the sync token")
        self.assertEqual(self.world.fake.request("GET", "/sync/state", headers={
            "authorization": f"Bearer {saved['app_token']}"})[0], 401, "the relay refuses the app's token")

    def test_a_new_login_has_no_key_an_older_client_would_send_to_the_relay(self):
        """A client from before the split reads `token` and sends it to the relay. The new login has no `token`, so such
        a client stops on it before it sends anything, and a login that has one is from before the split."""
        one = self.world.computer("computer 1")
        saved = self.saved(one)
        self.assertNotIn("token", saved)
        self.assertEqual({"app_token", "sync_token"} <= set(saved), True)
        one.client.close()
        one.client = None
        keys.save_json(login.Paths(one.paths).login, {**saved, "token": saved["app_token"]})
        with self.assertRaises(SyncError):
            SyncClient.load(one.paths, transport=self.world.fake)

    def test_a_login_from_before_the_split_is_refused_and_its_token_never_sent(self):
        one = self.world.computer("computer 1")
        one.client.close()
        one.client = None
        saved = self.saved(one)
        old = {"token": saved["app_token"], **{k: v for k, v in saved.items() if k not in ("app_token", "sync_token")}}
        keys.save_json(login.Paths(one.paths).login, old)
        self.bearers.clear()
        with self.assertRaises(SyncError) as refused:
            SyncClient.load(one.paths, transport=self.world.fake)
        self.assertIn("Run `superhistory sync login` once to replace its token.", str(refused.exception))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), mock.patch.dict(os.environ, {HOME_ENV: one.home}):
            self.assertEqual(cli.main(["sync"]), 1)
        self.assertIn("Run `superhistory sync login` once to replace its token.", out.getvalue())
        self.assertEqual(self.bearers, [], "the old token went nowhere")

    def test_signing_in_again_replaces_both_tokens_and_keeps_the_device(self):
        (one, two), _ = self.world.group("computer 1", "computer 2")
        before = self.saved(two)
        keys.save_json(login.Paths(two.paths).login, {"token": before["app_token"], **{
            k: v for k, v in before.items() if k not in ("app_token", "sync_token")}})
        two.client.close()
        two.client = None
        login.login(two.paths, self.world.fake, "https://app.test", "https://relay.test", label="another name",
                    show=lambda uri, code: None, sleep=lambda s: None)
        after = self.saved(two)
        self.assertEqual((after["device_id"], after["label"]), (before["device_id"], before["label"]))
        self.assertTrue(after["sync_token"] and after["app_token"] not in (before["app_token"], before["sync_token"]))
        for token, path in ((before["sync_token"], "/sync/state"), (before["app_token"], "/v1/devices")):
            self.assertEqual(self.world.fake.request("GET", path, headers={"authorization": f"Bearer {token}"})[0], 401,
                             "the old tokens stop working")
        two.open()
        one.item("notes", "n1", "a made-up note")
        one.sync()
        self.assertIsNone(two.sync().stalled, "the computer keeps its place in the group")
        self.assertTrue(two.has("notes", one.key("n1")))
        self.assertEqual(len([d for d in self.world.fake.devices.values() if d["user"] == self.world.fake.user_of(
            "person@example.com")]), 2, "no second device was made")


class RenewalKeysTest(unittest.TestCase):
    def test_signing_in_again_with_keys_the_device_never_registered_is_refused(self):
        """The device's keys never change at the app, so new keys made after the old ones were lost would sign packs no
        other computer accepts."""
        world = World()
        self.addCleanup(world.close)
        (one, two), _ = world.group("computer 1", "computer 2")
        two.client.close()
        two.client = None
        paths = login.Paths(two.paths)
        before = keys.load_json(paths.login)
        os.remove(paths.device_ssh)
        with self.assertRaisesRegex(keys.Unaccepted, "on your account under other keys"):
            login.login(two.paths, world.fake, "https://app.test", "https://relay.test", show=lambda uri, code: None,
                        sleep=lambda s: None)
        self.assertEqual(keys.load_json(paths.login), before, "the old tokens were not replaced")


class KeylessRenewalTest(unittest.TestCase):
    def test_signing_in_again_on_a_device_that_lost_sync_says_what_to_do(self):
        """A device from before keys were registered lost Sync in that migration, and its keys can never be added, so
        signing in again cannot give it a sync token. It stops with what to do instead of saving a login sync refuses."""
        world = World()
        self.addCleanup(world.close)
        one = world.computer("computer 1")
        device = world.fake.devices[one.client.device_id]
        device["scopes"], device["age_recipient"], device["ssh_key"] = ["push", "read"], None, None
        one.client.close()
        one.client = None
        before = keys.load_json(login.Paths(one.paths).login)
        with self.assertRaisesRegex(keys.Unaccepted, "remove it on the website"):
            login.login(one.paths, world.fake, "https://app.test", "https://relay.test", show=lambda uri, code: None,
                        sleep=lambda s: None)
        self.assertEqual(keys.load_json(login.Paths(one.paths).login), before, "the saved login is left as it was")



class RenewalProofTest(unittest.TestCase):
    """Signing an existing computer in again hands over that computer's tokens, so the terminal must prove it holds
    the computer's private key. Its public keys, which any device of the account can read, are not enough."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        (self.one, self.two), _ = self.world.group("computer 1", "computer 2")
        genuine = self.world.fake.devices[self.two.client.device_id]
        self.copied = {"do": "enroll", "prefix": genuine["prefix"], "scopes": ["sync"],
                       "age_recipient": genuine["age_recipient"], "ssh_key": genuine["ssh_key"]}
        self.tmp = tempfile.mkdtemp(prefix="superhistory-attacker-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.other_key = os.path.join(self.tmp, "key")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", self.other_key], check=True)

    def ask(self):
        app = relay.App(self.world.fake)
        code = app.ask(self.copied)
        return app, code

    def test_a_terminal_with_only_the_public_keys_gets_no_tokens(self):
        app, code = self.ask()
        with self.assertRaises(relay.ProofRequired) as asked:
            app.run(code["device_code"], self.copied)
        device_id = asked.exception.device_id
        self.assertEqual(device_id, self.two.client.device_id, "the app names the device it would sign in again")
        wrong = {
            "another computer's key": keys.renew_proof(self.other_key, code["device_code"], device_id),
            "the code alone": keys.sign(self.other_key, keys.RENEW_NAMESPACE, code["device_code"].encode()).decode(),
            "another namespace": keys.sign(self.other_key, "git", f"{code['device_code']}\n{device_id}\n".encode()).decode(),
        }
        for name, proof in wrong.items():
            with self.subTest(name):
                with self.assertRaises(relay.RelayError) as refused:
                    app.run(code["device_code"], self.copied, {"device_id": device_id, "proof": proof})
                self.assertEqual((refused.exception.status, refused.exception.code), (403, "not_proven"))
        with self.assertRaises(relay.RelayError) as refused:
            app.run(code["device_code"], self.copied, {"device_id": self.one.client.device_id, "proof": wrong["another computer's key"]})
        self.assertEqual(refused.exception.code, "not_proven", "a proof for another device id")
        self.assertIsNotNone(self.two.sync(), "the genuine computer's tokens still work")

    def test_the_genuine_computer_still_signs_in_again(self):
        before = keys.load_json(login.Paths(self.two.paths).login)
        self.two.client.close()
        self.two.client = None
        login.login(self.two.paths, self.world.fake, "https://app.test", "https://relay.test", show=lambda uri, code: None,
                    sleep=lambda s: None)
        after = keys.load_json(login.Paths(self.two.paths).login)
        self.assertEqual(after["device_id"], before["device_id"])
        self.assertNotEqual(after["sync_token"], before["sync_token"])

    def test_with_no_ssh_keygen_signing_in_again_says_what_to_install_and_changes_nothing(self):
        before = keys.load_json(login.Paths(self.two.paths).login)
        self.two.client.close()
        self.two.client = None
        run = subprocess.run

        def no_ssh_keygen(args, *rest, **kwargs):
            if args[0] == "ssh-keygen":
                raise FileNotFoundError(args[0])
            return run(args, *rest, **kwargs)
        with mock.patch.object(keys.subprocess, "run", no_ssh_keygen):
            with self.assertRaisesRegex(keys.ToolError, "ssh-keygen is not installed. Install OpenSSH's client"):
                login.login(self.two.paths, self.world.fake, "https://app.test", "https://relay.test",
                            show=lambda uri, code: None, sleep=lambda s: None)
        self.assertEqual(keys.load_json(login.Paths(self.two.paths).login), before, "the old tokens were not replaced")


if __name__ == "__main__":
    unittest.main()
