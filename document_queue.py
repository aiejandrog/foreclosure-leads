"""document_queue — resumable SQLite work queue for document acquisition.

WHY A QUEUE AT ALL
A Miami case is 40-120 docket entries; the pilot judgment alone is five separate page fetches. The
desktop session that drafted the collectors ran out of subscription usage MID-CASE on 2026-09-22.
Whatever holds this work has to survive that: a killed worker must resume without re-downloading
what it already has and without two workers fetching the same document twice.

THE THREE PROPERTIES, AND HOW EACH IS GOT
  resumable   every job is a row with a status; nothing lives only in a process's memory.
  exclusive   `claim()` runs inside BEGIN IMMEDIATE, so two workers cannot take the same row. A
              lease carries an expiry — a worker that dies leaves a row that ages back to pending
              rather than a row locked forever.
  deduped     (county, case, source_ref, kind) is UNIQUE, so re-enqueueing a case is free and
              idempotent; and a completed job records the content hash, so a resumed run can tell
              "already have these exact bytes" from "never fetched".

STATUSES
  pending  waiting            leased  a worker holds it until lease_expires
  done     bytes stored       gap     an AccessGap — we could not get it, and that is RECORDED
  failed   an unexpected error; retried until MAX_ATTEMPTS, then left for a human to look at

`gap` is deliberately terminal and deliberately not `failed`. A restricted or login-walled document
is a known, reportable hole in coverage (CASE-REVIEW-PROCEDURE.md), not a bug to retry forever.

WHY `done` IS NOT ALWAYS FINAL
`done` used to mean "never look at this again", and on 2026-09-22 that cost the pilot a run: the
judgment had been fetched by the reader that mistook a watermark for a page, so the job was marked
done, and the FIXED reader then skipped the one document it existed to re-read. Done is now
qualified by HOW it was done — `reader_version` and the `read_status` that reader reached — and
`claim_ref` will re-take a done job when a better reader now exists, or when the document was
never actually read and this run can OCR where the last one could not. A document nobody could
read is not finished; it is waiting for a reader that can.
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import case_review

MAX_ATTEMPTS = 3
DEFAULT_LEASE = 900          # 15 minutes: longer than any single document fetch, short enough that
                             # a killed worker's rows come back the same session.

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  county      TEXT NOT NULL,
  case_no     TEXT NOT NULL,
  source_ref  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until REAL,
  sha256      TEXT,
  reader_version INTEGER,
  read_status TEXT,
  error       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (county, case_no, source_ref, kind)
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (status, lease_until);
CREATE INDEX IF NOT EXISTS jobs_case  ON jobs (county, case_no);
"""


def default_path():
    """Under paths.DEALFLOW_DIR — the queue carries case numbers and document names, so it is PII
    and must not land in the repo or in OneDrive. output_path enforces both."""
    return str(case_review.output_path(os.path.join('documents', 'queue.db')))


def _now():
    return datetime.now(timezone.utc).isoformat()


