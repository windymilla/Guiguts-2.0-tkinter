"""Test functions"""

from guiguts.file import File
from guiguts.mainwindow import process_label, process_accel
from guiguts.preferences import preferences, PrefKey
from guiguts.utilities import is_mac, is_windows, is_x11, _is_system


def test_which_os() -> None:
    """Test the OS checking functions"""
    assert is_mac() or is_windows() or is_x11()
    assert not _is_system("Android")


def test_file() -> None:
    """Test the File class"""
    ff = File(lambda: None, lambda: None)
    ff.filename = "dummy.txt"
    assert ff.filename == "dummy.txt"


def test_preferences() -> None:
    """Test the Preferences class"""
    # Accessing a pref that doesn't have a default should raise an AssertionError
    try:
        preferences.get_default(PrefKey.ROOT_GEOMETRY)
        raise RuntimeError("Failed to trap lack of preferences default")
    except AssertionError:
        pass
    preferences.set_default(PrefKey.ROOT_GEOMETRY, "TestGeometry")
    assert preferences.get_default(PrefKey.ROOT_GEOMETRY) == "TestGeometry"
    preferences.set(PrefKey.ROOT_GEOMETRY, "OtherGeometry")
    assert preferences.get(PrefKey.ROOT_GEOMETRY) == "OtherGeometry"
    assert preferences.get_default(PrefKey.ROOT_GEOMETRY) == "TestGeometry"
    keys = preferences.keys()
    assert len(keys) == 1
    assert PrefKey.ROOT_GEOMETRY in keys
    preferences.set_default(PrefKey.AUTO_IMAGE, True)
    keys = preferences.keys()
    assert len(keys) == 2
    assert PrefKey.ROOT_GEOMETRY in keys
    assert PrefKey.AUTO_IMAGE in keys


def test_mainwindow() -> None:
    """Test mainwindow functions"""
    (tilde, text) = process_label("~Save...")
    assert tilde == 0
    assert text == "Save..."
    (tilde, text) = process_label("Save ~As...")
    assert tilde == 5
    assert text == "Save As..."
    (accel, event) = process_accel("Ctrl+X")
    assert accel == "Ctrl+X"
    assert event == "<Control-X>"
    (accel, event) = process_accel("Cmd/Ctrl+y")
    if is_mac():
        assert accel == "Cmd+y"
        assert event == "<Meta-y>"
        (accel, event) = process_accel("Cmd+?")
        assert accel == "Cmd+?"
        assert event == "<Command-?>"
    else:
        assert accel == "Ctrl+y"
        assert event == "<Control-y>"
    (accel, event) = process_accel("Shift+Ctrl+Z")
    assert accel == "Shift+Ctrl+Z"
    assert event == "<Shift-Control-Z>"
