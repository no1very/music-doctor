"""Unit tests for the Navidrome artwork maintenance helper.

Run with:  python -m unittest discover -s tests -v
No Docker, SSH or Navidrome is required: every case pins the environment and
inspects the built command line instead of executing it.
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MTW_DATA_DIR", _TMP)

from app import navidrome  # noqa: E402

ENV_KEYS = [
    "MTW_NAVIDROME_CONTAINER",
    "MTW_NAVIDROME_DATA_FOLDER",
    "MTW_NAVIDROME_SSH",
    "MTW_NAVIDROME_DOCKER",
    "MTW_NAVIDROME_TIMEOUT",
    "MTW_NAVIDROME_AUTOREPROCESS",
]


def clean_env(**values: str) -> dict:
    env = {key: "" for key in ENV_KEYS}
    env.update(values)
    return env


class CommandBuildingTests(unittest.TestCase):
    def build(self, kind: str, artist: str = "", **env: str) -> dict:
        with mock.patch.dict(os.environ, clean_env(**env), clear=False):
            return navidrome.command(kind, artist)

    def test_reprocess_matches_documented_invocation(self):
        built = self.build("reprocess", MTW_NAVIDROME_CONTAINER="navidrome")
        self.assertEqual(
            built["argv"],
            [
                "docker", "exec", "navidrome", "/app/navidrome", "artwork", "reprocess",
                "--kind", "ar", "-y", "--datafolder", "/data",
            ],
        )
        self.assertEqual(built["recipe"], "local")
        self.assertEqual(
            built["command"],
            "docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data",
        )

    def test_status_has_no_extra_flags(self):
        built = self.build("status", MTW_NAVIDROME_CONTAINER="navidrome")
        self.assertEqual(built["argv"][-4:], ["artwork", "status", "--datafolder", "/data"])

    def test_explain_accepts_bare_id_and_prefixes_ar(self):
        built = self.build("explain", "0123456789abcdef", MTW_NAVIDROME_CONTAINER="navidrome")
        self.assertIn("ar-0123456789abcdef", built["argv"])
        built = self.build("explain", "ar-0123456789abcdef", MTW_NAVIDROME_CONTAINER="navidrome")
        self.assertIn("ar-0123456789abcdef", built["argv"])

    def test_explain_without_id_is_a_config_error(self):
        with self.assertRaises(ValueError):
            self.build("explain", "", MTW_NAVIDROME_CONTAINER="navidrome")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build("rescan", MTW_NAVIDROME_CONTAINER="navidrome")

    def test_data_folder_and_container_overrides(self):
        built = self.build("reprocess", MTW_NAVIDROME_CONTAINER="nav",
                            MTW_NAVIDROME_DATA_FOLDER="/srv/nd")
        self.assertEqual(built["container"], "nav")
        self.assertEqual(built["data_folder"], "/srv/nd")
        self.assertIn("/srv/nd", built["argv"])

    def test_ssh_recipe_quotes_the_remote_command(self):
        built = self.build("reprocess", MTW_NAVIDROME_CONTAINER="navidrome",
                            MTW_NAVIDROME_SSH="nas")
        self.assertEqual(built["recipe"], "ssh")
        self.assertEqual(built["argv"][:2], ["ssh", "nas"])
        remote = built["argv"][2]
        self.assertIn("docker exec navidrome /app/navidrome artwork reprocess", remote)
        self.assertIn("--datafolder /data", remote)

    def test_ssh_target_with_flags(self):
        built = self.build("status", MTW_NAVIDROME_CONTAINER="navidrome",
                            MTW_NAVIDROME_SSH="root@192.168.1.10 -p 2222")
        self.assertEqual(built["argv"][:4], ["ssh", "root@192.168.1.10", "-p", "2222"])

    def test_container_is_lazy(self):
        built = self.build("status")
        self.assertEqual(built["container"], "")
        self.assertIn("<navidrome>", built["command"])


class SettingsAndAvailabilityTests(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            cfg = navidrome.settings()
        self.assertEqual(cfg["data_folder"], "/data")
        self.assertEqual(cfg["docker"], "docker")
        self.assertTrue(cfg["auto"])
        self.assertEqual(cfg["timeout"], 900)

    def test_auto_can_be_disabled(self):
        with mock.patch.dict(os.environ, clean_env(MTW_NAVIDROME_AUTOREPROCESS="0"), clear=False):
            self.assertFalse(navidrome.settings()["auto"])

    def test_missing_docker_is_reported_not_raised(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome.shutil, "which", return_value=None):
                ok, why = navidrome.availability()
        self.assertFalse(ok)
        self.assertIn("tools/navidrome.py reprocess", why)

    def test_run_without_docker_returns_error_result(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome.shutil, "which", return_value=None):
                result = navidrome.run("status")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], -3)
        self.assertIn("docker", result["stderr"])

    def test_container_detection_prefers_exact_name(self):
        cfg = navidrome.settings()
        out = "some-music\tnavidrome/navidrome:latest\nnavidrome\tnavidrome/navidrome:latest\n"

        class FakeProc:
            returncode = 0
            stdout = out
            stderr = ""

        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome.shutil, "which", return_value="/usr/bin/docker"):
                with mock.patch.object(navidrome.subprocess, "run", return_value=FakeProc()):
                    with mock.patch.dict(navidrome._CONTAINER_CACHE, clear=True):
                        self.assertEqual(navidrome.detect_container(cfg), "navidrome")


class HookTests(unittest.TestCase):
    def tearDown(self):
        navidrome._release()

    def test_no_new_images_skips(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            summary = navidrome.after_export(0)
        self.assertFalse(summary["started"])
        self.assertIn("没有写入新图片", summary["reason"])

    def test_disabled_by_env(self):
        with mock.patch.dict(os.environ, clean_env(MTW_NAVIDROME_AUTOREPROCESS="0"), clear=False):
            summary = navidrome.after_export(3)
        self.assertFalse(summary["enabled"])
        self.assertFalse(summary["started"])

    def test_unavailable_transport_is_reported(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome.shutil, "which", return_value=None):
                summary = navidrome.after_export(3)
        self.assertFalse(summary["started"])
        self.assertIn("docker", summary["reason"])

    def test_second_call_merges_into_the_active_reprocess(self):
        self.assertTrue(navidrome._claim())
        with mock.patch.dict(os.environ, clean_env(MTW_NAVIDROME_CONTAINER="navidrome"), clear=False):
            with mock.patch.object(navidrome.shutil, "which", return_value="/usr/bin/docker"):
                summary = navidrome.after_export(2)
        self.assertFalse(summary["started"])
        self.assertIn("已有重算任务", summary["reason"])


class SignatureTests(unittest.TestCase):
    def test_signature_is_empty_for_unreadable_folder(self):
        self.assertEqual(navidrome.image_signature(os.path.join(_TMP, "missing-folder")), "")

    def test_signature_changes_when_a_file_is_added(self):
        folder = os.path.join(_TMP, f"nd{uuid.uuid4().hex[:8]}")
        os.makedirs(folder, exist_ok=True)
        before = navidrome.image_signature(folder)
        with open(os.path.join(folder, "artist.jpg"), "wb") as handle:
            handle.write(b"\xff\xd8\xff")
        after = navidrome.image_signature(folder)
        self.assertTrue(before)
        self.assertTrue(after)
        self.assertNotEqual(before, after)


class RunAndStateTests(unittest.TestCase):
    """The orchestration path: build → execute → remember (no real Docker)."""

    def setUp(self):
        self.state_name = f"nd-state-{uuid.uuid4().hex[:8]}.json"
        patcher = mock.patch.object(navidrome, "STATE_FILE", self.state_name)
        patcher.start()
        self.addCleanup(patcher.stop)
        # _remember_reprocess() writes into config.data_dir, which may differ from _TMP
        # when MTW_DATA_DIR is set explicitly (e.g. a host-side run on the NAS).
        self.state_dir = str(navidrome.config.data_dir)
        self.addCleanup(
            lambda: os.path.exists(os.path.join(self.state_dir, self.state_name))
            and os.remove(os.path.join(self.state_dir, self.state_name))
        )
        self.folder = os.path.join(_TMP, f"nd-exp{uuid.uuid4().hex[:8]}")
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, "artist.jpg"), "wb") as handle:
            handle.write(b"\xff\xd8\xff")

    def test_reprocess_runs_built_argv_and_records_state(self):
        executed = {}

        def fake_run(argv, timeout):
            executed["argv"] = argv
            return {"ok": True, "code": 0, "stdout": "Sources: artist folder 254",
                    "stderr": "", "timed_out": False}

        with mock.patch.dict(os.environ, clean_env(MTW_NAVIDROME_CONTAINER="navidrome"), clear=False):
            with mock.patch.object(navidrome, "availability", return_value=(True, "")):
                with mock.patch.object(navidrome, "_run_process", side_effect=fake_run):
                    with mock.patch.object(navidrome, "db") as fake_db:
                        fake_db.get_setting.return_value = self.folder
                        result = navidrome.reprocess()

        self.assertTrue(result["ok"])
        self.assertEqual(executed["argv"][:3], ["docker", "exec", "navidrome"])
        self.assertIn("reprocess", executed["argv"])
        state_file = os.path.join(self.state_dir, self.state_name)
        self.assertTrue(os.path.exists(state_file))
        import json

        state = json.loads(open(state_file, encoding="utf-8").read())
        self.assertIn("last_reprocess_at", state)
        self.assertEqual(state["container"], "navidrome")
        self.assertEqual(state["image_signature"], navidrome.image_signature(self.folder))

    def test_container_is_detected_when_not_configured(self):
        seen = {}

        def fake_run(argv, timeout):
            seen["argv"] = argv
            return {"ok": True, "code": 0, "stdout": "", "stderr": "", "timed_out": False}

        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome, "availability", return_value=(True, "")):
                with mock.patch.object(navidrome, "detect_container", return_value="navidrome"):
                    with mock.patch.object(navidrome, "_run_process", side_effect=fake_run):
                        result = navidrome.status()

        self.assertIn("navidrome", seen["argv"])
        self.assertEqual(result["container"], "navidrome")

    def test_container_is_detected_when_caller_passed_none(self):
        """CLI always passes container= explicitly (None when unset) — detection must
        not collide with that keyword (regression seen on the NAS)."""
        seen = {}

        def fake_run(argv, timeout):
            seen["argv"] = argv
            return {"ok": True, "code": 0, "stdout": "", "stderr": "", "timed_out": False}

        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome, "availability", return_value=(True, "")):
                with mock.patch.object(navidrome, "detect_container", return_value="navidrome"):
                    with mock.patch.object(navidrome, "_run_process", side_effect=fake_run):
                        result = navidrome.status(container=None, data_folder="/data")

        self.assertTrue(result["ok"])
        self.assertIn("navidrome", seen["argv"])
        self.assertEqual(result["container"], "navidrome")

    def test_missing_container_is_a_clear_error(self):
        with mock.patch.dict(os.environ, clean_env(), clear=False):
            with mock.patch.object(navidrome, "availability", return_value=(True, "")):
                with mock.patch.object(navidrome, "detect_container", return_value=""):
                    result = navidrome.status()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], -4)
        self.assertIn("MTW_NAVIDROME_CONTAINER", result["stderr"])

    def test_needs_reprocess_tracks_the_export_folder(self):
        with mock.patch.object(navidrome, "db") as fake_db:
            fake_db.get_setting.return_value = self.folder
            unknown = os.path.join(self.state_dir, self.state_name)
            if os.path.exists(unknown):
                os.remove(unknown)
            need, why = navidrome.needs_reprocess()
            self.assertTrue(need)
            self.assertIn("重算记录", why)

            navidrome._remember_reprocess(
                {"container": "navidrome", "data_folder": "/data"}
            )
            need, why = navidrome.needs_reprocess()
            self.assertFalse(need)

            with open(os.path.join(self.folder, "second.jpg"), "wb") as handle:
                handle.write(b"\xff\xd8\xff")
            need, why = navidrome.needs_reprocess()
            self.assertTrue(need)
            self.assertIn("已变化", why)


if __name__ == "__main__":
    unittest.main()
