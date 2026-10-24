"""Google read and native Calendar-management tests. All provider results are fictional mocks."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "workspace/bin/tari-google"
loader = importlib.machinery.SourceFileLoader("clawvoice_google", str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
google = importlib.util.module_from_spec(spec)
loader.exec_module(google)


class GoogleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "demo@example.test", "TARI_TIMEZONE": "UTC"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, args, result):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(google, "run_gog", return_value=result) as run, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = google.main(args)
        return status, out.getvalue(), err.getvalue(), run

    def test_gmail_writes_and_old_proposal_interface_unavailable(self):
        for args in (("calendar", "propose", "create", "primary"), ("calendar", "apply", "token"),
                     ("gmail", "send"), ("gmail", "read", "--force")):
            with self.subTest(args=args), patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "run_gog") as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    google.main(args)
                run.assert_not_called()

    def test_explicit_account_required_no_inherited_fallback(self):
        with patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "", "GOG_ACCOUNT": "other@example.test"}):
            with self.assertRaises(google.GoogleError):
                google.account()

    def test_fixed_read_profile_and_runtime_guards(self):
        inherited = {"TARI_GOOGLE_READ_CLIENT": "write-profile", "GOG_ACCESS_TOKEN": "private",
                     "GOG_API_BASE_URL": "https://invalid.test", "GOG_RESULTS_ONLY": "1",
                     "GOG_DRY_RUN": "1", "GOG_AUTH_MODE": "adc", "GOG_HOME": "/isolated/google",
                     "GOG_KEYRING_BACKEND": "file", "GOG_KEYRING_PASSWORD": "private"}
        result = subprocess.CompletedProcess([], 0, '{"messages":[]}', "")
        with patch.dict(os.environ, inherited), patch.object(google.subprocess, "run", return_value=result) as run:
            google.run_gog(["gmail", "messages", "search", "example"], "gmail.messages.search")
        args, kwargs = run.call_args
        cmd = args[0]
        self.assertEqual(cmd[cmd.index("--client") + 1], "tari-read")
        self.assertIn("--readonly", cmd)
        self.assertIn("--gmail-no-send", cmd)
        self.assertEqual(cmd[cmd.index("--enable-commands-exact") + 1], "gmail.messages.search")
        self.assertNotIn("--force", cmd)
        for key in ("GOG_ACCESS_TOKEN", "GOG_API_BASE_URL", "GOG_RESULTS_ONLY", "GOG_DRY_RUN", "GOG_AUTH_MODE"):
            self.assertNotIn(key, kwargs["env"])
        self.assertEqual(kwargs["env"]["GOG_HOME"], "/isolated/google")
        self.assertEqual(kwargs["timeout"], 60)

    def test_read_interface_cannot_invoke_calendar_delete(self):
        with patch.object(google.subprocess, "run") as run, self.assertRaises(google.GoogleError):
            google.run_gog(["calendar", "delete", "primary", "id"], "calendar.delete")
        run.assert_not_called()

    def test_upstream_failure_never_leaks_stderr(self):
        result = subprocess.CompletedProcess([], 1, "", "private-token recovery-link account@example.test")
        with patch.object(google.subprocess, "run", return_value=result), self.assertRaises(google.GoogleError) as caught:
            google.run_gog(["calendar", "calendars"], "calendar.calendars")
        self.assertNotIn("private-token", str(caught.exception))

    def test_timeout_and_invalid_json_fail(self):
        for side_effect, result in ((subprocess.TimeoutExpired("gog", 60), None),
                                    (None, subprocess.CompletedProcess([], 0, "not JSON", "")),
                                    (None, subprocess.CompletedProcess([], 0, "null", ""))):
            with self.subTest(result=result), patch.object(google.subprocess, "run", side_effect=side_effect, return_value=result), self.assertRaises(google.GoogleError):
                google.run_gog(["calendar", "calendars"], "calendar.calendars")

    def test_calendar_pagination_complete(self):
        pages = [{"calendars": [{"id": "first"}], "nextPageToken": "next"}, {"calendars": [{"id": "second"}]}]
        with patch.object(google, "run_gog", side_effect=pages) as run:
            self.assertEqual([r["id"] for r in google.calendars()], ["first", "second"])
        self.assertEqual(run.call_args.args[0][-2:], ["--page", "next"])

    def test_pagination_failure_never_means_empty_calendar(self):
        for result in ({"calendars": []}, {"calendars": "invalid"},
                       {"calendars": [{"id": "demo"}], "nextPageToken": "repeat"}):
            with self.subTest(result=result), patch.object(google, "run_gog", return_value=result), self.assertRaises(google.GoogleError):
                google.calendars()

    def test_gmail_pagination_preserved_and_security_mail_withheld(self):
        result = {"messages": [{"id": "message-a", "subject": "Verification code", "snippet": "123456 https://example.test/reset?token=private"}], "nextPageToken": "next"}
        status, out, err, run = self.invoke(["gmail", "search", "newer_than:1d"], result)
        self.assertEqual(status, 0, err)
        data = json.loads(out)
        self.assertTrue(data["read_only"])
        self.assertTrue(data["untrusted_content"])
        self.assertEqual(data["data"]["nextPageToken"], "next")
        self.assertEqual(data["data"]["messages"][0]["id"], "message-a")
        self.assertNotIn("123456", out)
        self.assertNotIn("https://", out)

    def test_normal_mail_retains_content_and_removes_links(self):
        status, out, err, run = self.invoke(["gmail", "read", "message-a"], {"message": {"id": "message-a", "body": "Team lunch https://example.test"}})
        self.assertEqual(status, 0, err)
        self.assertIn("Team lunch", out)
        self.assertNotIn("https://", out)
        self.assertIn("--sanitize-content", run.call_args.args[0])

    def test_malformed_mail_is_not_success(self):
        for args, response in ((["gmail", "search", "example"], {"unexpected": []}),
                               (["gmail", "read", "message-a"], {"message": {}})):
            status, out, err, run = self.invoke(args, response)
            self.assertEqual(status, 1)
            self.assertEqual(out, "")
            self.assertFalse(json.loads(err)["verified"])

    def test_empty_events_only_after_discovery(self):
        pages = [{"calendars": [{"id": "demo", "primary": True}]}, {"events": []}]
        with patch.object(google, "run_gog", side_effect=pages):
            result = google.list_events("2026-10-24T00:00:00+00:00", "2026-10-25T00:00:00+00:00")
        self.assertTrue(result["complete"])
        self.assertEqual(result["events"], [])

    def test_incomplete_event_is_not_a_complete_schedule(self):
        for event in ({"id": "event-a"}, {"id": "event-a", "start": {"date": "bad"}, "end": {"date": "bad"}}):
            pages = [{"calendars": [{"id": "demo"}]}, {"events": [event]}]
            with self.subTest(event=event), patch.object(google, "run_gog", side_effect=pages), self.assertRaises(google.GoogleError):
                google.list_events("2026-10-24T00:00:00+00:00", "2026-10-25T00:00:00+00:00")

    def test_timed_and_all_day_events_keep_source_identity(self):
        events = [{"id": "timed", "start": {"dateTime": "2026-10-24T09:00:00Z"},
                   "end": {"dateTime": "2026-10-24T10:00:00Z"}},
                  {"id": "all-day", "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-25"}}]
        pages = [{"calendars": [{"id": "demo"}]}, {"events": events}]
        with patch.object(google, "run_gog", side_effect=pages):
            result = google.list_events("2026-10-24T00:00:00+00:00", "2026-10-25T00:00:00+00:00")
        self.assertTrue(result["complete"])
        self.assertEqual({row["calendar_id"] for row in result["events"]}, {"demo"})

    def test_native_calendar_operations_and_recurrence_flags_pass_through(self):
        examples = [
            ["create", "shared-calendar", "--summary=Planning", "--from=2026-10-24T09:00:00-04:00",
             "--to=2026-10-24T10:00:00-04:00", "--attendees=guest@example.test", "--rrule=RRULE:FREQ=WEEKLY"],
            ["update", "shared-calendar", "event-a", "--summary=Renamed", "--scope=single",
             "--original-start=2026-10-24T09:00:00-04:00"],
            ["delete", "shared-calendar", "event-a", "--scope=all"],
            ["respond", "shared-calendar", "event-a", "--status=accepted"],
        ]
        for arguments in examples:
            with self.subTest(arguments=arguments):
                status, out, err, run = self.invoke(["calendar", "manage", *arguments], {"id": "event-a"})
                self.assertEqual(status, 0, err)
                self.assertEqual(run.call_args.args, (["calendar", *arguments], "calendar"))
                self.assertTrue(run.call_args.kwargs["calendar_management"])
                self.assertTrue(json.loads(out)["untrusted_content"])
                self.assertNotIn("verified", json.loads(out))

    def test_calendar_management_uses_full_separate_profile(self):
        result = subprocess.CompletedProcess([], 0, '{"id":"event-a"}', "")
        with patch.object(google.subprocess, "run", return_value=result) as run:
            google.run_gog(["calendar", "delete", "primary", "event-a"], "calendar", calendar_management=True)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--client") + 1], "tari-calendar-write")
        self.assertEqual(cmd[cmd.index("--enable-commands") + 1], "calendar")
        self.assertIn("--gmail-no-send", cmd)
        self.assertIn("--force", cmd)
        self.assertNotIn("--readonly", cmd)

    def test_calendar_management_cannot_route_to_mail(self):
        with patch.object(google.subprocess, "run") as run, self.assertRaises(google.GoogleError):
            google.run_gog(["gmail", "send"], "calendar", calendar_management=True)
        run.assert_not_called()

    def test_native_calendar_flags_cannot_override_credentials_or_output(self):
        for flag in ("--account=other@example.test", "-aother@example.test", "--home=/other",
                     "--client=other", "--access-token=private", "--results-only", "--json=false"):
            with self.subTest(flag=flag), patch.object(google, "run_gog") as run, self.assertRaises(google.GoogleError):
                google.manage_calendar(["create", "primary", flag])
            run.assert_not_called()

    def test_calendar_timeout_is_uncertain_and_never_retried(self):
        with patch.object(google, "run_gog", side_effect=google.GoogleError("timed out")) as run:
            with self.assertRaisesRegex(google.GoogleError, "outcome may be unknown"):
                google.manage_calendar(["create", "primary", "--summary=Planning"])
        self.assertEqual(run.call_count, 1)

    def test_deletion_absence_requires_actual_404_or_410(self):
        for status in (404, 410):
            result = subprocess.CompletedProcess([], 1, "", "googleapi: Error " + str(status) + ": gone")
            with self.subTest(status=status), patch.object(google.subprocess, "run", return_value=result):
                self.assertEqual(google.event("primary", "event-a", allow_absent=True), {"absent": True})
        result = subprocess.CompletedProcess([], 1, "", "authentication failed: private response")
        with patch.object(google.subprocess, "run", return_value=result), self.assertRaises(google.GoogleError):
            google.event("primary", "event-a", allow_absent=True)


if __name__ == "__main__":
    unittest.main()
