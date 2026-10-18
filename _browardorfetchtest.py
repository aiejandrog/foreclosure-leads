#!/usr/bin/env python3
"""Offline tests for the Broward Official Records SFTP fetch.

The session is fake. No network, and no real feed rows, names, or addresses.
"""
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import broward_or_fetch as F

HERE = os.path.dirname(os.path.abspath(__file__))


class Entry(object):
    def __init__(self, name, size, mode=None):
        self.filename = name
        self.st_size = size
        self.st_mode = mode


class FakeSftp(object):
    def __init__(self, entries):
        self.entries = list(entries)
        self.listed = []
        self.got = []
        self.payload = {}
        self.channel_timeout = None

    def listdir_attr(self, path):
        self.listed.append(path)
        return list(self.entries)

    def get(self, remote, local):
        self.got.append((remote, local))
        body = self.payload.get(remote, b'x' * 4)
        with open(local, 'wb') as fh:
            fh.write(body)

    def get_channel(self):
        return self

    def settimeout(self, seconds):
        self.channel_timeout = seconds

    def close(self):
        pass


class FakeKey(object):
    def __init__(self, name, raw):
        self._name = name
        self._raw = raw

    def get_name(self):
        return self._name

    def asbytes(self):
        return self._raw


def _run(**kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        stats = F.run(**kwargs)
    return stats, buf.getvalue()


class SelectTests(unittest.TestCase):
    def test_daily_index_names_and_nothing_else(self):
        yes = (
            '10-02-2026doc-ver.txt',
            '10-02-2026nme-ver.txt',
            '10-02-2026lnk-ver.txt',
            '10-02-2026lgl-ver.txt',
            '10-02-2026doc-ver-rng.txt',
            '10-02-2026DOC-VER.TXT',
        )
        no = (
            '10-02-2026img.zip',
            '10-02-2026img.ZIP',
            'cy2024doc-rec.txt',
            '10-02-2026doc-ver-rng.txt.exe',
            'not-a-file',
            '../10-02-2026doc-ver.txt',
            'Official_Records_Download/10-02-2026doc-ver.txt',
            '10-02-2026doc-ver.txt/evil',
            '',
            '10-2-2026doc-ver.txt',
        )
        for name in yes:
            self.assertTrue(F.is_daily_index(name), name)
        for name in no:
            self.assertFalse(F.is_daily_index(name), name)

    def test_remote_path_stays_inside_the_daily_folder(self):
        self.assertEqual(F.remote_join('10-02-2026doc-ver.txt'),
                         'Official_Records_Download/10-02-2026doc-ver.txt')
        with self.assertRaises(ValueError):
            F.remote_join('10-02-2026img.zip')

    def test_listing_keeps_index_files_only(self):
        sftp = FakeSftp([
            Entry('10-02-2026doc-ver.txt', 4),
            Entry('10-02-2026nme-ver.txt', 4),
            Entry('10-02-2026img.zip', 400_000_000),
            Entry('cy2024doc-rec.txt', 8),
            Entry('random books', 0),
        ])
        session = F.ParamikoSession(None, sftp)
        got = session.list_exports()
        self.assertEqual(sftp.listed, ['Official_Records_Download'])
        self.assertEqual([name for name, _size in got],
                         ['10-02-2026doc-ver.txt', '10-02-2026nme-ver.txt'])


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='broward-or-fetch-')
        self.dest = os.path.join(self.tmp, 'days')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _session(self, names_sizes, payloads=None, explode=None):
        entries = [Entry(name, size) for name, size in names_sizes]
        sftp = FakeSftp(entries)
        if payloads:
            for name, body in payloads.items():
                sftp.payload[F.remote_join(name)] = body

        class Session(object):
            def __init__(self):
                self.closed = False
                self.inner = F.ParamikoSession(None, sftp)

            def list_exports(self):
                if explode == 'list':
                    raise TimeoutError('stalled')
                return self.inner.list_exports()

            def get(self, name, local_path):
                if explode == name:
                    raise OSError('reset')
                return self.inner.get(name, local_path)

            def close(self):
                self.closed = True

        holder = {}

        def opener(password):
            holder['password'] = password
            holder['session'] = Session()
            return holder['session']

        holder['opener'] = opener
        holder['sftp'] = sftp
        return holder

    def test_flag_off_does_not_connect(self):
        def opener(_password):
            raise AssertionError('connected while off')

        for value in ('', '0', 'off', 'false', 'no'):
            stats, text = _run(env={F.feed.ENV_FLAG: value, F.ENV_PASSWORD: 'secret',
                                     F.feed.ENV_DIR: self.dest},
                               opener=opener)
            self.assertTrue(stats['ok'])
            self.assertEqual(stats['skipped'], 'flag')
            self.assertIn('off', text)
            self.assertNotIn('secret', text)

    def test_on_switch_matches_the_feed(self):
        seen = []

        def opener(password):
            seen.append(password)
            raise TimeoutError('stop')

        stats, _text = _run(env={F.feed.ENV_FLAG: 'yes', F.ENV_PASSWORD: 'pw',
                                 F.feed.ENV_DIR: self.dest},
                            opener=opener)
        self.assertEqual(seen, ['pw'])
        self.assertEqual(stats['warning'], 'TimeoutError')

    def test_missing_password_and_missing_dir_do_not_connect(self):
        def opener(_password):
            raise AssertionError('connected')

        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.feed.ENV_DIR: self.dest}, opener=opener)
        self.assertFalse(stats['ok'])
        self.assertEqual(stats['warning'], 'no-password')
        self.assertIn('BROWARD_SFTP_PASSWORD', text)
        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'secret'}, opener=opener)
        self.assertEqual(stats['warning'], 'no-dir')
        self.assertIn('DEALFLOW_BROWARD_OR_DIR', text)
        self.assertNotIn('secret', text)

    def test_refuses_the_repo(self):
        def opener(_password):
            raise AssertionError('connected')

        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'secret'},
                           directory=HERE, opener=opener)
        self.assertEqual(stats['warning'], 'inside-repo')
        self.assertIn('repo', text)
        self.assertFalse(os.path.isfile(os.path.join(HERE, '10-02-2026doc-ver.txt')))

    def test_downloads_missing_files_and_skips_ones_already_there(self):
        os.makedirs(self.dest)
        already = os.path.join(self.dest, '10-02-2026nme-ver.txt')
        with open(already, 'wb') as fh:
            fh.write(b'kept')
        holder = self._session([
            ('10-02-2026doc-ver.txt', 4),
            ('10-02-2026nme-ver.txt', 4),
            ('10-02-2026lnk-ver.txt', 3),
            ('10-02-2026img.zip', 400_000_000),
        ], payloads={'10-02-2026lnk-ver.txt': b'abc'})
        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'secret-token'},
                           directory=self.dest, opener=holder['opener'])
        self.assertEqual(holder['password'], 'secret-token')
        self.assertTrue(holder['session'].closed)
        self.assertEqual(stats['downloaded'], 2)
        self.assertEqual(stats['already'], 1)
        self.assertEqual(stats['failed'], 0)
        self.assertIn('downloaded 2, already 1, failed 0', text)
        self.assertNotIn('secret-token', text)
        self.assertEqual(open(os.path.join(self.dest, '10-02-2026doc-ver.txt'), 'rb').read(), b'xxxx')
        self.assertEqual(open(already, 'rb').read(), b'kept')
        self.assertEqual(open(os.path.join(self.dest, '10-02-2026lnk-ver.txt'), 'rb').read(), b'abc')
        self.assertFalse(os.path.exists(os.path.join(self.dest, '10-02-2026img.zip')))
        got_names = [os.path.basename(remote) for remote, _local in holder['sftp'].got]
        self.assertEqual(got_names, ['10-02-2026doc-ver.txt', '10-02-2026lnk-ver.txt'])

        holder2 = self._session([
            ('10-02-2026doc-ver.txt', 4),
            ('10-02-2026nme-ver.txt', 4),
            ('10-02-2026lnk-ver.txt', 3),
        ])
        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'secret-token'},
                           directory=self.dest, opener=holder2['opener'])
        self.assertEqual(stats['downloaded'], 0)
        self.assertEqual(stats['already'], 3)
        self.assertEqual(holder2['sftp'].got, [])
        self.assertIn('downloaded 0, already 3, failed 0', text)

    def test_size_change_is_fetched_again_and_a_short_read_is_not_kept(self):
        os.makedirs(self.dest)
        changed = os.path.join(self.dest, '10-03-2026doc-ver.txt')
        with open(changed, 'wb') as fh:
            fh.write(b'old')
        holder = self._session([
            ('10-03-2026doc-ver.txt', 4),
            ('10-03-2026nme-ver.txt', 4),
        ], payloads={'10-03-2026nme-ver.txt': b'no'})
        stats, _text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'pw'},
                            directory=self.dest, opener=holder['opener'])
        self.assertEqual(stats['downloaded'], 1)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(open(changed, 'rb').read(), b'xxxx')
        self.assertFalse(os.path.exists(os.path.join(self.dest, '10-03-2026nme-ver.txt')))
        self.assertFalse(os.path.exists(os.path.join(self.dest, '10-03-2026nme-ver.txt.partial')))

    def test_one_file_failing_does_not_drop_the_rest(self):
        holder = self._session([
            ('10-04-2026doc-ver.txt', 4),
            ('10-04-2026nme-ver.txt', 4),
        ], explode='10-04-2026doc-ver.txt')
        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'pw'},
                           directory=self.dest, opener=holder['opener'])
        self.assertEqual(stats['downloaded'], 1)
        self.assertEqual(stats['failed'], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, '10-04-2026nme-ver.txt')))
        self.assertFalse(os.path.exists(os.path.join(self.dest, '10-04-2026doc-ver.txt')))
        self.assertFalse(os.path.exists(os.path.join(self.dest, '10-04-2026doc-ver.txt.partial')))
        self.assertIn('failed 1', text)
        self.assertTrue(holder['session'].closed)

    def test_connect_errors_exit_clean_and_hide_the_password(self):
        secret = 's3cret-value'

        def opener(password):
            raise TimeoutError('failed for ' + password)

        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: secret, F.feed.ENV_DIR: self.dest},
                           opener=opener)
        self.assertFalse(stats['ok'])
        self.assertEqual(stats['warning'], 'TimeoutError')
        self.assertIn('connect failed (TimeoutError)', text)
        self.assertNotIn(secret, text)
        self.assertEqual(text.count('\n'), 1)

        def missing(_password):
            raise ImportError('paramiko')

        stats, text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: secret, F.feed.ENV_DIR: self.dest},
                           opener=missing)
        self.assertEqual(stats['warning'], 'no-paramiko')
        self.assertIn('paramiko is not installed', text)
        self.assertNotIn(secret, text)

    def test_oversized_index_is_not_saved(self):
        holder = self._session([('10-05-2026doc-ver.txt', F.MAX_BYTES + 1)])
        stats, _text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'pw'},
                            directory=self.dest, opener=holder['opener'])
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(stats['downloaded'], 0)
        self.assertEqual(holder['sftp'].got, [])
        self.assertEqual(os.listdir(self.dest), [])

    def test_zero_byte_day_is_kept_and_then_skipped(self):
        holder = self._session([('10-06-2026lnk-ver.txt', 0)],
                              payloads={'10-06-2026lnk-ver.txt': b''})
        stats, _text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'pw'},
                            directory=self.dest, opener=holder['opener'])
        self.assertEqual(stats['downloaded'], 1)
        path = os.path.join(self.dest, '10-06-2026lnk-ver.txt')
        self.assertEqual(os.path.getsize(path), 0)
        holder2 = self._session([('10-06-2026lnk-ver.txt', 0)])
        stats, _text = _run(env={F.feed.ENV_FLAG: '1', F.ENV_PASSWORD: 'pw'},
                            directory=self.dest, opener=holder2['opener'])
        self.assertEqual(stats['already'], 1)
        self.assertEqual(holder2['sftp'].got, [])


