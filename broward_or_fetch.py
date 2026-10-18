#!/usr/bin/env python3
"""broward_or_fetch.py — download Broward's free Official Records daily index.

The recorder posts one quality-assured set of files per released day on SFTP.
This copies the index text files that broward_or_feed.py reads. It does not
parse them, and it does not change who can be called, mailed, or texted.

Checked 2026-10-18 against BCFTP.Broward.org port 22:

  * Login user is the public account ``crpublic``.
    The password is read from BROWARD_SFTP_PASSWORD. The county prints it on
    the FTP access page. This file does not contain it.
  * The home directory has three folders. The daily feed is
    ``Official_Records_Download``, flat, no date subfolders. ``OR_Yearly_Exports``
    and ``random books`` are not this feed and are not fetched.
  * Each day is six names: ``MM-DD-YYYYdoc-ver.txt``, ``nme-ver.txt``,
    ``lnk-ver.txt``, ``lgl-ver.txt``, ``doc-ver-rng.txt``, and ``img.zip``.
    About ten days are kept. The image zip is the page scans (hundreds of MB
    a day). This fetch saves the five text files and leaves the zip.

Files land in DEALFLOW_BROWARD_OR_DIR, outside this repo. A name that is
already there with the same size is not downloaded again. Off unless
DEALFLOW_BROWARD_OR_FEED is on (the same switch as broward_or_feed.py).

A dead server, a missing password, or a missing paramiko install prints one
warning and exits 0, so the nightly refresh keeps going.

    python broward_or_fetch.py            # no-op unless DEALFLOW_BROWARD_OR_FEED=1
    python broward_or_fetch.py --enable --dir /path/outside/the/repo
"""
import argparse
import base64
import hashlib
import os
import sys

import broward_or_feed as feed

ENV_PASSWORD = 'BROWARD_SFTP_PASSWORD'
HOST = 'BCFTP.Broward.org'
PORT = 22
USER = 'crpublic'
REMOTE_DIR = 'Official_Records_Download'
# ssh-rsa SHA256, observed 2026-10-18. A different key is a failed connect,
# not a download.
HOST_KEY_TYPE = 'ssh-rsa'
HOST_KEY_SHA256 = '4wdRNzVLM0McmLwKxo+3W3HIpb8Y+PsUiyfFYC/QI5U'
CONNECT_TIMEOUT = 15
CHANNEL_TIMEOUT = 30
# The largest index file on that check was under 300 KB. The image zip is
# hundreds of MB and must not ride along if a name is ever mislabeled.
MAX_BYTES = 32 * 1024 * 1024
_KINDS = ('doc-ver-rng', 'doc-ver', 'nme-ver', 'lnk-ver', 'lgl-ver')


def host_key_matches(key):
    """True when ``key`` is the pinned Broward host key."""
    try:
        name = key.get_name()
        raw = key.asbytes()
    except Exception:
        return False
    if name != HOST_KEY_TYPE or not raw:
        return False
    digest = hashlib.sha256(raw).digest()
    got = base64.b64encode(digest).decode('ascii').rstrip('=')
    return got == HOST_KEY_SHA256


def is_daily_index(name):
    """A daily index text file. Image zips and yearly exports are not."""
    base = str(name or '')
    if not base or base != os.path.basename(base) or '/' in base or '\\' in base:
        return False
    lower = base.lower()
    if not lower.endswith('.txt'):
        return False
    stem = lower[:-4]
    if len(stem) < 11 or stem[2] != '-' or stem[5] != '-':
        return False
    date, kind = stem[:10], stem[10:]
    parts = date.split('-')
    if len(parts) != 3:
        return False
    month, day, year = parts
    if not (len(month) == 2 and len(day) == 2 and len(year) == 4):
        return False
    if not (month.isdigit() and day.isdigit() and year.isdigit()):
        return False
    return kind in _KINDS


def remote_join(name):
    if not is_daily_index(name):
        raise ValueError('not a daily index file')
    return REMOTE_DIR + '/' + name


def local_for(dest, name):
    if not is_daily_index(name):
        raise ValueError('not a daily index file')
    root = os.path.abspath(dest)
    path = os.path.abspath(os.path.join(root, name))
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('not a daily index file')
    return path


def _present(path, size):
    if not os.path.isfile(path):
        return False
    if size is None:
        return True
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


def _import_paramiko():
    import paramiko
    return paramiko


class ParamikoSession(object):
    """The one folder this job reads, and get() for one index file."""

    def __init__(self, client, sftp):
        self.client = client
        self.sftp = sftp

    def list_exports(self):
        found = []
        for entry in self.sftp.listdir_attr(REMOTE_DIR):
            name = getattr(entry, 'filename', '') or ''
            if not is_daily_index(name):
                continue
            size = getattr(entry, 'st_size', None)
            try:
                size = int(size)
            except (TypeError, ValueError):
                size = None
            if size is not None and size < 0:
                size = None
            found.append((name, size))
        found.sort(key=lambda item: item[0])
        return found

    def get(self, name, local_path):
        self.sftp.get(remote_join(name), local_path)

    def close(self):
        for obj in (self.sftp, self.client):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass


