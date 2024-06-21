"""Common code/classes relating to Tk widgets."""

import tkinter as tk
from tkinter import ttk
from typing import Any, Optional, TypeVar, Callable

import regex as re

from guiguts.preferences import preferences, PrefKey
from guiguts.utilities import is_windows, is_mac, process_accel

NUM_HISTORY = 10


TlDlg = TypeVar("TlDlg", bound="ToplevelDialog")


class ToplevelDialog(tk.Toplevel):
    """Basic dialog with a frame - to avoid duplicated code.

    Dialogs inheriting from ToplevelDialog can add widgets inside
    `self.frame` (which resizes with the dialog)

    Attributes:
        top_frame: Frame widget in grid(0,0) position to contain widgets.
    """

    # Dictionary of ToplevelDialog objects, keyed by class name.
    # Used to ensure only one instance of any dialog is created.
    _toplevel_dialogs: dict[str, "ToplevelDialog"] = {}

    def __init__(
        self,
        title: str,
        resize_x: bool = True,
        resize_y: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize the dialog.

        Args:
            title: Dialog title.
            resize_x: True(default) to allow resizing and remembering of the dialog width.
            resize_y: True(default) to allow resizing and remembering of the dialog height.
        """
        super().__init__(**kwargs)
        self.bind("<Escape>", lambda event: self.destroy())
        self.title(title)
        self.resizable(resize_x, resize_y)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.top_frame: ttk.Frame = ttk.Frame(self, padding=5)
        self.top_frame.columnconfigure(0, weight=1)
        self.top_frame.rowconfigure(0, weight=1)
        self.top_frame.grid(row=0, column=0, sticky="NSEW")

        self._do_config()
        self.save_config = False
        self.bind("<Configure>", self._handle_config)

        self.tooltip_list: list[ToolTip] = []
        self.bind("<Destroy>", self.tidy_up)

        grab_focus(self)

    @classmethod
    def show_dialog(
        cls: type[TlDlg],
        title: Optional[str] = None,
        destroy: bool = False,
        **kwargs: Any,
    ) -> TlDlg:
        """Show the instance of this dialog class, or create it if it doesn't exist.

        Args:
            title: Dialog title.
            destroy: True (default is False) if dialog should be destroyed & re-created, rather than re-used
            args: Optional args to pass to dialog constructor.
            kwargs: Optional kwargs to pass to dialog constructor.
        """
        # If dialog exists, either destroy it or deiconify
        dlg_name = cls.__name__
        if dlg := cls.get_dialog():
            if destroy:
                dlg.destroy()
            else:
                dlg.deiconify()
        # Now, if dialog doesn't exist (may have been destroyed above) (re-)create it
        if not cls.get_dialog():
            if title is not None:
                ToplevelDialog._toplevel_dialogs[dlg_name] = cls(title, **kwargs)  # type: ignore[call-arg]
            else:
                ToplevelDialog._toplevel_dialogs[dlg_name] = cls(**kwargs)  # type: ignore[call-arg]
        return ToplevelDialog._toplevel_dialogs[dlg_name]  # type: ignore[return-value]

    @classmethod
    def get_dialog(cls) -> Optional[TlDlg]:
        """Return the one occurrence of this dialog class if it exists.

        Returns:
            The one instance of this dialog type, or None if it's not currently shown.
        """
        dlg_name = cls.__name__
        if (
            dlg_name in ToplevelDialog._toplevel_dialogs
            and ToplevelDialog._toplevel_dialogs[dlg_name].winfo_exists()
        ):
            return ToplevelDialog._toplevel_dialogs[dlg_name]  # type: ignore[return-value]
        return None

    def register_tooltip(self, tooltip: "ToolTip") -> None:
        """Register a tooltip as being attached to a widget in this
        TopleveDialog so it can be destroyed when the dialog is destroyed.

        Args:
            tooltip - the ToolTip widget to register"""
        self.tooltip_list.append(tooltip)

    def key_bind(self, accel: str, handler: Callable[[], None]) -> None:
        """Convert given accelerator string to a key-event string and bind both
        the upper & lower case versions to the given handler.

        Args:
            accel: Accelerator string, e.g. "Cmd/Ctrl+Z", to trigger call to ``handler``.
            handler: Callback function to be bound to ``accel``.
        """
        _, key_event = process_accel(accel)
        lk = re.sub("[A-Z]>?$", lambda m: m.group(0).lower(), key_event)
        self.bind(lk, lambda _: handler())
        uk = re.sub("[a-z]>?$", lambda m: m.group(0).upper(), key_event)
        self.bind(uk, lambda _: handler())

    def tidy_up(self, event: tk.Event) -> None:
        """Tidy up when the dialog is destroyed.

        Calls the reset method which may be overridden.

        Args:
            event: identifies the widget being destroyed.
        """
        # Since this method is bound to the "<Destroy>" event on the dialog,
        # it will also be called for all child widgets - ignore them.
        if not issubclass(type(event.widget), ToplevelDialog):
            return
        for tooltip in self.tooltip_list:
            tooltip.destroy()
        self.tooltip_list = []

    def reset(self) -> None:
        """Reset the dialog.

        Can be overridden if derived dialog creates marks, tags, etc., that need removing.
        """

    @classmethod
    def close_all(cls) -> None:
        """Close all open ToplevelDialogs."""
        for dlg in ToplevelDialog._toplevel_dialogs.values():
            if dlg.winfo_exists():
                dlg.destroy()
                dlg.reset()
        ToplevelDialog._toplevel_dialogs.clear()

    def _do_config(self) -> None:
        """Configure the geometry of the ToplevelDialog.

        It is not possible (easy) to set just the width or height at this point,
        since the `geometry` method doesn't support that, and the current width
        or height can't be queried (in order to keep it unchanged) because the
        dialog doesn't yet have any contents, so no size.

        The parent dialog is responsible for setting the size correctly in the
        case of a dialog that is only resizable in one direction, using
        the config_width/config_height methods.
        """
        if geometry := self._get_pref_geometry():
            x_resize, y_resize = self.resizable()
            if not (x_resize and y_resize):
                geometry = re.sub(r"^\d+x\d+", "", geometry)
            self.geometry(geometry)

    def config_width(self) -> None:
        """Configure the width of the dialog to the Prefs value,
        leaving the height unchanged.

        This must not be called until all the contents of the dialog have
        been created & managed.
        """
        if geometry := self._get_pref_geometry():
            width = re.sub(r"x.+", "", geometry)
            self.update()  # Needed before querying geometry
            new_geometry = re.sub(r"^\d+", width, self.geometry())
            self.geometry(new_geometry)

    def config_height(self) -> None:
        """Configure the height of the dialog to the Prefs value,
        leaving the width unchanged.

        This must not be called until all the contents of the dialog have
        been created & managed.
        """
        if geometry := self._get_pref_geometry():
            height = re.sub(r"^\d+x(\d+).+", r"\1", geometry)
            self.update()  # Needed before querying geometry
            new_geometry = re.sub(r"(?<=x)\d+", height, self.geometry())
            self.geometry(new_geometry)

    def _get_pref_geometry(self) -> str:
        """Get preferred dialog geometry from Prefs file.

        Returns:
            String containing geometry, or empty string if none stored.
        """
        config_dict = preferences.get(PrefKey.DIALOG_GEOMETRY)
        try:
            return config_dict[self.__class__.__name__]
        except KeyError:
            return ""

    def _handle_config(self, _event: tk.Event) -> None:
        """Callback from dialog <Configure> event.

        By setting flag now, and queuing calls to _save_config,
        we ensure the flag will be true for the first call to
        _save_config when process becomes idle."""
        self.save_config = True
        self.after_idle(self._save_config)

    def _save_config(self) -> None:
        """Only save geometry when process becomes idle.

        Several calls to this may be queued by config changes during
        dialog creation and resizing. Only the first will actually
        do a save, because the flag will only be true on the first call."""
        if self.save_config:
            config_dict = preferences.get(PrefKey.DIALOG_GEOMETRY)
            key = self.__class__.__name__
            config_dict[key] = self.geometry()
            preferences.set(PrefKey.DIALOG_GEOMETRY, config_dict)
            self.save_config = False


class OkApplyCancelDialog(ToplevelDialog):
    """A ToplevelDialog with OK, Apply & Cancel buttons."""

    def __init__(self, title: str, **kwargs: Any) -> None:
        """Initialize the dialog."""
        super().__init__(title, **kwargs)
        button_frame = ttk.Frame(self, padding=5)
        button_frame.grid(row=1, column=0, sticky="NSEW")
        button_frame.columnconfigure(0, weight=1)
        ok_button = ttk.Button(
            button_frame, text="OK", default="active", command=self.ok_pressed
        )
        ok_button.grid(row=0, column=0)
        button_frame.columnconfigure(1, weight=1)
        apply_button = ttk.Button(
            button_frame, text="Apply", default="normal", command=self.apply_changes
        )
        apply_button.grid(row=0, column=1)
        button_frame.columnconfigure(2, weight=1)
        cancel_button = ttk.Button(
            button_frame, text="Cancel", default="normal", command=self.cancel_pressed
        )
        cancel_button.grid(row=0, column=2)
        self.bind("<Return>", lambda event: self.ok_pressed())
        self.bind("<Escape>", lambda event: self.cancel_pressed())

    def apply_changes(self) -> bool:
        """Complete processing needed when Apply/OK are pressed, e.g. storing
        dialog values in persistent variables.

        Will usually be overridden.

            Returns:
                True if OK to close dialog, False if not
        """
        return True

    def ok_pressed(self) -> None:
        """Apply changes and destroy dialog."""
        if self.apply_changes():
            self.destroy()

    def cancel_pressed(self) -> None:
        """Destroy dialog."""
        self.destroy()


class Combobox(ttk.Combobox):
    """A ttk Combobox with some convenience functions.

    Attributes:
        prefs_key: Key to saved history in prefs.
    """

    def __init__(
        self, parent: tk.Widget, prefs_key: PrefKey, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(parent, *args, **kwargs)
        self.prefs_key = prefs_key
        self["values"] = preferences.get(self.prefs_key)
        # If user selects value from dropdown, add it to top of history list
        self.bind("<<ComboboxSelected>>", lambda *_: self.add_to_history(self.get()))

    def add_to_history(self, string: str) -> None:
        """Store given string in history list.

        Stores string in prefs as well as widget drop-down.

        Args:
            string: String to add to list.
        """
        if string:
            history = preferences.get(self.prefs_key)
            try:
                history.remove(string)
            except ValueError:
                pass  # OK if string wasn't in list
            history.insert(0, string)
            del history[NUM_HISTORY:]
            preferences.set(self.prefs_key, history)
            self["values"] = history

    def display_latest_value(self) -> None:
        """Display most recent value (if any) from history list."""
        try:
            self.current(0)
        except tk.TclError:
            self.set("")


class ToolTip(tk.Toplevel):
    """Create a tooltip for a widget."""

    def __init__(
        self,
        widget: tk.Widget,
        msg: str,
        use_pointer_pos: bool = False,
    ):
        """Create a ToolTip.

        Args:
            widget : The widget this tooltip is assigned to
            msg : String to display in tooltip
        """
        self.widget = widget
        self.widget_tl = widget.winfo_toplevel()

        if issubclass(type(self.widget_tl), ToplevelDialog):
            self.widget_tl.register_tooltip(self)  # type: ignore[union-attr]
        self.use_pointer_pos = use_pointer_pos

        tk.Toplevel.__init__(self)
        self.overrideredirect(True)
        # Make tooltip transparent initially, in case it appears briefly on startup
        self.wm_attributes("-alpha", 0.0)
        # Move it off screen too - belt & suspenders - "alpha" not necessarily supported
        self.geometry("+-1000+-1000")

        self.delay = 0.5
        self.inside = False
        frame = ttk.Frame(self, borderwidth=1, relief=tk.SOLID)
        frame.grid(padx=1, pady=1)
        ttk.Label(frame, text=msg).grid()
        self.enter_bind = self.widget.bind("<Enter>", self.on_enter, add="+")
        self.leave_bind = self.widget.bind("<Leave>", self.on_leave, add="+")
        self.press_bind = self.widget.bind("<ButtonRelease>", self.on_leave, add="+")
        self.update_idletasks()
        self.width = self.winfo_width()
        self.height = self.winfo_height()
        # Hide tooltip then make non-transparent for later use
        self.withdraw()
        self.wm_attributes("-alpha", 1.0)

    def on_enter(self, _event: tk.Event) -> None:
        """When mouse enters widget, prepare to show tooltip"""
        self.inside = True
        self.after(int(self.delay * 1000), self._show)

    def on_leave(self, _event: tk.Event | None = None) -> None:
        """Hides tooltip when mouse leaves, or button pressed."""
        self.inside = False
        self.withdraw()

    def _show(self) -> None:
        """Displays the ToolTip if mouse still inside."""
        if not (self.inside and self.widget.winfo_exists()):
            return
        if self.use_pointer_pos:
            x_pos = self.widget.winfo_pointerx() + 20
            y_pos = self.widget.winfo_pointery() + 10
        else:
            # Attempt to center horizontally below widget
            x_pos = self.widget.winfo_rootx() + int(
                self.widget.winfo_width() / 2 - self.width / 2
            )
            y_pos = self.widget.winfo_rooty() + int(self.widget.winfo_height())
            # Keep tooltip just inside main window/dialog horizontal limits
            x_pos = max(x_pos, self.widget_tl.winfo_rootx())
            if (
                x_pos + self.width
                > self.widget_tl.winfo_rootx() + self.widget_tl.winfo_width()
            ):
                x_pos = (
                    self.widget_tl.winfo_rootx()
                    + self.widget_tl.winfo_width()
                    - self.width
                )
            # If tooltip would go below bottom of main window or dialog
            # that widget is in, display it above the widget instead
            if (
                y_pos + self.height
                > self.widget_tl.winfo_rooty() + self.widget_tl.winfo_height()
            ):
                y_pos = self.widget.winfo_rooty() - self.height
        self.geometry(f"+{x_pos}+{y_pos}")
        self.deiconify()

    def destroy(self) -> None:
        """Destroy the ToolTip and unbind all the bindings."""
        if self.widget.winfo_exists():
            unbind_from(self.widget, "<Enter>", self.enter_bind)
            unbind_from(self.widget, "<Leave>", self.leave_bind)
            unbind_from(self.widget, "<ButtonPress>", self.press_bind)
        super().destroy()


def unbind_from(widget: tk.Widget, sequence: str, func_id: str) -> None:
    """Unbind function associated with `func_id`.

    Based on: https://github.com/python/cpython/issues/75666#issuecomment-1877547466
    Necessary due to long-standing bug in tkinter: when `unbind` is called
    all bindings to the given sequence are removed, not just the one associated
    with `func_id`.

    Args:
        widget: Widget to unbind from.
        sequence: Sequence function was bound to.
        func_id: Function ID returned by original `bind` call.
    """
    # Make dictionary of all bindings to seq
    bindings = {
        x.split()[1][3:]: x for x in widget.bind(sequence).splitlines() if x.strip()
    }
    # Delete the binding associated with `funcid`
    del bindings[func_id]
    # Re-bind all the other bindings to the widget
    widget.bind(sequence, "\n".join(list(bindings.values())))


def grab_focus(
    toplevel: tk.Toplevel | tk.Tk,
    focus_widget: Optional[tk.Widget] = None,
    icon_deicon: bool = False,
) -> None:
    """Arcane calls to force window manager to put toplevel window
    to the front and make it active, optionally setting focus to
    specific widget.

    Args:
        toplevel: Toplevel widget to receive focus
        focus_widget: Optional widget within the toplevel tree to take keyboard focus
        icon_deicon: True if iconify/deiconify Windows hack required
    """
    toplevel.lift()
    if icon_deicon and is_windows():
        toplevel.iconify()
        toplevel.deiconify()
    toplevel.focus_force()
    if focus_widget is not None:
        focus_widget.focus_set()


def mouse_bind(
    widget: tk.Widget, event: str, callback: Callable[[tk.Event], object]
) -> None:
    """Bind mouse button callback to event on widget.

    If binding is to mouse button 3, then if on a Mac bind
    to mouse-2 and Control-mouse-1 instead

    Args:
        widget: Widget to bind to
        event: Event string to trigger callback
        callback: Function to be called when event occurs
    """
    assert not event.startswith("<") and not event.endswith(">")
    # Convert to event format and handle Cmd/Ctrl
    _, event = process_accel(event)

    if is_mac() and (match := re.match(r"<(.*)3>", event)):
        button2 = f"<{match[1]}2>"
        widget.bind(button2, callback)
        control1 = f"<Control-{match[1]}1>"
        widget.bind(control1, callback)
    else:
        widget.bind(event, callback)


# For convenient access, store the single Style instance here,
# with a function to set/query it.
# Also store the default colors for a Text widget that are not
# altered by the default themes.
_single_style = None  # pylint: disable=invalid-name
_theme_default_text_bg = ""  # pylint: disable=invalid-name
_theme_default_text_fg = ""  # pylint: disable=invalid-name
_theme_default_text_ibg = ""  # pylint: disable=invalid-name


def themed_style(style: Optional[ttk.Style] = None) -> ttk.Style:
    """Store and return the single Style object"""
    global _single_style
    if style is not None:
        assert _single_style is None
        _single_style = style
        _theme_init_tk_widget_colors()
    assert _single_style is not None
    return _single_style


def theme_name_internal_from_user(user_theme: str) -> str:
    """Return the internal theme name given the name the user will see.

    Args:
        user_theme: Name user will see in Preferences dialog.

    Returns:
        Internal name for theme.
    """
    match user_theme:
        case "Default":
            if is_mac():
                return "aqua"
            if is_windows():
                return "vista"
            return "default"
        case "Dark":
            return "awdark"
        case "Light":
            return "awlight"
        case _:
            assert False, "Bad user theme name"


def theme_set_tk_widget_colors(widget: tk.Text) -> None:
    """Set bg & fg colors of a Text (non-themed) widget to match
    the theme colors.

    Args:
        widget: The widget to be customized.
    """
    theme_name = preferences.get(PrefKey.THEME_NAME)
    if theme_name == "Dark":
        widget.configure(
            background="black", foreground="white", insertbackground="white"
        )
    elif theme_name == "Light":
        widget.configure(
            background="white", foreground="black", insertbackground="black"
        )
    elif theme_name == "Default":
        widget.configure(
            background=_theme_default_text_bg,
            foreground=_theme_default_text_fg,
            insertbackground=_theme_default_text_ibg,
        )


def _theme_init_tk_widget_colors() -> None:
    """Get default bg & fg colors of a Text (non-themed) widget,
    by creating one, getting the colors, then destroying it.

    Needs to be called before theme is changed from default theme.
    """
    global _theme_default_text_bg, _theme_default_text_fg, _theme_default_text_ibg
    temp_text = tk.Text()
    _theme_default_text_bg = temp_text.cget("background")
    _theme_default_text_fg = temp_text.cget("foreground")
    _theme_default_text_ibg = temp_text.cget("insertbackground")
    temp_text.destroy()


# Keep track of which Text/Entry widget of interest last had focus.
# "Of interest" means those we might want to paste special characters into,
# e.g. main text widget, search dialog fields, etc., but not the entry field
# in dialogs like Compose Sequence (which are used to create the special
# characters).

_text_focus_widget: Optional[tk.Widget] = None


def register_focus_widget(widget: tk.Entry | tk.Text) -> None:
    """Register a widget as being "of interest", i.e. to track when it gets focus.

    Args:
        widget: The widget whose focus is to be tracked.
    """
    global _text_focus_widget
    assert isinstance(widget, (tk.Entry, tk.Text))

    def set_focus_widget(event: tk.Event) -> None:
        """Store the widget that triggered the event."""
        global _text_focus_widget
        _text_focus_widget = event.widget

    widget.bind("<FocusIn>", set_focus_widget)
    if _text_focus_widget is None:
        _text_focus_widget = widget


def insert_in_focus_widget(string: str) -> None:
    """Insert given string in the text/entry widget of interest that most recently had focus.

    Args:
        string: String to be inserted.
    """
    if _text_focus_widget is None or not _text_focus_widget.winfo_exists():
        return
    assert isinstance(_text_focus_widget, (tk.Entry, tk.Text))
    _text_focus_widget.insert(tk.INSERT, string)