class ConnectTests(unittest.TestCase):
    def test_host_key_pin_rejects_a_different_key(self):
        self.assertFalse(F.host_key_matches(FakeKey('ssh-rsa', b'not-the-server')))
        self.assertFalse(F.host_key_matches(FakeKey('ssh-ed25519', b'not-the-server')))
        self.assertFalse(F.host_key_matches(object()))

    def test_connect_uses_the_public_user_timeouts_and_pin(self):
        seen = {}

        class Channel(object):
            def __init__(self):
                self.timeout = None

            def settimeout(self, seconds):
                self.timeout = seconds

        class Sftp(object):
            def __init__(self):
                self.channel = Channel()

            def get_channel(self):
                return self.channel

            def close(self):
                pass

        class Keys(object):
            def __init__(self):
                self.added = []

            def add(self, hostname, keytype, key):
                self.added.append((hostname, keytype, key))

        class Client(object):
            def __init__(self):
                self.policy = None
                self.kwargs = None
                self.keys = Keys()
                self.sftp = Sftp()

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

            def connect(self, **kwargs):
                self.kwargs = kwargs

            def open_sftp(self):
                return self.sftp

            def get_host_keys(self):
                return self.keys

            def close(self):
                pass

        client = Client()

        class Impl(object):
            SSHException = type('SSHException', (Exception,), {})
            MissingHostKeyPolicy = object

        Impl.SSHClient = staticmethod(lambda: client)
        session = F.open_sftp('from-env', impl=Impl)
        self.assertIsInstance(session, F.ParamikoSession)
        kwargs = client.kwargs
        self.assertEqual(kwargs['hostname'], 'BCFTP.Broward.org')
        self.assertEqual(kwargs['port'], 22)
        self.assertEqual(kwargs['username'], 'crpublic')
        self.assertEqual(kwargs['password'], 'from-env')
        self.assertEqual(kwargs['timeout'], F.CONNECT_TIMEOUT)
        self.assertEqual(kwargs['banner_timeout'], F.CONNECT_TIMEOUT)
        self.assertEqual(kwargs['auth_timeout'], F.CONNECT_TIMEOUT)
        self.assertFalse(kwargs['look_for_keys'])
        self.assertFalse(kwargs['allow_agent'])
        self.assertEqual(client.sftp.channel.timeout, F.CHANNEL_TIMEOUT)
        with self.assertRaises(Impl.SSHException):
            client.policy.missing_host_key(client, 'BCFTP.Broward.org', FakeKey('ssh-rsa', b'nope'))
        self.assertEqual(client.keys.added, [])
        good = FakeKey('ssh-rsa', b'ignored')
        # A matching key is accepted. Build one whose digest is the pin by
        # stubbing host_key_matches for this call only.
        orig = F.host_key_matches
        F.host_key_matches = lambda key: key is good
        try:
            client.policy.missing_host_key(client, 'BCFTP.Broward.org', good)
        finally:
            F.host_key_matches = orig
        self.assertEqual(client.keys.added, [('BCFTP.Broward.org', 'ssh-rsa', good)])
        session.close()


