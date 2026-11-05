"""Why a send failed, which the mailer used to know and not say.

`Mailer.send` returns a bool, and the reason behind a `False` was reaching nobody: the exception was caught,
its **class name** was logged, and its text was dropped. For an administrator whose e-mail does not work that
is the difference between "the port is wrong" and a morning of changing settings at random — a server with no
STARTTLS, a port with nothing listening, a host name with a typo in it and a rejected password all looked
exactly alike.

The faults here are real ones. A socket that refuses, a name that does not resolve and a plaintext port
offered TLS are produced rather than mocked, because the point is the text the standard library actually
produces, and a mock would only prove that the test knows what it put in.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import unittest

from desk_engine.mail import Mailer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PlainSmtp:
    """Speaks SMTP in the clear and offers no STARTTLS, which is the commonest live misconfiguration."""

    def __init__(self) -> None:
        self.port = free_port()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._talk, args=(conn,), daemon=True).start()

    def _talk(self, conn: socket.socket) -> None:
        conn.settimeout(10)
        with conn:
            try:
                conn.sendall(b"220 test.local ESMTP\r\n")
                buffer = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                    while b"\r\n" in buffer:
                        line, buffer = buffer.split(b"\r\n", 1)
                        up = line.decode("utf-8", "replace").upper()
                        if up.startswith(("EHLO", "HELO")):
                            conn.sendall(b"250-test.local\r\n250 SIZE 1000000\r\n")
                        elif up == "STARTTLS":
                            conn.sendall(b"502 5.5.1 STARTTLS not available\r\n")
                        elif up == "QUIT":
                            conn.sendall(b"221 2.0.0 Bye\r\n")
                            return
                        elif up == "DATA":
                            conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                        else:
                            conn.sendall(b"250 2.0.0 Ok\r\n")
            except OSError:
                return

    def close(self) -> None:
        self.sock.close()


class WhyASendFailed(unittest.TestCase):
    def setUp(self) -> None:
        self.smtp = PlainSmtp()
        self.addCleanup(self.smtp.close)

    def send(self, **over) -> Mailer:
        m = Mailer(**{"host": "127.0.0.1", "port": self.smtp.port, "sender": "Desk <desk@acme.test>",
                      "security": "none"} | over)
        self.assertIsNotNone(asyncio.run(m.send("someone@acme.test", "Subject", "Body")))
        return m

    def test_a_server_with_no_starttls_says_which_extension_is_missing(self):
        m = self.send(security="starttls")
        self.assertIn("STARTTLS", m.last_error)

    def test_nothing_listening_says_the_connection_was_refused(self):
        m = self.send(port=free_port())
        self.assertIn("ConnectionRefused", m.last_error)

    def test_a_name_that_does_not_resolve_says_so(self):
        m = self.send(host="smtp.not-a-real-host.invalid", port=587)
        self.assertTrue("gaierror" in m.last_error or "resolve" in m.last_error.lower(), m.last_error)

    def test_each_fault_reads_differently_from_the_others(self):
        """The whole point: three faults used to be one message, and the log carried only the class name."""
        reasons = {self.send(security="starttls").last_error,
                   self.send(port=free_port()).last_error,
                   self.send(host="smtp.not-a-real-host.invalid", port=587).last_error}
        self.assertEqual(3, len(reasons), reasons)

    def test_the_reason_carries_the_library_s_own_text_and_not_only_its_class(self):
        m = self.send(port=free_port())
        self.assertIn(":", m.last_error, "only the exception class was kept, which is the old behaviour")
        self.assertTrue(m.last_error.split(":", 1)[1].strip(), m.last_error)

    def test_a_send_that_works_clears_the_previous_reason(self):
        """Otherwise the next failure shows the last one's reason, which is worse than showing none."""
        m = self.send(port=free_port())
        self.assertTrue(m.last_error)
        m.port = self.smtp.port
        self.assertTrue(asyncio.run(m.send("someone@acme.test", "Subject", "Body")))
        self.assertEqual("", m.last_error)

    def test_an_unconfigured_mailer_still_simply_declines(self):
        m = Mailer()
        self.assertFalse(asyncio.run(m.send("someone@acme.test", "s", "b")))
        self.assertEqual("", m.last_error, "declining for want of configuration is not a failure to report")


if __name__ == "__main__":
    unittest.main()
