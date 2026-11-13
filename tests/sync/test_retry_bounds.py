"""Every wait and retry the relay's answers drive ends, and says why: a pairing slot nobody answers, a roster change or a
rotation the relay keeps refusing, an upload it keeps sending back, a pack that still changes once signed again, and a
removal that never finishes. The app's paged answers climb or are refused, and no answer is read past its size limit."""

import errno
import itertools
import json
import socket
import threading
import time
import unittest
import uuid
from unittest import mock

from kb.sync import relay, state
from kb.sync.client import MAX_REMOVAL_ROUNDS, MAX_RESIGNS, SyncError

from .support import World


class RetryBoundsTest(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def ticking(self):
        """A clock that moves a second on every reading, so a wait ends after as many readings as its seconds."""
        clock = itertools.count()
        return mock.patch("kb.sync.client.time.monotonic", side_effect=lambda: float(next(clock)))

    def empty_slot(self, client, answers, cap=100):
        """The relay answers every slot read with nothing, recording each read, and fails the test past `cap` reads."""
        def slot(slot_id):
            answers.append(slot_id)
            if len(answers) > cap:
                raise AssertionError("the wait kept asking the relay")
            return (None, None)
        return mock.patch.object(client.relay, "slot", side_effect=slot)

    def test_waiting_for_the_joining_computer_stops_at_the_time_limit(self):
        (a,), _ = self.world.group("computer 1")
        pairing = a.client.add_device()
        answers, sleeps = [], []
        with self.empty_slot(a.client, answers), self.ticking():
            with self.assertRaisesRegex(SyncError, "did not answer within the time limit"):
                a.client.approve(pairing, lambda label, words: True, wait=10, sleep=sleeps.append)
        self.assertLessEqual(len(answers), 12)
        with a.client._exclusive(0):
            pass  # the store is free again

    def test_waiting_for_the_welcome_stops_at_the_time_limit(self):
        (a,), _ = self.world.group("computer 1")
        b = self.world.computer("computer 2")
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        answers = []
        with self.empty_slot(b.client, answers), self.ticking():
            with self.assertRaisesRegex(SyncError, "did not answer within the time limit"):
                b.client.finish_join(wait=10, sleep=lambda s: None)
        self.assertLessEqual(len(answers), 12)
        self.assertIsNone(b.client.roster)

    def test_adding_a_computer_stops_after_three_refused_roster_changes(self):
        (a,), _ = self.world.group("computer 1")
        b = self.world.computer("computer 2")
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        tries = []

        def refuse(*args):
            tries.append(args[0])
            if len(tries) > 10:
                raise AssertionError("approve kept trying")
            raise relay.RelayError(409, "version_changed")
        with mock.patch.object(a.client.relay, "put_roster", side_effect=refuse):
            with self.assertRaisesRegex(SyncError, "The group kept changing while adding the computer"):
                a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.assertEqual(len(tries), 3)
        self.assertEqual(self.world.fake.group("person@example.com").roster_version, 1, "nothing was added")

    def test_a_rotation_stops_after_three_refused_tries(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        epoch = a.client.keyring.newest
        tries = []

        def refuse(*args):
            tries.append(args[0])
            if len(tries) > 10:
                raise AssertionError("the rotation kept trying")
            raise relay.RelayError(409, "epoch_changed")
        with mock.patch.object(a.client.relay, "rotate", side_effect=refuse):
            with self.assertRaisesRegex(SyncError, "Another computer kept changing the group"):
                a.client.new_recovery_code()
        self.assertEqual(len(tries), 3)
        self.assertEqual(a.client.keyring.newest, epoch, "no new key was trusted")

    def test_an_upload_the_relay_keeps_sending_back_stops_after_three_retries(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "n1", "a note")
        tries = []

        def refuse(*args, **kwargs):
            tries.append(args[0])
            if len(tries) > 10:
                raise AssertionError("the upload kept trying")
            raise relay.RelayError(409, "stale_epoch")
        with mock.patch.object(a.client.relay, "put_pack", side_effect=refuse):
            with self.assertRaises(relay.RelayError) as caught:
                a.client.push()
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "stale_epoch"))
        self.assertEqual(len(tries), 4, "the first upload and three retries")
        self.assertIsNotNone(state.pending(a.db), "the pack waits for the next push")

    def test_a_pack_that_still_changes_once_signed_again_is_not_sent(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        a.item("notes", "n1", "a note")
        before = len(self.world.fake.group("person@example.com").packs)
        signed = []
        real_sign = a.client._sign

        def sign(*args):
            signed.append(args[0])
            if len(signed) > 10:
                raise AssertionError("the pack kept being signed again")
            return real_sign(*args)
        with mock.patch.object(a.client, "_masked_when_signed", return_value=False), \
                mock.patch.object(a.client, "_sign", side_effect=sign):
            with self.assertRaisesRegex(SyncError, "still changed after it was signed again"):
                a.client.push()
        self.assertEqual(len(signed), 1 + MAX_RESIGNS, "signed once, then again at most MAX_RESIGNS times")
        self.assertEqual(len(self.world.fake.group("person@example.com").packs), before, "nothing was sent")

    def test_finishing_removals_stops_after_its_rounds(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        rotations = []
        with mock.patch.object(a.client, "_revoked_members", return_value={b.client.device_id}), \
                mock.patch.object(a.client, "_rotate", side_effect=lambda due: rotations.append(due)):
            with self.assertRaisesRegex(SyncError, "A removed computer still holds the current key"):
                a.client.push()
        self.assertEqual(len(rotations), MAX_REMOVAL_ROUNDS)


class Pages:
    """A server that sends `bodies` in turn, one per request."""
    def __init__(self, *bodies):
        self.bodies = list(bodies)

    def request(self, method, path, data, headers):
        return 200, json.dumps(self.bodies.pop(0)).encode()


class AnswerBoundsTest(unittest.TestCase):
    def test_the_apps_confirmed_packs_climb_page_by_page_or_are_refused(self):
        page = {"points": [{"dseq": d, "hash": "b" * 64} for d in range(1, relay.CHAIN_PAGE + 1)]}
        with self.assertRaises(relay.RelayError) as caught:
            relay.App(Pages(page, page)).chain_points("t" * 32, str(uuid.uuid4()), 0, 5000)
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "bad_answer"), "a page sent again")
        last = {"points": [{"dseq": relay.CHAIN_PAGE + 1, "hash": "c" * 64}]}
        got = relay.App(Pages(page, last)).chain_points("t" * 32, str(uuid.uuid4()), 0, 5000)
        self.assertEqual(len(got), relay.CHAIN_PAGE + 1, "a short page ends the paging")

    def test_an_answer_past_the_size_limit_is_refused_before_it_is_read_whole(self):
        asked = []

        class Body:
            def read(self, n):
                asked.append(n)
                return b"x" * n
        with self.assertRaises(ValueError):
            relay._read(Body())
        self.assertEqual(asked, [relay.MAX_ANSWER + 1])


