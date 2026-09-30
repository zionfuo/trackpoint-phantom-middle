"""Tests for the pure policy inside trackpoint-phantom-middle.py.

No hardware, no root, no EVIOCGRAB: every case here is either a pure function or
fed synthetic device records shaped exactly like the ones probe() builds. So this
runs anywhere, including CI and a machine with no TrackPoint at all.

    python3 -m unittest discover -s tests
"""
import importlib.util
import unittest
from pathlib import Path

from evdev import ecodes as e

REPO = Path(__file__).resolve().parent.parent
DAEMON = REPO / "trackpoint-phantom-middle.py"


def load_daemon():
    """Import the daemon by path.

    Its filename contains dashes, so it is not importable by name — and renaming
    it is not on the table: install.sh copies this one file to /usr/local/bin,
    and that single-file deployment is deliberate.
    """
    spec = importlib.util.spec_from_file_location("trackpoint_phantom_middle", DAEMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TPM = load_daemon()


def record(path, name, phys, pointer_like=True, trackpoint_like=True, self_=False):
    return {
        "path": path,
        "name": name,
        "phys": phys,
        "self": self_,
        "pointer_like": pointer_like,
        "trackpoint_like": trackpoint_like,
    }


# The input table of the development machine (X1 Carbon Gen 8), reduced to the
# nodes that matter. `event4` is the reason this project does capability
# detection the hard way: the Synaptics touchpad's RMI4 companion "Mouse" node
# passes every capability test the TrackPoint does.
MACHINE = [
    record("/dev/input/event3", "AT Translated Set 2 keyboard", "isa0060/serio0/input0",
           pointer_like=False, trackpoint_like=False),
    record("/dev/input/event4", "SYNA8006:00 06CB:CD8B Mouse", "i2c-SYNA8006:00",
           pointer_like=True, trackpoint_like=False),
    record("/dev/input/event5", "SYNA8006:00 06CB:CD8B Touchpad", "i2c-SYNA8006:00",
           pointer_like=False, trackpoint_like=False),
    record("/dev/input/event6", "TPPS/2 Elan TrackPoint", "isa0060/serio1/input0"),
    record("/dev/input/event15", "TrackPoint Middle Filtered", "py-evdev-uinput",
           pointer_like=True, trackpoint_like=True, self_=True),
]


class ClassifyTests(unittest.TestCase):
    """The forwarding policy, pinned. This is the whole behaviour of the daemon."""

    def test_middle_button_is_dropped(self):
        self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_MIDDLE), TPM.DROP)

    def test_left_and_right_are_forwarded(self):
        self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_LEFT), TPM.FORWARD)
        self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_RIGHT), TPM.FORWARD)

    def test_motion_is_forwarded(self):
        self.assertEqual(TPM.classify(e.EV_REL, e.REL_X), TPM.FORWARD)
        self.assertEqual(TPM.classify(e.EV_REL, e.REL_Y), TPM.FORWARD)

    def test_other_keys_are_ignored_not_dropped(self):
        # Ignored means "we never forward it", which is different from DROP:
        # only DROP feeds the phantom counter, so the log number means what the
        # README says it means.
        self.assertEqual(TPM.classify(e.EV_KEY, e.KEY_A), TPM.IGNORE)
        self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_TASK), TPM.IGNORE)

    def test_syn_report_forwarded(self):
        self.assertEqual(TPM.classify(e.EV_SYN, e.SYN_REPORT), TPM.FORWARD)

    def test_syn_dropped_asks_for_resync(self):
        self.assertEqual(TPM.classify(e.EV_SYN, e.SYN_DROPPED), TPM.RESYNC)

    def test_other_syn_is_ignored(self):
        self.assertEqual(TPM.classify(e.EV_SYN, e.SYN_CONFIG), TPM.IGNORE)

    def test_other_types_ignored(self):
        self.assertEqual(TPM.classify(e.EV_MSC, e.MSC_SCAN), TPM.IGNORE)

    def test_drop_keys_is_configurable(self):
        saved = TPM.DROP_KEYS
        try:
            TPM.DROP_KEYS = {e.BTN_TASK}
            self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_TASK), TPM.DROP)
            self.assertEqual(TPM.classify(e.EV_KEY, e.BTN_MIDDLE), TPM.IGNORE)
        finally:
            TPM.DROP_KEYS = saved