class WireTests(unittest.TestCase):
    def test_password_is_not_in_the_source(self):
        src = open(os.path.join(HERE, 'broward_or_fetch.py'), encoding='utf-8').read()
        self.assertIn("USER = 'crpublic'", src)
        self.assertIn('BROWARD_SFTP_PASSWORD', src)
        for line in src.splitlines():
            if 'crpublic' in line.lower():
                self.assertNotIn('password', line.lower(), line)
        for word in ('stay_gate', 'optout', 'equity_state', 'publish_guard', 'make_tracker('):
            self.assertNotIn(word, src)

    def test_requirements_install_paramiko(self):
        req = open(os.path.join(HERE, 'requirements.txt'), encoding='utf-8').read()
        self.assertIn('paramiko', req)

    def test_refresh_runs_fetch_then_feed_after_the_scrape(self):
        bat = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8').read()
        i_scrape = bat.index('python -u stub_resolve.py')
        i_fetch = bat.index('python -u broward_or_fetch.py')
        i_feed = bat.index('python -u broward_or_feed.py')
        i_next = bat.index('python -u gen_cases_qs.py')
        self.assertLess(i_scrape, i_fetch)
        self.assertLess(i_fetch, i_feed)
        self.assertLess(i_feed, i_next)
        window = bat[i_fetch:i_next]
        self.assertEqual(window.count('if errorlevel 1'), 2)
        self.assertIn('DEALFLOW_BROWARD_OR_FEED=1', bat)

    def test_main_returns_zero_when_the_server_times_out(self):
        dest = tempfile.mkdtemp(prefix='broward-or-main-')
        orig = F.open_sftp
        old_pass = os.environ.get(F.ENV_PASSWORD)
        try:
            def boom(_password, impl=None):
                raise TimeoutError('down')

            F.open_sftp = boom
            os.environ[F.ENV_PASSWORD] = 'not-for-the-log'
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = F.main(['--enable', '--dir', dest])
            self.assertEqual(rc, 0)
            self.assertIn('connect failed (TimeoutError)', buf.getvalue())
            self.assertNotIn('not-for-the-log', buf.getvalue())
        finally:
            F.open_sftp = orig
            if old_pass is None:
                os.environ.pop(F.ENV_PASSWORD, None)
            else:
                os.environ[F.ENV_PASSWORD] = old_pass
            shutil.rmtree(dest, ignore_errors=True)

    def test_module_is_not_gitignored(self):
        import subprocess
        rc = subprocess.run(['git', 'check-ignore', '-q', '_browardorfetchtest.py'], cwd=HERE).returncode
        self.assertNotEqual(rc, 0)


if __name__ == '__main__':
    unittest.main()
