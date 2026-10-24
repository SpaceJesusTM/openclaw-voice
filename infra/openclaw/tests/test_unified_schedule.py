"""Calendar-backed planner lifecycle and failure boundaries (no live providers)."""
import json
import subprocess
from unittest.mock import patch
import unittest
import test_planner as legacy
from test_planner import planner, event


class UnifiedScheduleTests(unittest.TestCase):
    setUp = legacy.PlannerTests.setUp
    day_path = legacy.PlannerTests.day_path
    # Reuse isolation and CLI assertions, without inheriting the legacy tests.
    def run_cli(self, *arguments):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['day-planner', *arguments]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                planner.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def calendar_read(self, command, **kwargs):
        if command[2] == 'list':
            return subprocess.CompletedProcess(command, 0, json.dumps({'events': self.events, 'complete': True}), '')
        if command[2] == 'get':
            row = next((e for e in self.events if e['id'] == command[4]), {'absent': True})
            return subprocess.CompletedProcess(command, 0, json.dumps({'event': row}), '')
        self.assertEqual(command[2], 'manage')
        action = command[3]
        if action == 'create':
            row = event('new-event', start=command[command.index('--from') + 1], end=command[command.index('--to') + 1])
            row['summary'] = command[command.index('--summary') + 1]
            self.events.append(row)
        else:
            row = next(e for e in self.events if e['id'] == command[5])
            if action == 'delete':
                self.events.remove(row)
            for flag, key in (('--summary', 'summary'), ('--from', 'start'), ('--to', 'end')):
                if flag in command:
                    value = command[command.index(flag) + 1]
                    row[key] = value if key == 'summary' else {'dateTime': value}
        return subprocess.CompletedProcess(command, 0, json.dumps({'data': {'event': row}}), '')

    def test_unified_lifecycle_same_identity_no_local_mirror(self):
        with patch.object(planner, 'TZ_NAME', 'America/Toronto'):
            for args in [('add', '2026-10-25', '08:00-09:00', 'Breakfast test'),
                         ('move', '2026-10-25', 'Breakfast test', '10:00'),
                         ('resize', '2026-10-25', 'new-event', '2h'),
                         ('rename', '2026-10-25', 'new-event', 'Renamed test')]:
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)['event_id'], 'new-event')
                self.assertEqual(len(self.events), 1)
                if args[0] == 'add':
                    from datetime import datetime, timezone
                    instant = datetime.fromisoformat(self.events[0]['start']['dateTime'])
                    self.assertEqual(instant.astimezone(timezone.utc).hour, 12)
            self.assertEqual(self.events[0]['start']['dateTime'], '2026-10-25T10:00:00-04:00')
            self.assertEqual(self.events[0]['end']['dateTime'], '2026-10-25T12:00:00-04:00')
            self.assertFalse((__import__('pathlib').Path(planner.ROOT) / 'days').exists())
            self.assertEqual(self.run_cli('remove', '2026-10-25', 'new-event')[0], 0)
            self.assertEqual(self.events, [])

    def test_cross_day_move_retains_calendar_identity(self):
        self.run_cli('add', 'tomorrow', '08:00-09:00', 'Move me')
        code, out, err = self.run_cli('move', 'tomorrow', 'new-event', '--to-day', '2026-10-22')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['event_id'], 'new-event')
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0]['start']['dateTime'].startswith('2026-10-22'))

    def test_existing_event_duplicate_and_shared_context(self):
        self.events = [dict(event(), summary='Meeting'), event('Shared context', calendar='other')]
        code, out, err = self.run_cli('add', 'tomorrow', '10:00-11:00', 'Meeting')
        self.assertEqual(code, 0, err)
        self.assertIn('Already planned', out)
        self.assertFalse(any(c.args[0][2] == 'manage' for c in self.mock_run.call_args_list))
        code, out, _ = self.run_cli('show', 'tomorrow', '--json')
        rows = json.loads(out)['schedule']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['calendar'], 'other')
        self.assertFalse(rows[1]['counted'])

    def test_malformed_legacy_data_blocks_calendar_write(self):
        self.day_path().parent.mkdir(parents=True)
        self.day_path().write_text("# Day Plan — 2026-10-21\n\nunknown owner metadata\n")
        code, _, _ = self.run_cli('add', 'tomorrow', '13:00-14:00', 'Test')
        self.assertEqual(code, 5)
        self.assertFalse(any(c.args[0][2] == 'manage' for c in self.mock_run.call_args_list))

    def test_failed_or_uncertain_write_never_saves_local_or_retries(self):
        read = self.calendar_read
        def fail(command, **kwargs):
            if command[2] == 'manage':
                raise subprocess.TimeoutExpired(command, 90)
            return read(command, **kwargs)
        self.mock_run.side_effect = fail
        code, _, err = self.run_cli('add', 'tomorrow', '13:00-14:00', 'Test')
        self.assertEqual(code, 3)
        self.assertIn('outcome may be unknown', err)
        self.assertEqual(sum(c.args[0][2] == 'manage' for c in self.mock_run.call_args_list), 1)
        self.assertFalse(self.day_path().exists())

    def test_readback_mismatch_does_not_retry_or_mirror(self):
        read = self.calendar_read
        def wrong(command, **kwargs):
            result = read(command, **kwargs)
            if command[2] == 'get':
                data = json.loads(result.stdout)
                data['event']['summary'] = 'Different title'
                return subprocess.CompletedProcess(command, 0, json.dumps(data), '')
            return result
        self.mock_run.side_effect = wrong
        code, _, err = self.run_cli('add', 'tomorrow', '13:00-14:00', 'Test')
        self.assertEqual(code, 3)
        self.assertIn('did not match', err)
        self.assertEqual(len(self.events), 1)  # write succeeded, read-back did not
        self.assertEqual(sum(c.args[0][2] == 'manage' for c in self.mock_run.call_args_list), 1)
        self.assertFalse(self.day_path().exists())

    def test_legacy_breakfast_preserved_and_ambiguous_reference_refused(self):
        self.run_cli('add', 'tomorrow', '08:00-09:00', 'Breakfast', '--local-only')
        before = self.day_path().read_bytes()
        self.events = [dict(event(start='2026-10-21T08:00:00Z', end='2026-10-21T09:00:00Z'), summary='Breakfast')]
        self.assertEqual(self.run_cli('move', 'tomorrow', 'Breakfast', '12:00')[0], 2)
        self.run_cli('show', 'tomorrow', '--json')
        self.assertEqual(self.day_path().read_bytes(), before)

    def test_recurring_and_dst_ambiguity_refused(self):
        self.events = [dict(event(), recurrence=['RRULE:FREQ=WEEKLY'])]
        self.assertEqual(self.run_cli('rename', 'tomorrow', 'meeting', 'New')[0], 2)
        with patch.object(planner, 'TZ_NAME', 'America/Toronto'):
            self.assertEqual(self.run_cli('add', '2026-11-01', '01:00-02:00', 'Ambiguous')[0], 2)
            self.assertEqual(self.run_cli('add', '2026-03-08', '02:00-03:00', 'Gap')[0], 2)
            code, _, err = self.run_cli('add', '2026-11-02', '08:00-09:00', 'EST test')
            self.assertEqual(code, 0, err)
            self.assertEqual(self.events[-1]['start']['dateTime'], '2026-11-02T08:00:00-05:00')
