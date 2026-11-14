"""Unit tests for app/ubs_core.py (no Qt needed)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import ubs_core  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "widgets"


class WidgetDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.w = ubs_core.Widgets(FIXTURES)

    def test_finds_jsx_and_widget_folders(self):
        self.assertEqual(self.w.ids(), ["clock.widget/index.jsx", "stateful.jsx"])

    def test_skips_hidden_underscore_and_vendor_dirs(self):
        ids = self.w.ids()
        self.assertNotIn(".hidden.jsx", ids)
        self.assertFalse(any(i.startswith(("node_modules/", "lib/")) for i in ids))
        self.assertNotIn("clock.widget/helper.js", ids)

    def test_missing_root_gives_no_widgets(self):
        self.assertEqual(ubs_core.Widgets(FIXTURES / "nope").ids(), [])

    def test_visible_filters_hidden(self):
        self.assertEqual(self.w.visible({"stateful.jsx"}), ["clock.widget/index.jsx"])

    def test_path_resolves_files_inside_root(self):
        self.assertEqual(self.w.path("stateful.jsx"), (FIXTURES / "stateful.jsx").resolve())
        self.assertEqual(self.w.path("_helper.js"), (FIXTURES / "_helper.js").resolve())

    def test_path_rejects_traversal_and_missing(self):
        self.assertIsNone(self.w.path("../test_core.py"))
        self.assertIsNone(self.w.path("/etc/passwd"))
        self.assertIsNone(self.w.path("missing.jsx"))
        self.assertIsNone(self.w.path("clock.widget"))  # directories are not served


class StateTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "state.json"
            ubs_core.save_state({"hidden_widgets": ["a.jsx"], "desktop_hidden": True}, f)
            self.assertEqual(ubs_core.load_state(f), {"hidden_widgets": ["a.jsx"], "desktop_hidden": True})
            self.assertFalse((Path(d) / "sub" / "state.tmp").exists())

    def test_missing_or_corrupt_state_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "state.json"
            self.assertEqual(ubs_core.load_state(f), {})
            f.write_text("{not json")
            self.assertEqual(ubs_core.load_state(f), {})
            f.write_text("[1, 2]")
            self.assertEqual(ubs_core.load_state(f), {})

    def test_default_locations_follow_xdg(self):
        env = {"XDG_STATE_HOME": "/s", "XDG_DATA_HOME": "/d"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("UBERSICHT_WIDGETS_DIR", None)
            self.assertEqual(ubs_core.state_file(), Path("/s/kde-uebersicht/state.json"))
            self.assertEqual(ubs_core.default_widgets_dir(), Path("/d/uebersicht/widgets"))
        with mock.patch.dict(os.environ, {"UBERSICHT_WIDGETS_DIR": "/w"}):
            self.assertEqual(ubs_core.default_widgets_dir(), Path("/w"))


class EsbuildTest(unittest.TestCase):
    def test_argv_uses_shared_flags(self):
        argv = ubs_core.esbuild_argv("/bin/esbuild", Path("/w/x.jsx"))
        self.assertEqual(argv[:2], ["/bin/esbuild", "/w/x.jsx"])
        self.assertEqual(argv[2:], json.loads(ubs_core.ESBUILD_FLAGS_FILE.read_text()))
        self.assertIn("--external:uebersicht", argv)
        self.assertIn("--format=cjs", argv)

    def test_explicit_esbuild_wins(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "esbuild"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(0o755)
            self.assertEqual(ubs_core.find_esbuild(str(exe)), str(exe))


class ScaleRectsTest(unittest.TestCase):
    def test_identity_without_zoom(self):
        self.assertEqual(ubs_core.scale_rects([(10, 20, 30, 40)], (100, 100), (100, 100)), [(10, 20, 30, 40)])

    def test_text_scaling_zoom(self):
        # zoom 1.1: viewport 2792 CSS px inside a 3072 px window
        [(x, y, w, h)] = ubs_core.scale_rects([(731.2, 100.2, 13.6, 15.5)], (2792, 1477), (3072, 1728))
        self.assertLessEqual(x, 731.2 * 3072 / 2792)
        self.assertGreaterEqual(x + w, (731.2 + 13.6) * 3072 / 2792)
        self.assertGreaterEqual(y + h, (100.2 + 15.5) * 1728 / 1477)

    def test_missing_viewport_falls_back_to_identity(self):
        self.assertEqual(ubs_core.scale_rects([(1, 2, 3, 4)], None, (50, 50)), [(1, 2, 3, 4)])
        self.assertEqual(ubs_core.scale_rects([(1, 2, 3, 4)], (0, 0), (50, 50)), [(1, 2, 3, 4)])


class ZoomTest(unittest.TestCase):
    def test_parse_zoom_mode(self):
        self.assertEqual(ubs_core.parse_zoom_mode("system"), "system")
        self.assertEqual(ubs_core.parse_zoom_mode(None), "system")
        self.assertEqual(ubs_core.parse_zoom_mode("1.25"), 1.25)
        self.assertEqual(ubs_core.parse_zoom_mode(1.5), 1.5)
        self.assertEqual(ubs_core.parse_zoom_mode("junk"), "system")
        self.assertEqual(ubs_core.parse_zoom_mode("99"), "system")

    def test_system_mode_follows_text_scale(self):
        # the engine already zooms by 1.1 on its own → nothing extra needed
        self.assertAlmostEqual(ubs_core.webkit_zoom_level("system", 1.1, 1.1), 1.0)
        # the engine does not scale by itself → we apply it
        self.assertAlmostEqual(ubs_core.webkit_zoom_level("system", 1.5, 1.0), 1.5)

    def test_fixed_mode_compensates_implicit_zoom(self):
        self.assertAlmostEqual(ubs_core.webkit_zoom_level(1.0, 1.1, 1.1), 1 / 1.1)
        self.assertAlmostEqual(ubs_core.webkit_zoom_level(1.25, 1.1, 1.0), 1.25)

    def test_bad_inputs_fall_back_to_one(self):
        self.assertAlmostEqual(ubs_core.webkit_zoom_level("system", 0, 0), 1.0)


class PlasmaHelpersTest(unittest.TestCase):
    def test_zoom_key(self):
        self.assertEqual(ubs_core.zoom_key("system"), "system")
        self.assertEqual(ubs_core.zoom_key(1.0), "1")
        self.assertEqual(ubs_core.zoom_key(1.25), "1.25")
        keys = [k for k, _ in ubs_core.ZOOM_CHOICES]
        for preset in ubs_core.ZOOM_PRESETS:
            self.assertIn(ubs_core.zoom_key(preset), keys)

    def test_widget_label(self):
        self.assertEqual(ubs_core.widget_label("hello.jsx"), "hello")
        self.assertEqual(ubs_core.widget_label("clock.widget/index.jsx"), "clock.widget")
        self.assertEqual(ubs_core.widget_label("a/b.widget/index.js"), "a/b.widget")

    def test_parse_command(self):
        self.assertEqual(ubs_core.parse_command("reload\n"), ("reload", ""))
        self.assertEqual(ubs_core.parse_command(" widget-hide  my clock.jsx \n"), ("widget-hide", "my clock.jsx"))

    def test_control_socket_path(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/42"}):
            self.assertEqual(ubs_core.control_socket_path(), Path("/run/user/42/kde-uebersicht.sock"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ubs_core.control_socket_path().parent, Path("/tmp"))

    def test_scan_and_diff(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.jsx").write_text("1")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text("x")
            (root / ".hidden.jsx").write_text("h")
            before = ubs_core.scan_tree(root)
            self.assertEqual(set(before), {"a.jsx"})
            (root / "a.jsx").write_text("22")
            (root / "lib").mkdir()
            (root / "lib" / "util.js").write_text("u")
            after = ubs_core.scan_tree(root)
            self.assertEqual(ubs_core.changed_paths(before, after), {"a.jsx", "lib/util.js"})
            self.assertEqual(ubs_core.changed_paths(after, after), set())
        self.assertEqual(ubs_core.scan_tree(Path(d)), {})

    def test_send_command_roundtrip(self):
        import socket
        import threading

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.sock"
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(1)

            def serve():
                conn, _ = srv.accept()
                with conn:
                    line = conn.makefile().readline()
                    conn.sendall(json.dumps({"got": line.strip()}).encode() + b"\n")

            t = threading.Thread(target=serve)
            t.start()
            reply = ubs_core.send_command(path, "status")
            t.join()
            srv.close()
            self.assertEqual(json.loads(reply), {"got": "status"})


if __name__ == "__main__":
    unittest.main()