class DocumentQueue:
    def __init__(self, path=None):
        self.path = path or default_path()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        # Columns added after the first pilot ran. A queue.db already exists on the desktop with
        # rows in it; those rows are the ones that need re-reading, so migrate rather than start
        # a new file and lose the record of what was already fetched.
        have = {row['name'] for row in self.db.execute('PRAGMA table_info(jobs)')}
        for column, decl in (('reader_version', 'INTEGER'), ('read_status', 'TEXT')):
            if column not in have:
                self.db.execute('ALTER TABLE jobs ADD COLUMN %s %s' % (column, decl))

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- enqueue -------------------------------------------------------------------------------
    def add(self, county, case, source_ref, kind, payload=None):
        """Idempotent. Returns True when a NEW job was created, False when it was already known."""
        now = _now()
        try:
            self.db.execute(
                'INSERT INTO jobs (county, case_no, source_ref, kind, payload, created_at, updated_at)'
                ' VALUES (?,?,?,?,?,?,?)',
                (county, case, source_ref, kind, json.dumps(payload or {}), now, now))
            return True
        except sqlite3.IntegrityError:
            return False

    def add_many(self, county, case, jobs):
        return sum(self.add(county, case, ref, kind, payload) for ref, kind, payload in jobs)

    # ---- claim ---------------------------------------------------------------------------------
    def claim(self, owner, lease=DEFAULT_LEASE, county=None, case=None):
        """Atomically take one ready job, or None. Ready = pending, or a lease that has expired.

        BEGIN IMMEDIATE takes the write lock BEFORE the SELECT, so the select-then-update pair is
        atomic against another worker. A plain transaction would let two workers both read the same
        pending row and both claim it — which is the duplicate download this queue exists to stop.
        """
        now = time.time()
        where, args = ["(status = 'pending' OR (status = 'leased' AND lease_until < ?))"], [now]
        if county:
            where.append('county = ?'); args.append(county)
        if case:
            where.append('case_no = ?'); args.append(case)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute(
                'SELECT * FROM jobs WHERE ' + ' AND '.join(where) + ' ORDER BY id LIMIT 1',
                args).fetchone()
            if row is None:
                self.db.execute('COMMIT')
                return None
            self.db.execute(
                'UPDATE jobs SET status = ?, lease_owner = ?, lease_until = ?, attempts = attempts + 1,'
                ' updated_at = ? WHERE id = ?',
                ('leased', owner, now + lease, _now(), row['id']))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        job = dict(row)
        job.update({'status': 'leased', 'lease_owner': owner, 'attempts': row['attempts'] + 1})
        job['payload'] = json.loads(job['payload'] or '{}')
        return job

    def claim_ref(self, owner, county, case, source_ref, kind, lease=DEFAULT_LEASE,
                  reader_version=None, can_ocr=False, force=False):
        """Claim ONE named job, or None when it is genuinely finished or held by someone else.

        A caller working a specific document needs this rather than `claim()`: taking "any ready
        job" would hand it a lease on a different document than the one in its hand.

        `reader_version` and `can_ocr` describe THIS run's reader, and they are what make a `done`
        job re-claimable:

          * the job was done by an older reader — the reader has since been fixed, so the verdict
            it reached is not one to keep. This is the pilot bug.
          * the job was done but never actually read (`image_only`, `partial`, or no status at
            all, which is what every row written before this change looks like) AND this run can
            OCR where the last one could not.

        `force=True` takes a done job regardless. The version check answers "has the code moved?",
        which only works if the version covers everything that could change; three runs in a row
        were skipped because it did not. `force` is the caller saying "read it again" and being
        obeyed.

        A document that this same reader already read to completion is left alone, so a normal
        re-run is still free. Pass neither argument and `done` stays terminal as before.
        """
        now = time.time()
        ready = ["status = 'pending'", "(status = 'leased' AND lease_until < ?)"]
        args = [county, case, source_ref, kind, now]
        if reader_version is not None:
            ready.append("(status = 'done' AND (reader_version IS NULL OR reader_version < ?))")
            args.append(reader_version)
        if can_ocr:
            ready.append("(status = 'done' AND (read_status IS NULL OR read_status != 'read'))")
        if force:
            # The caller has said it wants this document read again whatever the queue thinks.
            # A diagnostic tool re-running one document must not be argued with by a cache.
            ready.append("status = 'done'")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute(
                'SELECT * FROM jobs WHERE county = ? AND case_no = ? AND source_ref = ? AND kind = ?'
                ' AND (' + ' OR '.join(ready) + ')',
                args).fetchone()
            if row is None:
                self.db.execute('COMMIT')
                return None
            self.db.execute(
                'UPDATE jobs SET status = ?, lease_owner = ?, lease_until = ?, attempts = attempts + 1,'
                ' updated_at = ? WHERE id = ?',
                ('leased', owner, now + lease, _now(), row['id']))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        job = dict(row)
        job.update({'status': 'leased', 'lease_owner': owner, 'attempts': row['attempts'] + 1})
        job['payload'] = json.loads(job['payload'] or '{}')
        return job

    def renew(self, job_id, owner, lease=DEFAULT_LEASE):
        cur = self.db.execute(
            'UPDATE jobs SET lease_until = ?, updated_at = ? WHERE id = ? AND lease_owner = ?'
            " AND status = 'leased'", (time.time() + lease, _now(), job_id, owner))
        return cur.rowcount == 1

    # ---- finish --------------------------------------------------------------------------------
    def _finish(self, job_id, owner, status, sha=None, error=None, reader_version=None,
                read_status=None):
        # lease_owner in the WHERE clause: a worker whose lease already expired and was taken by
        # someone else must NOT be able to overwrite the new holder's result.
        cur = self.db.execute(
            'UPDATE jobs SET status = ?, sha256 = ?, error = ?, reader_version = ?,'
            ' read_status = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?'
            ' WHERE id = ? AND lease_owner = ?',
            (status, sha, error, reader_version, read_status, _now(), job_id, owner))
        return cur.rowcount == 1

    def complete(self, job_id, owner, sha256=None, reader_version=None, read_status=None):
        """Done — and WITH WHAT. A completion that does not say which reader reached which
        read_status cannot be re-examined when the reader improves, which is how the pilot's
        stamp-only read became permanent."""
        return self._finish(job_id, owner, 'done', sha=sha256, reader_version=reader_version,
                            read_status=read_status)

    def gap(self, job_id, owner, reason):
        """An AccessGap. Terminal and reportable — coverage has a hole and we can name it."""
        return self._finish(job_id, owner, 'gap', error=str(reason)[:500])

    def fail(self, job_id, owner, error):
        """Unexpected error. Back to pending while attempts remain, so a transient fault resumes."""
        row = self.db.execute('SELECT attempts FROM jobs WHERE id = ?', (job_id,)).fetchone()
        attempts = row['attempts'] if row else MAX_ATTEMPTS
        status = 'pending' if attempts < MAX_ATTEMPTS else 'failed'
        cur = self.db.execute(
            'UPDATE jobs SET status = ?, error = ?, lease_owner = NULL, lease_until = NULL,'
            ' updated_at = ? WHERE id = ? AND lease_owner = ?',
            (status, str(error)[:500], _now(), job_id, owner))
        return cur.rowcount == 1

    # ---- report --------------------------------------------------------------------------------
    def counts(self, county=None, case=None):
        where, args = [], []
        if county:
            where.append('county = ?'); args.append(county)
        if case:
            where.append('case_no = ?'); args.append(case)
        sql = 'SELECT status, COUNT(*) n FROM jobs'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        rows = self.db.execute(sql + ' GROUP BY status', args).fetchall()
        return {r['status']: r['n'] for r in rows}

    def jobs(self, county=None, case=None, status=None):
        where, args = [], []
        for col, val in (('county', county), ('case_no', case), ('status', status)):
            if val:
                where.append(col + ' = ?'); args.append(val)
        sql = 'SELECT * FROM jobs'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        out = []
        for row in self.db.execute(sql + ' ORDER BY id', args):
            item = dict(row)
            item['payload'] = json.loads(item['payload'] or '{}')
            out.append(item)
        return out

    def coverage(self, county, case):
        """What an operator may honestly say about this case's document coverage."""
        c = self.counts(county, case)
        total = sum(c.values())
        return {'county': county, 'case': case, 'jobs': total,
                'done': c.get('done', 0), 'gaps': c.get('gap', 0),
                'failed': c.get('failed', 0),
                'outstanding': c.get('pending', 0) + c.get('leased', 0),
                # Complete means every job resolved AND none of them resolved as a gap. A case with
                # a login-walled exhibit is NOT complete, however many pages we did read.
                'collection_complete': bool(total) and total == c.get('done', 0)}