class Dripping:
    """A local server that answers one request with `head`, then sends `drip` every 20 ms for up to 8 seconds, or until
    the client goes."""

    def __init__(self, head, drip=b""):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, args=(head, drip), daemon=True)
        self.thread.start()

    def serve(self, head, drip):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            data, end = b"", time.monotonic() + 8
            try:
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                conn.sendall(head)
                while drip and time.monotonic() < end and not self.stop.wait(0.02):
                    conn.sendall(drip)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.sock.close()
        self.thread.join(10)


class RequestDeadlineTest(unittest.TestCase):
    """A server that keeps sending, a byte or a line at a time, never holds a request past its deadline."""

    def held(self, head, drip):
        server = Dripping(head, drip)
        self.addCleanup(server.close)
        start = time.monotonic()
        with self.assertRaises(relay.TransportError):
            relay.HttpTransport(server.url, timeout=5, deadline=1).request("GET", "/sync/state")
        return time.monotonic() - start

    def test_chunked_trailer_lines_without_end(self):
        took = self.held(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n", b"X-More: 1\r\n")
        self.assertLess(took, 4)

    def test_a_header_line_a_byte_at_a_time(self):
        self.assertLess(self.held(b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a"), 4)

    def test_a_body_a_byte_at_a_time(self):
        self.assertLess(self.held(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n", b"a"), 4)

    def test_an_address_lookup_that_never_returns(self):
        def lookup(*args, **kwargs):
            time.sleep(5)
            return []
        start = time.monotonic()
        with mock.patch("socket.getaddrinfo", side_effect=lookup), self.assertRaises(relay.TransportError):
            relay.HttpTransport("http://relay.localhost", timeout=5, deadline=1).request("GET", "/sync/state")
        self.assertLess(time.monotonic() - start, 2)

    def test_seven_addresses_that_never_answer(self):
        # Each try to connect waits out its socket timeout, as an address that drops every packet makes it. Seven of
        # them would take 3.5 s; the request ends at its 1 s deadline.
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))] * 7
        tries = []

        def dropped(sock, where):
            tries.append(sock.gettimeout())
            time.sleep(sock.gettimeout())
            raise TimeoutError("timed out")
        start = time.monotonic()
        with mock.patch("socket.getaddrinfo", return_value=address), mock.patch.object(socket.socket, "connect", dropped), \
                self.assertRaises(relay.TransportError):
            relay.HttpTransport("http://relay.localhost", timeout=0.5, deadline=1).request("GET", "/sync/state")
        self.assertLess(time.monotonic() - start, 1.6)
        self.assertLess(len(tries), 7)

    def test_an_address_this_computer_cannot_use_moves_on_to_the_next(self):
        # An IPv6 address first, on a computer without IPv6, then an IPv4 one that answers.
        server = Dripping(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
        self.addCleanup(server.close)
        port = int(server.url.rsplit(":", 1)[1])
        addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        real = socket.socket

        def make(family=-1, kind=-1, proto=-1, fileno=None):
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, "Address family not supported by protocol")
            return real(family, kind, proto, fileno)
        with mock.patch("socket.getaddrinfo", return_value=addresses), mock.patch("socket.socket", side_effect=make):
            answer = relay.HttpTransport(f"http://relay.localhost:{port}", timeout=5, deadline=5).request("GET", "/sync/state")
        self.assertEqual(answer, (200, b"{}"))

    def test_an_answer_in_time_is_read_as_it_is(self):
        server = Dripping(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
        self.addCleanup(server.close)
        before = threading.active_count()
        self.assertEqual(relay.HttpTransport(server.url, timeout=5, deadline=1).request("GET", "/sync/state"), (200, b"{}"))
        time.sleep(1.5)  # past the deadline: the timer was stopped, so it shuts nothing down
        self.assertLessEqual(threading.active_count(), before)


if __name__ == "__main__":
    unittest.main()