def open_sftp(password, impl=None):
    """Connect and return a session. Raises on timeout, auth, or a bad host key."""
    impl = impl or _import_paramiko()

    class _Pinned(impl.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            if not host_key_matches(key):
                raise impl.SSHException('Broward SFTP host key does not match')
            client.get_host_keys().add(hostname, key.get_name(), key)

    client = impl.SSHClient()
    client.set_missing_host_key_policy(_Pinned())
    client.connect(
        hostname=HOST,
        port=PORT,
        username=USER,
        password=password,
        timeout=CONNECT_TIMEOUT,
        banner_timeout=CONNECT_TIMEOUT,
        auth_timeout=CONNECT_TIMEOUT,
        look_for_keys=False,
        allow_agent=False,
    )
    sftp = client.open_sftp()
    channel = sftp.get_channel()
    if channel is not None:
        channel.settimeout(CHANNEL_TIMEOUT)
    return ParamikoSession(client, sftp)


def transfer(session, files, dest):
    """Download index files that are not already in ``dest`` at the remote size."""
    os.makedirs(dest, exist_ok=True)
    downloaded = already = failed = 0
    for name, size in files:
        if not is_daily_index(name) or (size is not None and size > MAX_BYTES):
            failed += 1
            continue
        path = local_for(dest, name)
        if _present(path, size):
            already += 1
            continue
        partial = path + '.partial'
        try:
            session.get(name, partial)
            got = os.path.getsize(partial)
            if size is not None and got != size:
                raise IOError('short read')
            os.replace(partial, path)
            downloaded += 1
        except Exception:
            failed += 1
            try:
                os.remove(partial)
            except OSError:
                pass
    return {'downloaded': downloaded, 'already': already, 'failed': failed}


def _warn(reason):
    print('BROWARD OR FETCH: warning: %s' % reason)


def _summary(stats):
    print('BROWARD OR FETCH: downloaded %d, already %d, failed %d'
          % (stats['downloaded'], stats['already'], stats['failed']))


def run(env=None, directory=None, opener=None, force=False):
    """Fetch, or no-op. Returns a small counts dict. Never raises for the network."""
    env = os.environ if env is None else env
    if not (force or feed.enabled(env)):
        print('BROWARD OR FETCH: off (set DEALFLOW_BROWARD_OR_FEED=1)')
        return {'ok': True, 'skipped': 'flag', 'downloaded': 0, 'already': 0, 'failed': 0}
    password = (env.get(ENV_PASSWORD) or '').strip()
    if not password:
        _warn('BROWARD_SFTP_PASSWORD is not set')
        return {'ok': False, 'warning': 'no-password', 'downloaded': 0, 'already': 0, 'failed': 0}
    directory = (directory or (env.get(feed.ENV_DIR) or '')).strip()
    if not directory:
        _warn('DEALFLOW_BROWARD_OR_DIR is not set')
        return {'ok': False, 'warning': 'no-dir', 'downloaded': 0, 'already': 0, 'failed': 0}
    try:
        feed._assert_outside_repo(directory)
    except ValueError:
        _warn('refusing a folder inside the repo')
        return {'ok': False, 'warning': 'inside-repo', 'downloaded': 0, 'already': 0, 'failed': 0}
    opener = opener or open_sftp
    session = None
    try:
        session = opener(password)
        files = session.list_exports()
        stats = transfer(session, files, directory)
    except ImportError:
        _warn('paramiko is not installed')
        return {'ok': False, 'warning': 'no-paramiko', 'downloaded': 0, 'already': 0, 'failed': 0}
    except Exception as exc:
        where = 'connect' if session is None else 'fetch'
        _warn('%s failed (%s)' % (where, type(exc).__name__))
        return {'ok': False, 'warning': type(exc).__name__, 'downloaded': 0, 'already': 0, 'failed': 0}
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
    stats['ok'] = stats['failed'] == 0
    _summary(stats)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description='Download Broward Official Records daily index files.')
    ap.add_argument('--dir', default='', help='folder to save into (default: DEALFLOW_BROWARD_OR_DIR)')
    ap.add_argument('--enable', action='store_true',
                    help='run even without DEALFLOW_BROWARD_OR_FEED=1')
    args = ap.parse_args(argv)
    try:
        run(directory=args.dir or None, force=args.enable)
    except Exception:
        _warn('unexpected error')
    return 0


if __name__ == '__main__':
    sys.exit(main())