class ParseDropKeysTests(unittest.TestCase):
    def test_names_resolve_to_codes(self):
        self.assertEqual(TPM.parse_drop_keys("BTN_MIDDLE,BTN_TASK"),
                         {e.BTN_MIDDLE, e.BTN_TASK})

    def test_whitespace_and_blanks_tolerated(self):
        self.assertEqual(TPM.parse_drop_keys(" BTN_MIDDLE , ,"), {e.BTN_MIDDLE})

    def test_unknown_names_are_skipped(self):
        self.assertEqual(TPM.parse_drop_keys("BTN_MIDDLE,NOT_A_KEY"), {e.BTN_MIDDLE})

    def test_empty_yields_empty(self):
        self.assertEqual(TPM.parse_drop_keys(""), set())


class IsSelfTests(unittest.TestCase):
    def test_uinput_phys_is_self(self):
        self.assertTrue(TPM.is_self("py-evdev-uinput", "anything"))

    def test_uinput_name_is_self(self):
        self.assertTrue(TPM.is_self("some/phys", TPM.UINPUT_NAME))

    def test_real_device_is_not_self(self):
        self.assertFalse(TPM.is_self("isa0060/serio1/input0", "TPPS/2 Elan TrackPoint"))


class ChooseTests(unittest.TestCase):
    def test_autodetect_finds_the_trackpoint_not_the_touchpad_mouse(self):
        pick, note = TPM.choose(MACHINE, "", "")
        self.assertIsNotNone(pick, note)
        self.assertEqual(pick["path"], "/dev/input/event6")

    def test_autodetect_never_returns_our_own_output(self):
        pick, _ = TPM.choose(MACHINE, "", "")
        self.assertNotEqual(pick["path"], "/dev/input/event15")

    def test_exact_phys_wins_over_name(self):
        pick, _ = TPM.choose(MACHINE, "isa0060/serio1/input0", "nonsense")
        self.assertEqual(pick["path"], "/dev/input/event6")

    def test_exact_name_used_when_no_phys(self):
        pick, _ = TPM.choose(MACHINE, "", "TPPS/2 Elan TrackPoint")
        self.assertEqual(pick["path"], "/dev/input/event6")

    def test_exact_override_still_cannot_grab_our_own_output(self):
        # A TRACKPOINT_PHYS of py-evdev-uinput would otherwise make the daemon
        # filter its own pointer and freeze the cursor.
        pick, note = TPM.choose(MACHINE, "py-evdev-uinput", "")
        self.assertIsNone(pick)
        self.assertIn("matched", note)

    def test_ambiguity_is_refused_rather_than_guessed(self):
        # Two nodes claiming the same phys: refuse rather than pick by lexical
        # accident, and name the values to choose from.
        table = MACHINE + [
            record("/dev/input/event20", "TPPS/2 Elan TrackPoint", "isa0060/serio1/input0")
        ]
        pick, note = TPM.choose(table, "isa0060/serio1/input0", "")
        self.assertIsNone(pick)
        self.assertIn("refusing to guess", note)

    def test_touchpad_sharing_serio1_is_excluded_by_name_not_by_phys(self):
        # This is why a phys *prefix* was rejected. Where the trackpad is also
        # PS/2 Elantech, serio1 exposes input0 and input1, and a prefix like
        # 'isa0060/serio1/*' would match the touchpad too — grabbing it, killing
        # the red dot and breaking the touchpad. The name marker is what actually
        # separates them, and matching on exact identity keeps a typo harmless.
        table = [
            record("/dev/input/event6", "TPPS/2 Elan TrackPoint", "isa0060/serio1/input0"),
            record("/dev/input/event7", "ETPS/2 Elantech Touchpad", "isa0060/serio1/input1",
                   pointer_like=True, trackpoint_like=False),
        ]
        pick, note = TPM.choose(table, "", "")
        self.assertIsNotNone(pick, note)
        self.assertEqual(pick["path"], "/dev/input/event6")

    def test_autodetect_refuses_when_two_candidates_share_the_isa_preference(self):
        table = [
            record("/dev/input/event6", "TPPS/2 Elan TrackPoint", "isa0060/serio1/input0"),
            record("/dev/input/event7", "TPPS/2 IBM TrackPoint", "isa0060/serio1/input1"),
        ]
        pick, note = TPM.choose(table, "", "")
        self.assertIsNone(pick)
        self.assertIn("refusing to guess", note)

    def test_isa_preferred_over_other_phys(self):
        table = [
            record("/dev/input/event4", "Some TrackPoint Dock", "usb-0000:00:14.0-1/input0"),
            record("/dev/input/event6", "TPPS/2 Elan TrackPoint", "isa0060/serio1/input0"),
        ]
        pick, _ = TPM.choose(table, "", "")
        self.assertEqual(pick["path"], "/dev/input/event6")

    def test_override_rejected_when_not_pointer_like(self):
        pick, note = TPM.choose(MACHINE, "isa0060/serio0/input0", "")
        self.assertIsNone(pick)
        self.assertIn("not a relative pointer", note)

    def test_no_match_reports_the_source(self):
        pick, note = TPM.choose(MACHINE, "nope/input9", "")
        self.assertIsNone(pick)
        self.assertIn("TRACKPOINT_PHYS=nope/input9", note)

    def test_keyboard_is_never_autodetected(self):
        pick, _ = TPM.choose(MACHINE, "", "")
        self.assertNotEqual(pick["name"], "AT Translated Set 2 keyboard")

    def test_describe_mentions_phys_and_tags(self):
        line = TPM.describe(MACHINE[-1])
        self.assertIn("py-evdev-uinput", line)
        self.assertIn("own output", line)


class ButtonTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = TPM.ButtonTracker({e.BTN_LEFT, e.BTN_RIGHT})

    def test_press_then_release(self):
        self.tracker.note(e.BTN_LEFT, 1)
        self.assertEqual(self.tracker.held(), {e.BTN_LEFT})
        self.tracker.note(e.BTN_LEFT, 0)
        self.assertEqual(self.tracker.held(), set())

    def test_ignores_buttons_we_do_not_forward(self):
        self.tracker.note(e.BTN_MIDDLE, 1)
        self.assertEqual(self.tracker.held(), set())

    def test_reconcile_reports_a_missed_release(self):
        # We forwarded a press; the kernel no longer reports the button down.
        # The release is what must be emitted or the button sticks.
        self.tracker.note(e.BTN_LEFT, 1)
        release, press = self.tracker.reconcile([])
        self.assertEqual(release, {e.BTN_LEFT})
        self.assertEqual(press, set())

    def test_reconcile_reports_a_missed_press(self):
        release, press = self.tracker.reconcile([e.BTN_LEFT])
        self.assertEqual(press, {e.BTN_LEFT})
        self.assertEqual(release, set())

    def test_reconcile_is_empty_when_in_agreement(self):
        self.tracker.note(e.BTN_LEFT, 1)
        self.assertEqual(self.tracker.reconcile([e.BTN_LEFT]), (set(), set()))

    def test_reconcile_ignores_buttons_outside_our_set(self):
        release, press = self.tracker.reconcile([e.BTN_MIDDLE, e.KEY_A])
        self.assertEqual((release, press), (set(), set()))


if __name__ == "__main__":
    unittest.main()
