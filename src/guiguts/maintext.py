"""Handle main text widget"""

import logging
import subprocess
import tkinter as tk
from tkinter import ttk, Text
from tkinter import font as tk_font
from typing import Any, Callable, Optional, Literal, Generator
from enum import auto, StrEnum

import regex as re

from guiguts.preferences import preferences, PrefKey
from guiguts.utilities import (
    is_mac,
    IndexRowCol,
    IndexRange,
    TextWrapper,
)
from guiguts.widgets import (
    theme_set_tk_widget_colors,
    themed_style,
    register_focus_widget,
    grab_focus,
)

logger = logging.getLogger(__package__)

TK_ANCHOR_MARK = "tk::anchor1"
WRAP_NEXT_LINE_MARK = "WrapParagraphStart"
INDEX_END_MARK = "IndexEnd"
INDEX_NEXT_LINE_MARK = "IndexLineStart"
WRAP_END_MARK = "WrapSectionEnd"
PAGE_FLAG_TAG = "PageFlag"
PAGEMARK_PIN = "\x7f"  # Temp char to pin page mark locations
BOOKMARK_TAG = "Bookmark"
PAGEMARK_PREFIX = "Pg"
REPLACE_END_MARK = "ReplaceEnd"
SELECTION_MARK_START = "SelectionMarkStart"
SELECTION_MARK_END = "SelectionMarkEnd"
PEER_MIN_SIZE = 50


class FindMatch:
    """Index and length of match found by search method.

    Attributes:
        index: Index of start of match.
        count: Length of match.
    """

    def __init__(self, index: IndexRowCol, count: int):
        self.rowcol = index
        self.count = count


class WrapParams:
    """Stores parameters used when wrapping."""

    def __init__(self, left: int, first: int, right: int):
        """Initialize WrapParams object.

        Args:
            left: Left margin position.
            first: Left margin for first line.
            right: Right margin position.
        """
        self.left = left
        self.first = first
        self.right = right


class TextColumnNumbers(tk.Text):
    """Ruler to display the column numbers.

    Attributes:
        textwidget: Text widget to provide line numbers for.
        font: Font used by text widget, also used for line numbers.
    """

    def __init__(self, parent: ttk.Frame, text_widget: tk.Text, **kwargs: Any) -> None:
        self.textwidget = text_widget
        self.font = tk_font.nametofont(self.textwidget.cget("font"))
        self._ruler = self._construct_ruler()
        super().__init__(
            parent,
            font=self.font,
            height=1,
            padx=3,
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
            relief=tk.FLAT,
            wrap=tk.NONE,
            **kwargs,
        )
        self.bind("<<ThemeChanged>>", lambda event: self.theme_change())
        self.theme_change()

        # Ignore clicks, mousewheel scrolls, and click-drags on column ruler
        for event in (
            "Button-1",
            "Button-2",
            "Button-3",
            "Button-4",
            "Button-5",
            "ButtonRelease-1",
            "ButtonRelease-2",
            "ButtonRelease-3",
            "ButtonRelease-4",
            "ButtonRelease-5",
            "B1-Motion",
            "B2-Motion",
            "B3-Motion",
            "B4-Motion",
            "B5-Motion",
            "MouseWheel",
        ):
            self.bind(f"<{event}>", lambda x: "break")

    def _construct_ruler(self) -> str:
        """Build a ruler string for re-use later."""
        ruler_stub = ""
        for n in range(9):
            ruler_stub += f"····▾····{n + 1}"
        ruler = ""
        for n in range(5):
            ruler += f"{ruler_stub}····▾···{(n + 1)}0"
        return ruler

    def redraw(self) -> None:
        """Redraw the column ruler."""
        if not preferences.get(PrefKey.COLUMN_NUMBERS):
            return

        if self.textwidget is maintext().peer and not preferences.get(
            PrefKey.SPLIT_TEXT_WINDOW
        ):
            return

        # respond to font size changing
        self.configure(height=1)
        self.tag_remove(HighlightTag.COLUMN_RULER, "1.0", tk.END)
        self.delete("1.0", tk.END)

        if maintext().focus_widget() == self.textwidget:
            cur_bg = self.textwidget["selectbackground"]
        else:
            cur_bg = self.textwidget["inactiveselectbackground"]
        self.tag_configure(HighlightTag.COLUMN_RULER, background=cur_bg)

        # Draw the ruler to be the length of the longest line in the viewport.
        # If the longest line is narrower than the viewport, then pad it to be
        # wide enough to fill the viewport width.
        longest_line = self.longest_line()
        width = int(self.textwidget.winfo_width() / self.font.measure("8"))
        rulerlen = max(width, longest_line)
        self.insert("1.0", self._ruler[:rulerlen])

        # Highlight the current column (unless we're at column 0)
        cur_col = IndexRowCol(self.textwidget.index(tk.INSERT)).col
        if cur_col:
            self.tag_add(HighlightTag.COLUMN_RULER, f"1.{cur_col - 1}")

    def longest_line(self) -> int:
        """Look at lines in the current text widget's viewport and
        return length (in characters) of the longest one."""
        longest = 0
        index = self.textwidget.index("@0,0")
        while True:
            dline = self.textwidget.dlineinfo(index)
            if dline is None:
                break
            linelen = len(self.textwidget.get(f"{index} linestart", f"{index} lineend"))
            longest = max(longest, linelen)
            index = self.textwidget.index(index + "+1l")
        return longest

    def theme_change(self) -> None:
        """Handle change of color theme"""
        self.configure(
            background=themed_style().lookup("TButton", "background"),
            foreground=themed_style().lookup("TButton", "foreground"),
        )


class TextLineNumbers(tk.Canvas):
    """TextLineNumbers widget adapted from answer at
    https://stackoverflow.com/questions/16369470/tkinter-adding-line-number-to-text-widget

    Attributes:
        textwidget: Text widget to provide line numbers for.
        font: Font used by text widget, also used for line numbers.
        offset: Gap between line numbers and text widget.
    """

    def __init__(
        self,
        parent: tk.Widget,
        text_widget: tk.Text,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.textwidget = text_widget
        self.font = tk_font.nametofont(self.textwidget.cget("font"))
        self.x_offset = 10
        # Allow for non-zero additional line spacing in text widget
        self.y_offset = self.textwidget["spacing1"]
        tk.Canvas.__init__(self, parent, *args, highlightthickness=0, **kwargs)
        # Canvas needs to listen for theme change
        self.bind("<<ThemeChanged>>", lambda event: self.theme_change())
        self.text_color = themed_style().lookup("TButton", "foreground")

    def redraw(self) -> None:
        """Redraw line numbers."""
        # Allow for 5 digit line numbers
        width = self.font.measure("88888") + self.x_offset + 5
        self["width"] = width
        self.delete("all")
        cur_line = IndexRowCol(self.textwidget.index(tk.INSERT)).row
        if maintext().focus_widget() == self.textwidget:
            cur_bg = self.textwidget["selectbackground"]
        else:
            cur_bg = self.textwidget["inactiveselectbackground"]
        text_pos = self.winfo_width() - self.x_offset
        line_spacing_adj = int(self.textwidget["spacing1"])
        index = self.textwidget.index("@0,0")
        while True:
            dline = self.textwidget.dlineinfo(index)
            if dline is None:
                break
            linenum = IndexRowCol(index).row
            text = self.create_text(
                text_pos,
                dline[1] + self.y_offset,
                anchor="ne",
                font=self.font,
                text=linenum,
                fill=self.text_color,
            )
            # Highlight the line number of the current line
            if linenum == cur_line:
                bbox = list(self.bbox(text))
                rect = self.create_rectangle(
                    (
                        3,
                        bbox[1] - line_spacing_adj,
                        bbox[2] + self.x_offset - 3,
                        bbox[3],
                    ),
                    fill=cur_bg,
                    width=0,
                )
                self.tag_lower(rect, text)
            index = self.textwidget.index(index + "+1l")

    def theme_change(self) -> None:
        """Handle change of color theme"""
        self.configure(background=themed_style().lookup("TButton", "background"))
        self.text_color = themed_style().lookup("TButton", "foreground")


class HighlightTag(StrEnum):
    """Global highlight tag settings."""

    QUOTEMARK = auto()
    SPOTLIGHT = auto()
    PAREN = auto()
    CURLY_BRACKET = auto()
    SQUARE_BRACKET = auto()
    STRAIGHT_DOUBLE_QUOTE = auto()
    CURLY_DOUBLE_QUOTE = auto()
    STRAIGHT_SINGLE_QUOTE = auto()
    CURLY_SINGLE_QUOTE = auto()
    ALIGNCOL = auto()
    CURSOR_LINE = auto()
    COLUMN_RULER = auto()


class HighlightColors:
    """Global highlight color settings."""

    # Possible future enhancement:
    #
    # GG1 allowed you to set three colors:
    # - Background color (text areas, text inputs, main editor textarea)
    # - Button highlight color (hover on buttons, checkboxes, radio selects)
    # - Scanno/quote highlight color (which would apply here)
    #
    # Unclear what we should/will do in GG2 with themes & dark mode support.

    # Must be a definition for each available theme
    QUOTEMARK = {
        "Light": {"bg": "#a08dfc", "fg": "black"},
        "Dark": {"bg": "darkmagenta", "fg": "white"},
    }

    SPOTLIGHT = {
        "Light": {"bg": "orange", "fg": "black"},
        "Dark": {"bg": "darkorange", "fg": "white"},
    }

    PAREN = {
        "Light": {"bg": "violet", "fg": "white"},
        "Dark": {"bg": "mediumpurple", "fg": "white"},
    }

    CURLY_BRACKET = {
        "Light": {"bg": "blue", "fg": "white"},
        "Dark": {"bg": "blue", "fg": "white"},
    }

    SQUARE_BRACKET = {
        "Light": {"bg": "purple", "fg": "white"},
        "Dark": {"bg": "purple", "fg": "white"},
    }

    STRAIGHT_DOUBLE_QUOTE = {
        "Light": {"bg": "green", "fg": "white"},
        "Dark": {"bg": "green", "fg": "white"},
    }

    CURLY_DOUBLE_QUOTE = {
        "Light": {"bg": "limegreen", "fg": "white"},
        "Dark": {"bg": "teal", "fg": "white"},
    }

    STRAIGHT_SINGLE_QUOTE = {
        "Light": {"bg": "grey", "fg": "white"},
        "Dark": {"bg": "sienna", "fg": "white"},
    }

    CURLY_SINGLE_QUOTE = {
        "Light": {"bg": "dodgerblue", "fg": "white"},
        "Dark": {"bg": "#b23e0c", "fg": "white"},
    }

    ALIGNCOL = {
        "Light": {"bg": "greenyellow", "fg": "black"},
        "Dark": {"bg": "green", "fg": "white"},
    }

    CURSOR_LINE = {
        "Light": {"bg": "#efefef", "fg": "black"},
        "Dark": {"bg": "#303030", "fg": "white"},
    }

    COLUMN_RULER = {
        "Light": {"bg": "#A6CDFF", "fg": "black"},
        "Dark": {"bg": "#324F78", "fg": "white"},
    }


class TextPeer(tk.Text):
    """A peer of maintext's text widget.

    Note that tk.Text.peer_create() doesn't work properly, creating a tk widget, but
    not an tkinter instance of tk.Text. Hence the need for this:
    https://stackoverflow.com/questions/58286794 - see top answer
    """

    def __init__(  # pylint: disable=super-init-not-called
        self, main_text: "MainText"
    ) -> None:
        """Create a peer & a tkinter widget based on the peer."""
        main_text.tk.call(main_text, "peer", "create", f"{main_text.peer_frame}.peer")
        tk.BaseWidget._setup(self, main_text.peer_frame, {"name": "peer"})  # type: ignore[attr-defined]


class MainText(tk.Text):
    """MainText is the main text window, and inherits from ``tk.Text``."""

    def __init__(self, parent: tk.PanedWindow, root: tk.Tk, **kwargs: Any) -> None:
        """Create a Frame, and put a TextLineNumbers widget, a Text and two
        Scrollbars in the Frame.

        Layout and linking of the TextLineNumbers widget and Scrollbars to
        the Text widget are done here.

        Args:
            parent: Parent widget to contain MainText.
            root: Tk root.
            **kwargs: Optional additional keywords args for ``tk.Text``.
        """

        self.root = root
        self.paned_text_window = parent

        # Create surrounding Frame
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(1, weight=1)

        # Set up font
        family = preferences.get(PrefKey.TEXT_FONT_FAMILY)
        # If preference has never been set, then choose one of the preferred fonts
        if not family:
            families = tk_font.families()
            for pref_font in (
                "DP Sans Mono",
                "DejaVu Sans Mono",
                "Courier New",
                "Courier",
            ):
                if pref_font in families:
                    family = pref_font
                    break
        self.font = tk_font.Font(
            family=family,
            size=preferences.get(PrefKey.TEXT_FONT_SIZE),
        )
        # For some reason line spacing on Mac is very tight, so pad a bit here
        line_spacing = 4 if is_mac() else 0
        # Create Text itself & place in Frame
        super().__init__(self.frame, font=self.font, spacing1=line_spacing, **kwargs)
        tk.Text.grid(self, column=1, row=1, sticky="NSEW")

        self.languages = ""

        # alignment column
        self.aligncol = -1
        self.aligncol_active = tk.BooleanVar()

        # Create Line Numbers widget
        self.linenumbers = TextLineNumbers(self.frame, self)
        self.linenumbers.grid(column=0, row=1, sticky="NSEW")
        self.colnumbers = TextColumnNumbers(self.frame, self)
        self.colnumbers.grid(column=1, row=0, sticky="NSEW")
        self.numbers_need_updating = False

        def hscroll_set(*args: Any) -> None:
            self.hscroll.set(*args)
            self.colnumbers.xview("moveto", args[0])
            self._on_change()

        def vscroll_set(*args: Any) -> None:
            self.vscroll.set(*args)
            self._on_change()

        def hscroll_main_and_ruler(*args: Any) -> None:
            self.xview(*args)
            self.colnumbers.xview(*args)
            self._on_change()

        # Create scrollbars, place in Frame, and link to Text
        self.hscroll = ttk.Scrollbar(
            self.frame, orient=tk.HORIZONTAL, command=hscroll_main_and_ruler
        )
        self.hscroll.grid(column=1, row=2, sticky="EW")
        self.config(xscrollcommand=hscroll_set)
        self.vscroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.yview)
        self.vscroll.grid(column=2, row=1, sticky="NS")
        self.config(yscrollcommand=vscroll_set)

        self.config_callbacks: list[Callable[[], None]] = []

        # Set up response to text being modified
        self.modified_callbacks: list[Callable[[], None]] = []
        self.bind_event(
            "<<Modified>>", lambda _event: self.modify_flag_changed_callback()
        )

        # Set up callbacks when Undo/Redo is executed
        self.undo_redo_callbacks: dict[str, Callable[[], None]] = {}
        self.bind_event(
            "<<Undo>>", lambda _event: self._call_undo_redo_callbacks(), add=True
        )
        self.bind_event(
            "<<Redo>>", lambda _event: self._call_undo_redo_callbacks(), add=True
        )

        self.bind_event("<<Cut>>", lambda _event: self.smart_cut(), force_break=False)
        self.bind_event("<<Copy>>", lambda _event: self.smart_copy(), force_break=False)
        self.bind_event(
            "<<Paste>>", lambda _event: self.smart_paste(), force_break=False
        )
        self.bind_event(
            "<BackSpace>", lambda _event: self.smart_delete(), force_break=False
        )
        self.bind_event(
            "<Delete>", lambda _event: self.smart_delete(), force_break=False
        )

        # Register this widget to have its focus tracked for inserting special characters
        register_focus_widget(self)

        # Configure tags
        self.tag_configure(PAGE_FLAG_TAG, background="gold", foreground="black")
        self.tag_configure(BOOKMARK_TAG, background="lime", foreground="black")

        # Ensure text still shows selected when focus is in another dialog
        if not is_mac() and "inactiveselect" not in kwargs:
            self["inactiveselect"] = "#b0b0b0"

        self.current_sel_ranges: list[IndexRange] = []
        self.prev_sel_ranges: list[IndexRange] = []

        maintext(self)  # Register this single instance of MainText

        # Create peer widget
        self.peer_frame = ttk.Frame()
        self.peer_frame.columnconfigure(1, weight=1)
        self.peer_frame.rowconfigure(1, weight=1)
        self.peer = TextPeer(self)
        self.peer.grid(column=1, row=1, sticky="NSEW")

        # Configure peer widget using main text as a template
        self.peer.config(
            font=self.font,
            highlightthickness=self["highlightthickness"],
            spacing1=self["spacing1"],
            inactiveselectbackground=self["inactiveselectbackground"],
            wrap=self["wrap"],
        )
        self.peer.bind(
            "<<ThemeChanged>>", lambda _event: theme_set_tk_widget_colors(self.peer)
        )
        theme_set_tk_widget_colors(self.peer)

        self.peer_linenumbers = TextLineNumbers(self.peer_frame, self.peer)
        self.peer_linenumbers.grid(column=0, row=1, sticky="NSEW")
        self.peer_colnumbers = TextColumnNumbers(self.peer_frame, self.peer)
        self.peer_colnumbers.grid(column=1, row=0, sticky="NSEW")

        def peer_hscroll_set(*args: Any) -> None:
            self.peer_hscroll.set(*args)
            self.peer_colnumbers.xview("moveto", args[0])
            self._on_change()

        def peer_vscroll_set(*args: Any) -> None:
            self.peer_vscroll.set(*args)
            self._on_change()

        def hscroll_peer_and_ruler(*args: Any) -> None:
            self.peer.xview(*args)
            self.peer_colnumbers.xview(*args)
            self._on_change()

        # Create peer scrollbars, place in Frame, and link to peer Text
        self.peer_hscroll = ttk.Scrollbar(
            self.peer_frame, orient=tk.HORIZONTAL, command=hscroll_peer_and_ruler
        )
        self.peer_hscroll.grid(column=1, row=2, sticky="EW")
        self.peer["xscrollcommand"] = peer_hscroll_set
        self.peer_vscroll = ttk.Scrollbar(
            self.peer_frame, orient=tk.VERTICAL, command=self.peer.yview
        )
        self.peer_vscroll.grid(column=2, row=1, sticky="NS")
        self.peer["yscrollcommand"] = peer_vscroll_set

        self._text_peer_focus: tk.Text = self

        # Track whether main text or peer most recently had focus
        def text_peer_focus_track(event: tk.Event) -> None:
            assert event.widget in (self, self.peer)
            self._text_peer_focus = event.widget  # type: ignore[assignment]

        self.bind_event("<FocusIn>", text_peer_focus_track, add=True, bind_peer=True)

        # Register peer widget to have its focus tracked for inserting special characters
        register_focus_widget(self.peer)

        self.paned_text_window.add(maintext().frame, minsize=PEER_MIN_SIZE)

        # Bindings that both peer and maintext need
        def switch_text_peer(event: tk.Event) -> None:
            """Switch focus between main text and peer widget"""
            if event.widget == self and preferences.get(PrefKey.SPLIT_TEXT_WINDOW):
                self.peer.focus()
            else:
                self.focus()

        self.bind_event("<Tab>", switch_text_peer, bind_peer=True)

        # Column selection uses Alt key on Windows/Linux, Option key on macOS
        # Key Release is reported as Alt_L on all platforms
        modifier = "Option" if is_mac() else "Alt"
        self.bind_event(
            f"<{modifier}-ButtonPress-1>", self.column_select_click, bind_peer=True
        )
        self.bind_event(
            f"<{modifier}-B1-Motion>", self.column_select_motion, bind_peer=True
        )
        self.bind_event(
            f"<{modifier}-ButtonRelease-1>",
            self.column_select_release,
            bind_peer=True,
            add=True,
        )
        self.bind_event("<KeyRelease-Alt_L>", self.column_select_stop, bind_peer=True)
        # Make use of built-in Shift click functionality to extend selections,
        # but adapt for column select with Option/Alt key
        self.bind_event(
            f"<Shift-{modifier}-ButtonPress-1>",
            self.column_select_release,
            bind_peer=True,
        )
        self.bind_event(
            f"<Shift-{modifier}-ButtonRelease-1>",
            lambda _event: "break",
            bind_peer=True,
        )
        self.column_selecting = False

        # Override default left/right/up/down arrow key behavior if there is a selection
        # Above behavior would affect Shift-Left/Right/Up/Down, so also bind those to
        # null functions and allow default class behavior to happen
        for arrow in ("Left", "Up"):
            self.bind_event(
                f"<{arrow}>",
                lambda _event: self.move_to_selection_start(),
                force_break=False,
                bind_peer=True,
            )
            self.bind_event(
                f"<Shift-{arrow}>", lambda _event: "", force_break=False, bind_peer=True
            )
        for arrow in ("Right", "Down"):
            self.bind_event(
                f"<{arrow}>",
                lambda _event: self.move_to_selection_end(),
                force_break=False,
                bind_peer=True,
            )
            self.bind_event(
                f"<Shift-{arrow}>", lambda _event: "", force_break=False, bind_peer=True
            )
        # Mac also has Emacs-style equivalent to arrow keys - don't override uppercase versions
        if is_mac():
            for key in ("b", "p"):
                self.bind_event(
                    f"<Control-{key}>",
                    lambda _event: self.move_to_selection_start(),
                    force_break=False,
                    bind_peer=True,
                )
            for key in ("f", "n"):
                self.bind_event(
                    f"<Control-{key}>",
                    lambda _event: self.move_to_selection_end(),
                    force_break=False,
                    bind_peer=True,
                )
            # Control-A/E & Cmd+left/right move to start/end of line on Macs
            # so go to start/end of line that contains selection start/end
            for key in ("<Control-a>", "<Command-Left>"):
                self.bind_event(
                    key,
                    lambda _event: self.move_to_selection_start(force_line=True),
                    force_break=False,
                    bind_peer=True,
                )
            for key in ("<Control-e>", "<Command-Right>"):
                self.bind_event(
                    key,
                    lambda _event: self.move_to_selection_end(force_line=True),
                    force_break=False,
                    bind_peer=True,
                )

        # Double (word) and triple (line) clicking to select, leaves the anchor point
        # wherever the user clicked, so force it instead to be at the start of the word/line.
        # This has to be done after the default behavior, so via `after_idle`.
        # Also add a dummy event to ensure that shift double/triple clicks continue to
        # exhibit default "extend selection" behavior.
        def dbl_click(_event: tk.Event) -> None:
            self.after_idle(
                lambda: self.mark_set(
                    TK_ANCHOR_MARK, f"{self.index(tk.CURRENT)} wordstart"
                )
            )

        self.bind_event(
            "<Double-Button-1>",
            dbl_click,
            force_break=False,
            bind_peer=True,
        )
        self.bind_event(
            "<Shift-Double-Button-1>",
            lambda _event: "",
            force_break=False,
            bind_peer=True,
        )

        def triple_click(_event: tk.Event) -> None:
            self.after_idle(
                lambda: self.mark_set(
                    TK_ANCHOR_MARK, f"{self.index(tk.CURRENT)} linestart"
                )
            )

        self.bind_event(
            "<Triple-Button-1>",
            triple_click,
            force_break=False,
            bind_peer=True,
        )
        self.bind_event(
            "<Shift-Triple-Button-1>",
            lambda _event: "",
            force_break=False,
            bind_peer=True,
        )

        # Bind line numbers update routine to all events that might
        # change which line numbers should be displayed in maintext and peer
        self.bind_event(
            "<Configure>", self._on_change, add=True, force_break=False, bind_peer=True
        )

        # Intercept key presses that insert a character,
        # because if there is a column selection, we need to clear it.
        def keypress(event: tk.Event) -> None:
            """Clear any column selection on keypress."""
            if len(event.char) > 0 and len(self.selected_ranges()) >= 2:
                self.clear_selection()

        self.bind_event(
            "<KeyPress>", keypress, add=True, force_break=False, bind_peer=True
        )

        # Use KeyRelease not KeyPress since KeyPress might be caught earlier and not propagated to this point.
        self.bind_event(
            "<KeyRelease>", self._on_change, add=True, force_break=False, bind_peer=True
        )
        # Add mouse event here after column selection bindings above
        self.bind_event(
            "<ButtonRelease>",
            self._on_change,
            add=True,
            force_break=False,
            bind_peer=True,
        )
        # Add common Mac key bindings for beginning/end of file
        if is_mac():
            self.bind_event(
                "<Command-Up>", lambda _event: self.move_to_start(), bind_peer=True
            )
            self.bind_event(
                "<Command-Down>", lambda _event: self.move_to_end(), bind_peer=True
            )
            self.bind_event(
                "<Command-Shift-Up>",
                lambda _event: self.select_to_start(),
                bind_peer=True,
            )
            self.bind_event(
                "<Command-Shift-Down>",
                lambda e_event: self.select_to_end(),
                bind_peer=True,
            )

        # Defang some potentially destructive editing keys
        for _key in ("D", "H", "K", "T"):
            self.key_bind(
                f"<Control-{_key}>", lambda _event: self.do_nothing(), bind_all=False
            )
        if is_mac():
            for _key in ("I", "O"):
                self.key_bind(
                    f"<Control-{_key}>",
                    lambda _event: self.do_nothing(),
                    bind_all=False,
                )

        # Since Text widgets don't normally listen to theme changes,
        # need to do it explicitly here.
        self.bind_event(
            "<<ThemeChanged>>", lambda _event: theme_set_tk_widget_colors(self)
        )

        # Fix macOS text selection bug
        #
        # While dragging to select text, when the mouse cursor reaches the edge
        # of the text view, it stops. Desired behavior is to continue selecting,
        # auto-scrolling the view to reach more text. That's common behavior in
        # most apps, and is the default behavior on Windows and Linux.

        if is_mac():
            # Watch mouse click and drag events to detect when user is selecting
            # text via mouse cursor dragging. During drag-to-select, watch the
            # cursor position to detect whether it has left the text widget, and
            # if so, in which direction.
            #
            # - When Button-1 is pressed, activate the drag monitoring mode
            # - When cursor is dragged, run callback to watch cursor position
            # - If cursor leaves the text widget, start scrolling in the
            #   direction it has moved beyond the edge
            # - If cursor returns to within the text widget, stop scrolling
            # - On release of Button-1, deactivate drag monitoring (which
            #   stops running the monitoring callback)
            self.bind_event(
                "<Button-1>", self._autoscroll_start, add=True, bind_peer=True
            )
            self.bind_event(
                "<ButtonRelease-1>", self._autoscroll_stop, add=True, bind_peer=True
            )
            self.bind_event(
                "<B1-Motion>", self._autoscroll_callback, add=True, bind_peer=True
            )

        # Also used for column selection, not just above bug fix, so needed on all platforms
        self._autoscroll_active = False

        # Need to wait until maintext has been registered to set the font preference
        preferences.set(PrefKey.TEXT_FONT_FAMILY, family)

        # Delay showing peer to avoid getting spurious sash positions
        if preferences.get(PrefKey.SPLIT_TEXT_WINDOW):
            self.after_idle(self.show_peer)

        # Force focus to maintext widget
        self.after_idle(lambda: grab_focus(self.root, self, True))

        # Whether we were on dark theme the last time we looked (bool)
        self.dark_theme = self.is_dark_theme()

        # Initialize highlighting tags
        self.after_idle(lambda: self.highlight_configure_tags(first_run=True))

    def do_nothing(self) -> None:
        """The only winning move is not to play."""
        return

    def focus_widget(self) -> tk.Text:
        """Return whether main text or peer last had focus.

        Checks current focus, and if neither, returns the one that had it last.

        Returns:
            Main text widget or peer widget.
        """
        return self._text_peer_focus

    def bind_event(
        self,
        event_string: str,
        func: Callable[[tk.Event], Optional[str]],
        add: bool = False,
        force_break: bool = True,
        bind_all: bool = False,
        bind_peer: bool = False,
    ) -> None:
        """Bind event string to given function. Provides ability to force
        a "break" return in order to stop class binding being executed.

        Args:
            event_string: String describing key/mouse/etc event.
            func: Function to bind to event - may handle return "break" itself.
            add: True to add this binding without removing existing binding.
            force_break: True to always return "break", regardless of return from `func`.
            bind_all: True to bind keystroke to all other widgets as well as maintext
            bind_peer: True to bind keystroke to peer, even if bind_all is False
        """

        def break_func(event: tk.Event) -> Any:
            """Call bound function. Force "break" return if needed."""
            func_ret = func(event)
            return "break" if force_break and not add else func_ret

        self.bind(event_string, break_func, add)
        if bind_all:
            self.bind_all(event_string, break_func, add)
        if bind_peer:
            self.peer.bind(event_string, break_func, add)

    # The following methods are simply calling the Text widget method
    # then updating the linenumbers widget
    def insert(self, index: Any, chars: str, *args: Any) -> None:
        """Override method to ensure line numbers are updated."""
        super().insert(index, chars, *args)
        self._on_change()

    def delete(self, index1: Any, index2: Any = None) -> None:
        """Override method to ensure line numbers are updated."""
        super().delete(index1, index2)
        self._on_change()

    def replace(self, index1: Any, index2: Any, chars: str, *args: Any) -> None:
        """Override method to ensure line numbers are updated.

        Also preserve pagemark locations within the replacement."""
        self._replace_preserving_pagemarks(index1, index2, chars, *args)
        self._on_change()

    def replace_all(
        self, search_str: str, replace_str: str, regexp: bool = False
    ) -> None:
        """Replace all occurrences of search string/regex with replacement.

        Args:
            search_str: Search string.
            replace_str: Replacement string.
        """
        whole_file = IndexRange(self.start(), self.end())
        matches = self.find_matches(search_str, whole_file, nocase=False, regexp=regexp)
        for match in reversed(matches):
            start = match.rowcol.index()
            end = f"{start}+{match.count}c"
            self.replace(start, end, replace_str)

    def mark_set(self, markName: str, index: Any) -> None:
        """Override method to ensure line numbers are updated when insert cursor is moved."""
        super().mark_set(markName, index)
        if markName == tk.INSERT:
            self._on_change()

    def _do_linenumbers_redraw(self) -> None:
        """Only redraw line numbers once when process becomes idle.

        Several calls to this may be queued by _on_change, but only
        the first will actually do a redraw, because the flag will
        only be true on the first call."""
        if self.numbers_need_updating:
            self.numbers_need_updating = False
            self.linenumbers.redraw()
            self.peer_linenumbers.redraw()
            self.colnumbers.redraw()
            self.peer_colnumbers.redraw()

    def add_undo_redo_callback(self, key: str, func: Callable[[], None]) -> None:
        """Add callback function to a list of functions to be called when
        <<Undo>> or <<Redo>> virtual events happen.

        Calling routine has responsibility to remove callback when no longer needed.

        Args:
            key: Key to identify the caller, e.g. dialog class name
            func: Callback function to be added to list.
        """
        self.undo_redo_callbacks[key] = func

    def remove_undo_redo_callback(self, key: str) -> None:
        """Remove callback function from a list of functions to be called when
        <<Undo>> or <<Redo>> virtual events happen.

        Should only be called if callback was added earlier.

        Args:
            key: Key to identify the caller, e.g. dialog class name
        """
        assert key in self.undo_redo_callbacks
        if key in self.undo_redo_callbacks:
            del self.undo_redo_callbacks[key]

    def _call_undo_redo_callbacks(self) -> None:
        """Causes all functions registered via `add_undo_redo_callback`
        to be called after idle."""
        for func in self.undo_redo_callbacks.values():
            self.after_idle(func)

    def add_config_callback(self, func: Callable[[], None]) -> None:
        """Add callback function to a list of functions to be called when
        widget's configuration changes (e.g. width or height).

        Args:
            func: Callback function to be added to list.
        """
        self.config_callbacks.append(func)

    def _call_config_callbacks(self) -> None:
        """Causes all functions registered via `add_config_callback` to be called."""
        for func in self.config_callbacks:
            func()

    def _on_change(self, *_args: Any) -> None:
        """Callback when visible region of file may have changed.

        By setting flag now, and queuing calls to _do_linenumbers_redraw,
        we ensure the flag will be true for the first call to
        _do_linenumbers_redraw."""

        if not self.numbers_need_updating:
            self.root.after_idle(self._do_linenumbers_redraw)
            self.root.after_idle(self._call_config_callbacks)
            self.root.after_idle(self.save_sash_coords)
            # run `highlight_configure_tags` _before_ other highlighters
            self.root.after_idle(self.highlight_configure_tags)
            self.root.after_idle(self.highlight_quotbrac)
            self.root.after_idle(self.highlight_aligncol)
            self.root.after_idle(self.highlight_cursor_line)
            self.numbers_need_updating = True

    def save_sash_coords(self) -> None:
        """Save the splitter sash coords in Prefs."""
        if preferences.get(PrefKey.SPLIT_TEXT_WINDOW):
            preferences.set(
                PrefKey.SPLIT_TEXT_SASH_COORD, self.paned_text_window.sash_coord(0)[1]
            )

    def grid(self, *args: Any, **kwargs: Any) -> None:
        """Override ``grid``, so placing MainText widget actually places surrounding Frame"""
        return self.frame.grid(*args, **kwargs)

    def show_peer(self) -> None:
        """Show the peer text widget in the text's parent's paned window."""
        self.paned_text_window.add(maintext().peer_frame, minsize=PEER_MIN_SIZE)
        sash_coord = preferences.get(PrefKey.SPLIT_TEXT_SASH_COORD)
        if sash_coord:
            self.paned_text_window.sash_place(0, 0, sash_coord)
        preferences.set(PrefKey.SPLIT_TEXT_WINDOW, True)
        self.peer_linenumbers.theme_change()

    def hide_peer(self) -> None:
        """Remove the peer text widget from the text's parent's paned window."""
        self.paned_text_window.remove(maintext().peer_frame)
        preferences.set(PrefKey.SPLIT_TEXT_WINDOW, False)
        self.focus()  # Return focus to the main text.

    def set_font(self) -> None:
        """Set the font for the main text widget, based on the current Prefs values."""
        self.font.config(
            family=preferences.get(PrefKey.TEXT_FONT_FAMILY),
            size=preferences.get(PrefKey.TEXT_FONT_SIZE),
        )

        # On some systems, window isn't updated properly, so temporarily select all
        # then restore selection to force it to update.
        # Also restore after idle, or Linux version doesn't update.
        ranges = self.selected_ranges()
        self.do_select(IndexRange(self.start(), self.end()))
        self.restore_selection_ranges(ranges)
        self.after_idle(lambda: self.restore_selection_ranges(ranges))

    def show_line_numbers(self, show: bool) -> None:
        """Show or hide line numbers.

        Args:
            show: True to show, False to hide.
        """
        if show:
            self.linenumbers.grid()
            self.peer_linenumbers.grid()
        else:
            self.linenumbers.grid_remove()
            self.peer_linenumbers.grid_remove()

    def show_column_numbers(self, show: bool) -> None:
        """Show or hide column numbers.

        Args:
            show: True to show, False to hide.
        """
        if show:
            self.colnumbers.grid()
            self.peer_colnumbers.grid()
        else:
            self.colnumbers.grid_remove()
            self.peer_colnumbers.grid_remove()

    def key_bind(
        self, keyevent: str, handler: Callable[[Any], None], bind_all: bool
    ) -> None:
        """Bind lower & uppercase versions of ``keyevent`` to ``handler``
        in main text window, and all other widgets.

        If this is not done, then use of Caps Lock key causes confusing
        behavior, because pressing ``Ctrl`` and ``s`` sends ``Ctrl+S``.

        Args:
            keyevent: Key event to trigger call to ``handler``.
            handler: Callback function to be bound to ``keyevent``.
            bind_all: True to bind keystroke to all other widgets as well as maintext
        """
        lk = re.sub("(?<=[^A-Za-z])[A-Z]>$", lambda m: m.group(0).lower(), keyevent)
        uk = re.sub("(?<=[^A-Za-z])[a-z]>$", lambda m: m.group(0).upper(), keyevent)

        self.bind_event(lk, handler, bind_all=bind_all, bind_peer=True)
        self.bind_event(uk, handler, bind_all=bind_all, bind_peer=True)

    #
    # Handle "modified" flag
    #
    def add_modified_callback(self, func: Callable[[], None]) -> None:
        """Add callback function to a list of functions to be called when
        widget's modified flag changes.

        Args:
            func: Callback function to be added to list.
        """
        self.modified_callbacks.append(func)

    def modify_flag_changed_callback(self) -> None:
        """This method is bound to <<Modified>> event which happens whenever
        the widget's modified flag is changed - not just when changed to True.

        Causes all functions registered via ``add_modified_callback`` to be called.
        """
        for func in self.modified_callbacks:
            func()

    def set_modified(self, mod: bool) -> None:
        """Manually set widget's modified flag (may trigger call to
        ```modify_flag_changed_callback```).

        Args:
            mod: Boolean setting for widget's modified flag."""
        self.edit_modified(mod)

    def is_modified(self) -> bool:
        """Return whether widget's text has been modified."""
        return self.edit_modified()

    def do_save(self, fname: str, clear_modified_flag: bool = True) -> None:
        """Save widget's text to file.

        Args:
            fname: Name of file to save text to.
        """
        with open(fname, "w", encoding="utf-8") as fh:
            fh.write(self.get_text())
        if clear_modified_flag:
            self.set_modified(False)

    def do_open(self, fname: str) -> None:
        """Load text from file into widget.

        Args:
            fname: Name of file to load text from.
        """
        self.delete("1.0", tk.END)
        try:
            with open(fname, "r", encoding="utf-8") as fh:
                self.insert(tk.END, fh.read())
        except UnicodeDecodeError:
            with open(fname, "r", encoding="iso-8859-1") as fh:
                self.insert(tk.END, fh.read())
        # Remove BOM from first line if present
        if bom_match := self.find_match(
            "\ufeff", IndexRange("1.0", self.index("1.0 lineend"))
        ):
            self.delete(bom_match.rowcol.index())
        self.set_modified(False)
        self.edit_reset()

    def do_close(self) -> None:
        """Close current file and clear widget."""
        self.delete("1.0", tk.END)
        self.set_modified(False)
        self.edit_reset()

    def undo_block_begin(self) -> None:
        """Begin a block of changes that will be undone with one undo operation.

        Block is automatically closed when system becomes idle.

        Note: this version does not support nesting of blocks.
        """
        self.config(autoseparators=False)
        self.edit_separator()
        self.after_idle(self.undo_block_end)

    def undo_block_end(self) -> None:
        """End a block of changes that will be undone with one undo operation.

        Normally called automatically when system becomes idle, but can safely be
        called manually if required, e.g. to start & end two blocks within one
        user operation.

        Note: this version does not support nesting of blocks.
        """
        self.edit_separator()
        self.config(autoseparators=True)

    def get_insert_index(self) -> IndexRowCol:
        """Return index of the insert cursor as IndexRowCol object.

        Returns:
            IndexRowCol containing position of the insert cursor.
        """
        return IndexRowCol(self.focus_widget().index(tk.INSERT))

    def set_insert_index(
        self,
        insert_pos: IndexRowCol,
        focus: bool = True,
        focus_widget: Optional[tk.Text] = None,
    ) -> None:
        """Set the position of the insert cursor.

        Args:
            insert_pos: Location to position insert cursor.
            focus: Optional, False means focus will not be forced to maintext
            focus_widget: Optionally set index in this widget, not the default
        """
        if focus_widget is None:
            focus_widget = self.focus_widget()
        focus_widget.mark_set(tk.INSERT, insert_pos.index())
        # The `see` method can leave the desired line at the top or bottom of window.
        # So, we "see" lines above and below desired line incrementally up to
        # half window height each way, ensuring desired line is left in the middle.
        # If performance turns out to be an issue, consider giving `step` to `range`.
        # Step should be smaller than half minimum likely window height.
        start_index = focus_widget.index(
            f"@0,{int(focus_widget.cget('borderwidth'))} linestart"
        )
        end_index = focus_widget.index(f"@0,{focus_widget.winfo_height()} linestart")
        n_lines = IndexRowCol(end_index).row - IndexRowCol(start_index).row
        for inc in range(1, int(n_lines / 2) + 1):
            focus_widget.see(f"{tk.INSERT}-{inc}l")
            focus_widget.see(f"{tk.INSERT}+{inc}l")
        focus_widget.see(tk.INSERT)
        if focus:
            focus_widget.focus_set()

    def set_mark_position(
        self,
        mark: str,
        position: IndexRowCol,
        gravity: Literal["left", "right"] = tk.LEFT,
    ) -> None:
        """Set the position of a mark and its gravity.

        Args:
            mark: Name of mark.
            position: Location to position mark.
            gravity: tk.LEFT(default) to stick to left character; tk.RIGHT to stick to right
        """
        self.mark_set(mark, position.index())
        self.mark_gravity(mark, gravity)

    def get_text(self) -> str:
        """Return all the text from the text widget.

        Strips final additional newline that widget adds at tk.END.

        Returns:
            String containing text widget contents.
        """
        return self.get(1.0, f"{tk.END}-1c")

    def get_lines(self) -> Generator[tuple[str, int], None, None]:
        """Yield each line & line number in main text window."""
        for line_num in range(1, self.end().row + 1):
            line = maintext().get(f"{line_num}.0", f"{line_num}.0 lineend")
            yield line, line_num

    def toggle_selection_type(self) -> None:
        """Switch regular selection to column selection or vice versa."""
        sel_ranges = self.selected_ranges()
        if len(sel_ranges) > 1:
            self.do_select(IndexRange(sel_ranges[0].start, sel_ranges[-1].end))
        else:
            self.columnize_selection()

    def columnize_copy(self) -> None:
        """Columnize the current selection and copy it."""
        self.columnize_selection()
        self.column_copy_cut()

    def columnize_cut(self) -> None:
        """Columnize the current selection and copy it."""
        self.columnize_selection()
        self.column_copy_cut(cut=True)

    def columnize_paste(self) -> None:
        """Columnize the current selection, if any, and paste the clipboard contents."""
        self.columnize_selection()
        self.column_paste()

    def columnize_selection(self) -> None:
        """Adjust current selection to column mode,
        spanning a block defined by the two given corners."""
        if not (ranges := self.selected_ranges()):
            return
        self.do_column_select(IndexRange(ranges[0].start, ranges[-1].end))

    def do_column_select(self, col_range: IndexRange) -> None:
        """Use multiple selection ranges to select a block
        defined by the start & end of the given range.

        Args:
            IndexRange containing corners of block to be selected."""
        self.clear_selection()
        min_row = min(col_range.start.row, col_range.end.row)
        max_row = max(col_range.start.row, col_range.end.row)
        min_col = min(col_range.start.col, col_range.end.col)
        max_col = max(col_range.start.col, col_range.end.col)
        for line in range(min_row, max_row + 1):
            beg = IndexRowCol(line, min_col).index()
            end = IndexRowCol(line, max_col).index()
            self.focus_widget().tag_add("sel", beg, end)

    def clear_selection(self) -> None:
        """Clear any current text selection."""
        self.focus_widget().tag_remove("sel", "1.0", tk.END)

    def do_select(self, sel_range: IndexRange) -> None:
        """Select the given range of text.

        Args:
            sel_range: IndexRange containing start and end of text to be selected."""
        self.clear_selection()
        self.focus_widget().tag_add(
            "sel", sel_range.start.index(), sel_range.end.index()
        )

    def selected_ranges(self) -> list[IndexRange]:
        """Get the ranges of text marked with the `sel` tag.

        Returns:
            List of IndexRange objects indicating the selected range(s)
            Each range covers one line of selection from the leftmost
            to the rightmost selected columns in the first/last rows.
            If column is greater than line length it equates to end of line.
        """
        ranges = self.focus_widget().tag_ranges("sel")
        assert len(ranges) % 2 == 0
        sel_ranges = []
        if len(ranges) > 0:
            if len(ranges) == 2:
                # Deal with normal (single) selection first
                sel_ranges.append(IndexRange(ranges[0], ranges[1]))
            else:
                # Now column selection (read in conjunction with do_column_select)
                start_rowcol = IndexRowCol(ranges[0])
                end_rowcol = IndexRowCol(ranges[-1])
                minidx = min(ranges, key=lambda x: IndexRowCol(x).col)
                mincol = IndexRowCol(minidx).col
                maxidx = max(ranges, key=lambda x: IndexRowCol(x).col)
                maxcol = IndexRowCol(maxidx).col
                for row in range(start_rowcol.row, end_rowcol.row + 1):
                    start = IndexRowCol(row, mincol)
                    end = IndexRowCol(row, maxcol)
                    sel_ranges.append(IndexRange(start, end))
        return sel_ranges

    def selected_text(self) -> str:
        """Get the first chunk of text marked with the `sel` tag.

        Returns:
            String containing the selected text, or empty string if none selected.
        """
        ranges = self.focus_widget().tag_ranges("sel")
        assert len(ranges) % 2 == 0
        if ranges:
            return self.get(ranges[0], ranges[1])
        return ""

    def save_selection_ranges(self) -> None:
        """Save current selection ranges if they have changed since last call.

        Also save previous selection ranges, if beginning and end have both changed,
        so they can be restored if needed.
        """
        ranges = maintext().selected_ranges()
        # Inequality tests below rely on IndexCol/IndexRange having `__eq__` method
        if ranges != self.current_sel_ranges:
            # Problem is when the user drags to select, you can get multiple calls to this function,
            # which are really all the same selection. Possible better solution in future, but for now,
            # only save into prev if both the start and end are different to the last call.
            # Also save into prev if there were ranges on previous call, but not this one, i.e. the
            # selection has been cancelled.
            if self.current_sel_ranges and (
                (
                    ranges
                    and ranges[0].start != self.current_sel_ranges[0].start
                    and ranges[-1].end != self.current_sel_ranges[-1].end
                )
                or not ranges
            ):
                self.prev_sel_ranges = self.current_sel_ranges.copy()
            self.current_sel_ranges = ranges.copy()

    def restore_selection_ranges(
        self, ranges: Optional[list[IndexRange]] = None
    ) -> None:
        """Restore previous selection ranges.

        Args:
            ranges: Selection ranges to restore - defaults to previous selection range
        """
        if ranges is None:
            ranges = self.prev_sel_ranges
        if len(ranges) == 0:
            self.clear_selection()
        elif len(ranges) == 1:
            self.do_select(ranges[0])
        elif len(ranges) > 1:
            col_range = IndexRange(ranges[0].start, ranges[-1].end)
            self.do_column_select(col_range)

    def selection_ranges_store_with_marks(self) -> None:
        """Set marks at start and end of selection range(s).

        This means selection can be restored even if line/col numbers have changed.
        """
        mark = "1.0"
        # Delete all selection-range marks carefully to avoid accessing a deleted one
        while mark_next := self.mark_next(mark):
            if mark_next.startswith((SELECTION_MARK_START, SELECTION_MARK_END)):
                mark = self.index(mark_next)
                self.mark_unset(mark_next)
            else:
                mark = mark_next
        for idx, sel_range in enumerate(self.selected_ranges()):
            self.set_mark_position(
                f"{SELECTION_MARK_START}{idx}", sel_range.start, gravity=tk.LEFT
            )
            self.set_mark_position(
                f"{SELECTION_MARK_END}{idx}", sel_range.end, gravity=tk.RIGHT
            )

    def selection_ranges_restore_from_marks(self) -> None:
        """Set selection range(s) from the selection-range marks.

        Assumes marks occur in pairs at start/end of ranges."""
        self.clear_selection()
        next_mark = self.mark_next("1.0")
        while next_mark:
            mark = next_mark
            if mark.startswith(SELECTION_MARK_START):
                start_mark = mark
            elif mark.startswith(SELECTION_MARK_END):
                assert start_mark
                self.focus_widget().tag_add("sel", start_mark, mark)
            next_mark = self.mark_next(mark)

    def column_delete(self) -> None:
        """Delete the selected column text."""
        if not (ranges := self.selected_ranges()):
            return
        for _range in ranges:
            self.delete(_range.start.index(), _range.end.index())

    def column_copy_cut(self, cut: bool = False) -> None:
        """Copy or cut the selected text to the clipboard.

        A newline character is inserted between each line.

        Args:
            cut: True if cut is required, defaults to False (copy)
        """
        if not (ranges := self.selected_ranges()):
            return
        self.clipboard_clear()
        for _range in ranges:
            start = _range.start.index()
            end = _range.end.index()
            string = self.get(start, end)
            if cut:
                self.delete(start, end)
            self.clipboard_append(string + "\n")

    def column_paste(self) -> None:
        """Paste the clipboard text column-wise, overwriting any selected text.

        If more lines in clipboard than selected ranges, remaining lines will be
        inserted in the same column in lines below. If more selected ranges than
        clipboard lines, clipboard will be repeated until all selected ranges are
        replaced.
        """
        # Trap empty clipboard or no STRING representation
        try:
            clipboard = self.clipboard_get()
        except tk.TclError:
            return
        cliplines = clipboard.splitlines()
        if not cliplines:
            return
        num_cliplines = len(cliplines)

        # If nothing selected, set up an empty selection range at the current insert position
        sel_ranges = self.selected_ranges()
        ranges = []
        if sel_ranges:
            start_rowcol = sel_ranges[0].start
            end_rowcol = sel_ranges[-1].end
            for row in range(start_rowcol.row, end_rowcol.row + 1):
                rbeg = IndexRowCol(row, start_rowcol.col)
                rend = IndexRowCol(row, end_rowcol.col)
                ranges.append(IndexRange(rbeg, rend))
        else:
            insert_index = self.get_insert_index()
            ranges.append(IndexRange(insert_index, insert_index))
        num_ranges = len(ranges)

        # Add any necessary newlines if near end of file
        min_row = ranges[0].start.row
        max_row = min_row + max(num_cliplines, num_ranges)
        end_index = self.rowcol(IndexRowCol(max_row, 0).index())
        if max_row > end_index.row:
            self.insert(
                end_index.index() + " lineend", "\n" * (max_row - end_index.row)
            )

        for line in range(max(num_cliplines, num_ranges)):
            # Add any necessary spaces if line being pasted into is too short
            start_rowcol = IndexRowCol(ranges[0].start.row + line, ranges[0].start.col)
            end_rowcol = IndexRowCol(ranges[0].start.row + line, ranges[-1].end.col)
            end_index = self.rowcol(end_rowcol.index())
            nspaces = start_rowcol.col - end_index.col
            if nspaces > 0:
                self.insert(end_index.index(), " " * nspaces)

            clipline = cliplines[line % num_cliplines]
            if line < num_ranges:
                self.replace(start_rowcol.index(), end_rowcol.index(), clipline)
            else:
                self.insert(start_rowcol.index(), clipline)
        rowcol = self.rowcol(f"{start_rowcol.index()} + {len(clipline)}c")
        self.set_insert_index(rowcol)

    def affirm_clipboard_contents(self) -> None:
        """Ensure clipboard is set to its "current" contents.

        The purpose of this is to set the clipboard by non-Tcl/Tk means.
        This should bypass the bug where Tcl/Tk doesn't update the
        system clipboard counter. Some apps, e.g. BBEdit, need this to detect the
        clipboard has changed: https://github.com/python/cpython/issues/104613
        """
        if not is_mac():
            raise NotImplementedError("This function only works on macOS")

        # Use pbcopy macOS command to "touch" the clipboard contents
        try:
            with subprocess.Popen(["/usr/bin/pbcopy"], stdin=subprocess.PIPE) as proc:
                proc.communicate(input=self.clipboard_get().encode())
        except tk.TclError:
            pass

    def smart_copy(self) -> str:
        """Do column copy if multiple ranges selected, else default copy."""
        if is_mac():
            self.after_idle(self.affirm_clipboard_contents)
        if len(self.selected_ranges()) <= 1:
            return ""  # Permit default behavior to happen
        self.column_copy_cut()
        return "break"  # Skip default behavior

    def smart_cut(self) -> str:
        """Do column cut if multiple ranges selected, else default cut."""
        if is_mac():
            self.after_idle(self.affirm_clipboard_contents)
        if len(self.selected_ranges()) <= 1:
            return ""  # Permit default behavior to happen
        self.column_copy_cut(cut=True)
        return "break"  # Skip default behavior

    def smart_paste(self) -> str:
        """Do column paste if multiple ranges selected, else default paste."""
        # Afterwards, make sure insert cursor is visible
        self.after_idle(lambda: self.see(tk.INSERT))
        self.undo_block_begin()  # Undo it all in one block
        sel_range = self.selected_ranges()
        # Linux default behavior doesn't clear any current selection when pasting,
        # so do it manually (on all platforms - harmless on Windows/Mac)
        if len(sel_range) == 1:
            self.delete(sel_range[0].start.index(), sel_range[0].end.index())
        if len(sel_range) <= 1:
            return ""  # Permit default behavior to happen
        self.column_paste()
        return "break"  # Skip default behavior

    def smart_delete(self) -> str:
        """Do column delete if multiple ranges selected, else default backspace."""
        if len(self.selected_ranges()) <= 1:
            return ""  # Permit default behavior to happen
        self.column_delete()
        return "break"  # Skip default behavior

    def column_select_click_action(self, start: IndexRowCol) -> None:
        """Do what needs to be done when the user clicks to start column selection.
        Since user can click first then press modifier key, or hold modifier while
        clicking, there are two routes into this point.

        Args:
            start: Start index of column selection.
        """
        self.focus_widget().config(cursor="tcross")
        self.column_select_start(start)
        self._autoscroll_active = True

    def column_select_click(self, event: tk.Event) -> None:
        """Callback when column selection is started via mouse click.

        Args
            event: Event containing mouse coordinates.
        """
        # Starting a column selection needs to manually set focus on the main/peer
        # Force an update so the focus is actually set before it's queried during motion
        event.widget.focus()
        event.widget.update()
        self.column_select_click_action(self.rowcol(f"@{event.x},{event.y}"))

    def column_select_motion(self, event: tk.Event) -> None:
        """Callback when column selection continues via mouse motion.

        Jiggery-pokery needed because if mouse is on a short (or empty) line,
        and mouse position is to right of the last character, its column
        reported with by "@x,y" is the end of the line, not the screen column
        of the mouse location.

        Args:
            event: Event containing mouse coordinates.
        """
        anchor_rowcol = self.rowcol(TK_ANCHOR_MARK)
        cur_rowcol = self.rowcol(f"@{event.x},{event.y}")
        # Find longest visible line between start of selection and current mouse location
        minrow = min(anchor_rowcol.row, cur_rowcol.row)
        # No point starting before first line of screen
        toprow = self.rowcol("@0,0").row
        minrow = max(minrow, toprow)
        maxrow = max(anchor_rowcol.row, cur_rowcol.row)
        maxlen = -1
        y_maxlen = -1
        for row in range(minrow, maxrow + 1):
            geometry = event.widget.bbox(f"{row}.{cur_rowcol.col}")
            if geometry is None:
                continue
            line_len = len(self.get(f"{row}.0", f"{row}.0 lineend"))
            if line_len > maxlen:
                maxlen = line_len
                y_maxlen = geometry[1]
        # Find which column mouse would be at if it was over the longest line
        # but in the same horizontal position - this is the "true" mouse column
        # Get y of longest line, and use actual x of mouse
        truecol_rowcol = self.rowcol(f"@{event.x},{y_maxlen}")
        # At last, we can set the column in cur_rowcol to the "screen" column
        # which is what we need to pass to do_column_select().
        cur_rowcol.col = truecol_rowcol.col

        # Attempt to start up column selection if arriving here without a previous click
        # to start, e.g. user presses modifier key after beginning mouse-drag selection.
        if not self.column_selecting:
            ranges = self.selected_ranges()
            if not ranges:  # Fallback to using insert cursor position
                insert_rowcol = self.get_insert_index()
                ranges = [IndexRange(insert_rowcol, insert_rowcol)]
            if self.compare(cur_rowcol.index(), ">", ranges[0].start.index()):
                anchor = ranges[0].start
            else:
                anchor = ranges[-1].end
            self.column_select_start(anchor)
            self.column_select_click_action(anchor)  # Fake a column_select_click

        self.do_column_select(IndexRange(self.rowcol(TK_ANCHOR_MARK), cur_rowcol))

        # Handle scrolling past edge of widget
        if self._autoscroll_active:
            self._autoscroll_callback(event)

    def column_select_release(self, event: tk.Event) -> None:
        """Callback when column selection is stopped via mouse button release.

        Args:
            event: Event containing mouse coordinates.
        """
        self._autoscroll_active = False
        self.column_select_motion(event)
        self.column_select_stop(event)
        event.widget.mark_set(tk.INSERT, f"@{event.x},{event.y}")

    def column_select_start(self, anchor: IndexRowCol) -> None:
        """Begin column selection.

        Args:
            anchor: Selection anchor (start point) - this is also used by Tk
                    if user switches to normal selection style.
        """
        self.mark_set(TK_ANCHOR_MARK, anchor.index())
        self.column_selecting = True

    def column_select_stop(self, event: tk.Event) -> None:
        """Stop column selection."""
        self.column_selecting = False
        event.widget.config(cursor="")

    def rowcol(self, index: str) -> IndexRowCol:
        """Return IndexRowCol corresponding to given index in maintext/peer.

        Args:
            index: Index to position in maintext.

        Returns:
            IndexRowCol representing the position.
        """
        return IndexRowCol(self.focus_widget().index(index))

    def start(self) -> IndexRowCol:
        """Return IndexRowCol for start of text in widget, i.e. "1.0"."""
        return self.rowcol("1.0")

    def end(self) -> IndexRowCol:
        """Return IndexRowCol for end of text in widget, i.e. "end - 1c"
        because text widget "end" is start of line below last char."""
        return self.rowcol(tk.END + "-1c")

    def move_to_selection_start(self, force_line: bool = False) -> str:
        """Set insert position to start of any selection text.

        Args:
            force_line: True to force movement to start/end of relevant line
        """
        return self._move_to_selection_edge(end=False, force_line=force_line)

    def move_to_selection_end(self, force_line: bool = False) -> str:
        """Set insert position to end of any selection text.

        Args:
            force_line: True to force movement to start/end of relevant line
        """
        return self._move_to_selection_edge(end=True, force_line=force_line)

    def _move_to_selection_edge(self, end: bool, force_line: bool = False) -> str:
        """Set insert position to start or end of selection text.

        Args:
            end: True for end, False for start.
            force_line: True to force movement to start/end of relevant line
        """
        sel_ranges = self.selected_ranges()
        if not sel_ranges:
            return ""
        pos = sel_ranges[-1].end if end else sel_ranges[0].start
        idx = pos.index()
        if force_line:
            idx += " lineend" if end else "linestart"
        # Use low-level calls to avoid "see" behavior of set_insert_index
        self.focus_widget().mark_set(tk.INSERT, idx)
        self.focus_widget().see(tk.INSERT)
        self.clear_selection()
        return "break"

    def move_to_start(self) -> None:
        """Set insert position to start of text & clear any selection."""
        self.clear_selection()
        self.set_insert_index(self.start())

    def move_to_end(self) -> None:
        """Set insert position to end of text & clear any selection."""
        self.clear_selection()
        self.set_insert_index(self.end())

    def select_to_start(self) -> None:
        """Select from current position to start of text."""
        self.do_select(IndexRange(self.start(), self.get_insert_index()))
        self.set_insert_index(self.start())

    def select_to_end(self) -> None:
        """Select from current position to start of text."""
        self.do_select(IndexRange(self.get_insert_index(), self.end()))
        self.set_insert_index(self.end())

    def page_mark_previous(self, mark: str) -> str:
        """Return page mark previous to given one, or empty string if none."""
        return self.page_mark_next_previous(mark, -1)

    def page_mark_next(self, mark: str) -> str:
        """Return page mark after given one, or empty string if none."""
        return self.page_mark_next_previous(mark, 1)

    def page_mark_next_previous(self, mark: str, direction: Literal[1, -1]) -> str:
        """Return page mark before/after given one, or empty string if none.

        Args:
            mark: Mark to begin search from
            direction: +1 to go to next page; -1 for previous page
        """
        if direction < 0:
            mark_next_previous = self.mark_previous
        else:
            mark_next_previous = self.mark_next
        while mark := mark_next_previous(mark):  # type: ignore[assignment]
            if self.is_page_mark(mark):
                return mark
        return ""

    def is_page_mark(self, mark: str) -> bool:
        """Check whether mark is a page mark, e.g. "Pg027".

        Args:
            mark: String containing name of mark to be checked.

        Returns:
            True if string matches the format of page mark names.
        """
        return mark.startswith(PAGEMARK_PREFIX)

    def _replace_preserving_pagemarks(
        self, start_index: Any, end_index: Any, replacement: str, *tags: Any
    ) -> None:
        """Replace text indicated by indexes with given string (& optional tags).

        If text being replaced is multiline, and replacement has the same number
        of lines, replace each line separately to keep page breaks on the same line.

        Args:
            start_index: Start of text to be replaced.
            end_index: End of text to be replaced.
            replacement:  Replacement text.
            tags: Optional tuple of tags to be applied to inserted text.
        """
        # Convert to simple row.col index, otherwise indexes like "53.end-5c"
        # that can "move" when text is inserted/deleted will cause problems later.
        start_index = self.index(start_index)
        end_index = self.index(end_index)
        start_row = IndexRowCol(start_index).row
        end_row = IndexRowCol(end_index).row
        num_newlines_match = end_row - start_row
        num_newlines_replacement = replacement.count("\n")

        # If match is all on one line, or different number of lines in match/replacement,
        # do it in one block without splitting into lines
        if num_newlines_match == 0 or num_newlines_match != num_newlines_replacement:
            self._replace_preserving_pagemarks_block(
                start_index, end_index, replacement, tags
            )
            return

        # At least one line break in match & replacement - split & do one line at a time
        # We know that there are the same number of line breaks in match & replacement
        for chunk_num, replace_line in enumerate(replacement.split("\n")):
            chunk_start = (
                start_index if chunk_num == 0 else f"{start_row + chunk_num}.0"
            )
            chunk_end = (
                end_index
                if chunk_num == num_newlines_replacement
                else f"{start_row + chunk_num}.end"
            )
            self._replace_preserving_pagemarks_block(
                chunk_start, chunk_end, replace_line, tags
            )

    def _replace_preserving_pagemarks_block(
        self, start_point: Any, end_point: Any, replacement: str, *tags: Any
    ) -> None:
        """Replace text indicated by indexes with given string (& optional tags).

        If text being replaced contains page markers, preserve the location of those markers
        in the same proportions along the replacement text. E.g. if a marker exists at the mid-point
        of the replaced text, then put that marker at the mid-point of the replacement text.
        Without this, all page markers within replaced text end up at the beginning (or end
        depending on gravity) of the replacement text.

        Algorithm works backwards through file so that early partial replacements do not
        affect locations for subsequent partial replacements.

        Args:
            start_index: Start of text to be replaced.
            end_index: End of text to be replaced.
            replacement:  Replacement text.
            tags: Optional tuple of tags to be applied to inserted text.
        """
        # Convert input args to index because if marks, can get odd behavior depending on gravity.
        start_index = self.index(start_point)
        end_index = self.index(end_point)
        # Get lengths from start of replacement to each page marker
        len_to_marks: list[int] = []
        mark = end_index
        while mark := self.page_mark_previous(mark):
            if self.compare(mark, "<", start_index):
                break
            len_to_marks.append(len(self.get(start_index, mark)))
        # Scale any lengths by the ratio of new string length to old string length
        if len_to_marks:
            ratio = len(replacement) / len(self.get(start_index, end_index))
            len_to_marks = [round(x * ratio) for x in len_to_marks]
        # First length is the whole string length
        len_to_marks.insert(0, len(replacement))

        # Set gravity of page markers immediately after old text to "right" so they stay at the end
        end_marks: list[str] = []
        mark = end_index
        while mark := self.page_mark_next(mark):
            if self.compare(mark, ">", end_index):
                break
            end_marks.append(mark)
        for mark in end_marks:  # Safe to set the gravity now we have the list
            self.mark_gravity(mark, tk.RIGHT)

        mark = end_index
        prev_mark = mark
        len_idx = 0

        # Replace inter-page-marker chunks
        def replace_chunk(
            mark: str, prev_mark: str, beg_idx: int, end_idx: int
        ) -> None:
            """Convenience function to replace one chunk."""
            ins_str = replacement[beg_idx:end_idx]
            self.mark_set(REPLACE_END_MARK, prev_mark)
            self.insert(mark, ins_str, tags)
            self.delete(f"{mark}+{len(ins_str)}c", REPLACE_END_MARK)

        while mark := self.page_mark_previous(mark):
            if self.compare(mark, "<", start_index):
                break
            replace_chunk(
                mark, prev_mark, len_to_marks[len_idx + 1], len_to_marks[len_idx]
            )
            prev_mark = mark
            len_idx += 1

        # Replace final chunk (actually the earliest since going in reverse)
        replace_chunk(start_index, prev_mark, 0, len_to_marks[len_idx])

        # Restore gravity to left for marks at the end of the string
        for mark in end_marks:
            self.mark_gravity(mark, tk.LEFT)

    def find_match(
        self,
        search_string: str,
        start_range: IndexRange,
        nocase: bool = False,
        regexp: bool = False,
        backwards: bool = False,
    ) -> Optional[FindMatch]:
        """Find occurrence of string/regex in given range.

        Args:
            search_string: String/regex to be searched for.
            start_range: Range in which to search, or just start point to search whole file.
            nocase: True to ignore case.
            regexp: True if string is a *Tcl* regex; False for exact string match.
            backwards: True to search backwards through text.

        Returns:
            FindMatch containing index of start and count of characters in match.
            None if no match.
        """
        start_index = start_range.start.index()
        stop_index = start_range.end.index()

        count_var = tk.IntVar()
        try:
            match_start = self.search(
                search_string,
                start_index,
                stop_index,
                count=count_var,
                nocase=nocase,
                regexp=regexp,
                backwards=backwards,
            )
        except tk.TclError as exc:
            if str(exc).startswith("couldn't compile regular expression pattern"):
                raise TclRegexCompileError(str(exc)) from exc
            match_start = None

        if match_start:
            return FindMatch(IndexRowCol(match_start), count_var.get())
        return None

    def find_matches(
        self,
        search_string: str,
        text_range: IndexRange,
        nocase: bool,
        regexp: bool,
    ) -> list[FindMatch]:
        """Find all occurrences of string/regex in given range.

        Args:
            search_string: String/regex to be searched for.
            text_range: Range in which to search.
            nocase: True to ignore case.
            regexp: True if string is a *Tcl* regex; False for exact string match.

        Returns:
            List of FindMatch objects, each containing index of start and count of characters in a match.
            Empty list if no matches.
        """
        start_index = text_range.start.index()
        stop_index = text_range.end.index()

        matches = []
        count_var = tk.IntVar()
        start = start_index
        while start:
            try:
                start = self.search(
                    search_string,
                    start,
                    stop_index,
                    count=count_var,
                    nocase=nocase,
                    regexp=regexp,
                )
            except tk.TclError as exc:
                if str(exc).startswith("couldn't compile regular expression pattern"):
                    raise TclRegexCompileError(str(exc)) from exc
                break
            if start:
                matches.append(FindMatch(IndexRowCol(start), count_var.get()))
                start += f"+{count_var.get()}c"
        return matches

    def get_match_text(self, match: FindMatch) -> str:
        """Return text indicated by given match.

        Args:
            match: Start and length of matched text - assumed to be valid.
        """
        start_index = match.rowcol.index()
        end_index = maintext().index(start_index + f"+{match.count}c")
        return maintext().get(start_index, end_index)

    def select_match_text(self, match: FindMatch) -> None:
        """Select text indicated by given match.

        Args:
            match: Start and length of matched text - assumed to be valid.
        """
        start_index = match.rowcol.index()
        end_index = maintext().index(start_index + f"+{match.count}c")
        maintext().do_select(IndexRange(start_index, end_index))

    def find_match_user(
        self,
        search_string: str,
        start_point: IndexRowCol,
        nocase: bool,
        wholeword: bool,
        regexp: bool,
        backwards: bool,
        wrap: bool,
    ) -> Optional[FindMatch]:
        """Find occurrence of string/regex in file by slurping text into string.
        Called for user searches - regexps are Python flavor.

        If searching backwards with backref/lookaround, avoid regex bug by actually
        searching forward in range and getting last match.

        Args:
            search_string: Regex to be searched for.
            start_point: Start point for search.
            nocase: True to ignore case.
            wholeword: True to only search for whole words (i.e. word boundary at start & end).
            backwards: True to search backwards through text.
            wrap: True to wrap search round end (or start) of file.

        Returns:
            FindMatch containing index of start and count of characters in match.
            None if no match.
        """
        # Search first chunk from start point to beg/end of file
        if backwards:
            chunk_range = IndexRange(self.start(), start_point)
            # Doesn't matter if this ends up True, when not strictly necessary, e.g. `\\1`
            # Should include cases where reverse searching doesn't work: backrefs,
            # lookahead/behind, `^` & `$` (since they are converted to lookahead/behind)
            # Matching code in
            backrefs = regexp and re.search(
                r"(\\\d|\(\?[<=!]|(?<![\[\\])\^|(?<![\\])\$)", search_string
            )
        else:
            chunk_range = IndexRange(start_point, self.end())
            backrefs = False
        slurp_text = self.get(chunk_range.start.index(), chunk_range.end.index())

        # Searching backwards with backrefs/lookarounds doesn't behave as required, so
        # call special routine to use forward searching to search backward
        if backrefs:
            match = self._find_last_match_in_range(
                search_string,
                slurp_text,
                chunk_range,
                nocase,
                wholeword,
            )
        else:
            match, _ = self.find_match_in_range(
                search_string,
                slurp_text,
                chunk_range,
                nocase=nocase,
                regexp=regexp,
                wholeword=wholeword,
                backwards=backwards,
            )

        # If not found, and we're wrapping, search the other half of the file
        if match is None and wrap:
            if backwards:
                chunk_range = IndexRange(start_point, self.end())
            else:
                chunk_range = IndexRange(self.start(), start_point)
            slurp_text = self.get(chunk_range.start.index(), chunk_range.end.index())
            # Special backref search again
            if backrefs:
                match = self._find_last_match_in_range(
                    search_string,
                    slurp_text,
                    chunk_range,
                    nocase,
                    wholeword,
                )
            else:
                match, _ = self.find_match_in_range(
                    search_string,
                    slurp_text,
                    chunk_range,
                    nocase=nocase,
                    regexp=regexp,
                    wholeword=wholeword,
                    backwards=backwards,
                )
        return match

    def _find_last_match_in_range(
        self,
        search_string: str,
        slurp_text: str,
        slurp_range: IndexRange,
        nocase: bool,
        wholeword: bool,
    ) -> Optional[FindMatch]:
        """Find last match in given range.

        This is used instead of searching backwards if regex contains backreference,
        lookbehind or lookahead, since these don't work backwards without adjustment.

        Returns:
            Last match in range (or None).
        """
        slice_start = 0
        last_match = None
        slurp_len = len(slurp_text)
        while True:
            match, match_start = self.find_match_in_range(
                search_string,
                slurp_text[slice_start:],
                slurp_range,
                nocase=nocase,
                regexp=True,
                wholeword=wholeword,
                backwards=False,
            )
            if match is None:
                break
            last_match = match
            # Adjust start of slice of slurped text, and where that point is in the file
            advance = max(match.count, 1)  # Always advance at least 1 character
            slice_start += match_start + advance
            if slice_start >= slurp_len:
                break
            slurp_start = IndexRowCol(self.index(f"{match.rowcol.index()}+{advance}c"))
            slurp_range = IndexRange(slurp_start, slurp_range.end)
        return last_match

    def find_match_in_range(
        self,
        search_string: str,
        slurp_text: str,
        slurp_range: IndexRange,
        nocase: bool,
        regexp: bool,
        wholeword: bool,
        backwards: bool,
    ) -> tuple[Optional[FindMatch], int]:
        """Find occurrence of regex in text range using slurped text, and also
        where it is in the slurp text.

        Args:
            search_string: Regex to be searched for.
            slurp_text: Text from search range slurped from file.
            slurp_start: Index to start of `slurp_text` in file.
            nocase: True to ignore case.
            regexp: True if `search_string` is a regexp.
            wholeword: True to only search for whole words (i.e. word boundary at start & end).
            backwards: True to search backwards from the end, i.e. find last occurrence.

        Returns:
            Tuple: a FindMatch containing index in file of start and count of characters in match,
            and None if no match; also the index into the slurp text of the match start, which is
            needed for iterated use with the same slurp text, such as Replace All
        """
        slurp_newline_adjustment = 0
        slurp_start = slurp_range.start
        slurp_end = slurp_range.end
        # Special handling for ^/$: we can't just use `(?m)` or `re.MULTILINE` in order to
        # make these match start/end of line, because that flag also permit matchings
        # at start/end of *string* for ^/$, not just after/before newlines.
        # That would give a false match if search is in a range with part lines at start/end.
        if regexp:
            # Since "^" matches start of string (when not escaped with "\"), and we want it
            # to match start of line, replace it with lookbehind for newline.
            if re.search(r"(?<![\[\\])\^", search_string):
                search_string = re.sub(r"(?<![\[\\])\^", r"(?<=\\n)", search_string)
                # Need to make sure there is a newline before start of string
                # if string starts at the beginning of a line
                if slurp_start.col == 0:
                    slurp_text = "\n" + slurp_text
                    slurp_newline_adjustment = 1
            # Since "$" matches end of string (when not escaped with "\"), and we want it
            # to match end of line, replace it with lookahead for newline.
            if re.search(r"(?<![\\])\$", search_string):
                search_string = re.sub(r"(?<![\\])\$", r"(?=\\n)", search_string)
                # Need to make sure there is a newline after end of string
                # if string ends at the end of a line
                end_line_len = IndexRowCol(self.index(f"{slurp_end.row}.0 lineend")).col
                last_linestart_in_slurp = slurp_text.rfind("\n")
                if last_linestart_in_slurp < 0:  # Slurp text all on one line
                    last_slurp_line_len = slurp_start.col + len(slurp_text)
                else:
                    last_slurp_line_len = len(slurp_text[last_linestart_in_slurp:]) - 1
                if last_slurp_line_len >= end_line_len:
                    slurp_text = slurp_text + "\n"
        else:
            search_string = re.escape(search_string)
        # Don't use \b for word boundary because it includes underscore
        if wholeword:
            search_string = (
                r"(?=[[:alnum:]])(?<![[:alnum:]])"
                + search_string
                + r"(?<=[[:alnum:]])(?![[:alnum:]])"
            )
        # Preferable to use flags rather than prepending "(?i)", for example,
        # because if we need to report bad regex to user, it's better if it's
        # the regex they typed.
        flags = 0
        if backwards:
            flags |= re.REVERSE
        if nocase:
            flags |= re.IGNORECASE

        match = re.search(search_string, slurp_text, flags=flags)
        if match is None:
            return None, 0

        line_num = slurp_text.count("\n", 0, match.start())
        if line_num > 0:
            match_col = match.start() - slurp_text.rfind("\n", 0, match.start()) - 1
        else:
            match_col = match.start() + slurp_start.col
        line_num += slurp_start.row - slurp_newline_adjustment
        return (
            FindMatch(IndexRowCol(line_num, match_col), len(match[0])),
            match.start() - slurp_newline_adjustment,
        )

    def transform_selection(self, fn: Callable[[str], str]) -> None:
        """Transform a text selection by applying a function or method.

        Args:
            fn: Reference to a function or method
        """
        if not (ranges := self.selected_ranges()):
            return
        maintext().undo_block_begin()
        for _range in ranges:
            start = _range.start.index()
            end = _range.end.index()
            string = self.get(start, end)
            # apply transform, then replace old string with new
            self.replace(start, end, fn(string))

    def sentence_case_transformer(self, s: str) -> str:
        """Text transformer to convert a string to "Sentence case".

        The transformation is not aware of sentence structure; if the
        input string consists of multiple sentences, the result will
        likely not be what was desired. This behavior was ported as-is
        from Guiguts 1.x, but could potentially be improved, at least
        for languages structured like English.

        To be more specific: if multiple sentences are selected, the
        first character of the first sentence will be capitalized, and
        the subsequent sentences will begin with a lowercase letter.

        When using column selection, *each line* within the column
        has its first letter capitalized and the remainder lowercased.
        Why anyone would want to use sentence case with a column
        selection is left as an exercise for the reader.

        Args:
            s: an input string to be transformed

        Returns:
            A transformed string
        """
        # lowercase string, then look for first word character.
        # DOTALL allows '.' to match newlines
        m = re.match(r"(\W*\w)(.*)", s.lower(), flags=re.DOTALL)
        if m:
            return m.group(1).upper() + m.group(2)
        return s

    def title_case_transformer(self, s: str) -> str:
        """Text transformer to convert a string to "Title Case"

        Args:
            s: an input string to be transformed

        Returns:
            A transformed string
        """
        # A list of words to *not* capitalize.
        exception_words: tuple[str, ...] = ()
        if any(lang.startswith("en") for lang in self.get_language_list()):
            # This list should only be used for English text.
            exception_words = (
                "a",
                "an",
                "and",
                "at",
                "by",
                "from",
                "in",
                "of",
                "on",
                "the",
                "to",
            )

        def capitalize_first_letter(match: re.regex.Match[str]) -> str:
            word = match.group()
            if word in exception_words:
                return word
            return word.capitalize()

        # Look for word characters either at the start of the string, or which
        # immediately follow whitespace or punctuation; then apply capitalization.
        s2 = re.sub(r"(?<=\s|^|\p{P}\s?)(\w+)", capitalize_first_letter, s.lower())

        # Edge case: if the string started with a word found in exception_words, it
        # will have been lowercased erroneously.
        return s2[0].upper() + s2[1:]

    def set_languages(self, languages: str) -> None:
        """Set languages used in text.

        Args:
            languages: Language, or list of languages separated by "+". Assumed valid.
        """
        self.languages = languages

    def get_language_list(self) -> list[str]:
        """Get list of languages used in text.

        Returns:
            List of language strings.
        """
        return self.languages.split("+")

    def rewrap_section(
        self, section_range: IndexRange, tidy_function: Callable[[], None]
    ) -> None:
        """Wrap a section of the text.

        Args:
            section_range: Range of text to be wrapped.
            tidy_function: Function to call to tidy up before returning.
        """
        default_left = preferences.get(PrefKey.WRAP_LEFT_MARGIN)
        default_right = preferences.get(PrefKey.WRAP_RIGHT_MARGIN)
        block_indent = preferences.get(PrefKey.WRAP_BLOCK_INDENT)
        poetry_indent = preferences.get(PrefKey.WRAP_POETRY_INDENT)
        bq_indent = preferences.get(PrefKey.WRAP_BLOCKQUOTE_INDENT)
        bq_right = preferences.get(PrefKey.WRAP_BLOCKQUOTE_RIGHT_MARGIN)

        bq_depth = 0
        paragraph = ""
        paragraph_complete = False
        section_start = section_range.start.index()
        # Mark end since line numbers will change during wrapping process
        self.set_mark_position(WRAP_END_MARK, section_range.end, tk.RIGHT)
        line_start = section_start
        paragraph_start = section_start
        self.set_mark_position(
            WRAP_NEXT_LINE_MARK, IndexRowCol(section_start), tk.RIGHT
        )
        # For efficiency with many wrap operations, it is recommended to
        # re-use a single TextWrapper object, rather than creating new ones.
        wrapper = TextWrapper()
        # Keep list of wrap_params so user can nest block quotes
        # First is depth=0, i.e. not blockquote
        block_params_list: list[WrapParams] = [
            WrapParams(default_left, default_left, default_right)
        ]
        paragraph_complete = False

        # Loop until we reach the end of the whole section we want to rewrap
        while self.compare(WRAP_NEXT_LINE_MARK, "<", WRAP_END_MARK):
            line_start = self.index(WRAP_NEXT_LINE_MARK)
            self.set_mark_position(
                WRAP_NEXT_LINE_MARK,
                IndexRowCol(self.index(f"{line_start} +1l")),
                tk.RIGHT,
            )
            line = self.get(line_start, WRAP_NEXT_LINE_MARK)

            bq_depth_change = 0
            # Split for non-blank/blank lines
            if re.search(rf"[^\s{PAGEMARK_PIN}]", line):
                paragraph_complete = False
                # Check for various block markup types
                trimmed = line.lower().rstrip(" \n").replace(PAGEMARK_PIN, "")
                # Begin block quote (maybe customized)
                if match := re.fullmatch(r"/#(\[\d+)?(\.\d+)?(,\d+)?]?", trimmed):
                    bq_depth_change = 1
                    # Default is to just indent left margins by block_indent
                    # Special case if block depth currently zero - use default block right margin not general right margin
                    (
                        new_block_left,
                        new_block_first,
                        new_block_right,
                    ) = self.wrap_interpret_margins(
                        match[1],
                        match[2],
                        match[3],
                        block_params_list[-1].left + bq_indent,
                        block_params_list[-1].left + bq_indent,
                        block_params_list[-1].right if bq_depth > 0 else bq_right,
                    )
                    # Save latest wrap params
                    block_params_list.append(
                        WrapParams(new_block_left, new_block_first, new_block_right)
                    )
                    paragraph_complete = True
                # End block quote
                elif trimmed == "#/":
                    bq_depth_change = -1
                    paragraph_complete = True
                # Some common code for start of all other block types
                elif match := re.fullmatch(
                    r"/([\$xf\*plrci])(\[\d+)?(\.\d+)?(,\d+)?]?", trimmed
                ):
                    block_type = match[1]
                    # Output any previous paragraph
                    if paragraph:
                        self.wrap_paragraph(
                            paragraph_start,
                            line_start,
                            paragraph,
                            block_params_list[bq_depth],
                            wrapper,
                        )
                        paragraph = ""
                    # Reposition line_start in case above wrapping changed line numbering
                    # to be the line after the markup line
                    line_start = self.index(WRAP_NEXT_LINE_MARK)

                    # Find matching close markup within section being wrapped
                    if close_index := self.search(
                        rf"^{PAGEMARK_PIN}*{re.escape(block_type)}/\s*$",
                        line_start,
                        stopindex=WRAP_END_MARK,
                        nocase=True,
                        regexp=True,
                    ):
                        self.set_mark_position(
                            WRAP_NEXT_LINE_MARK,
                            IndexRowCol(self.index(f"{close_index} +1l")),
                            tk.RIGHT,
                        )
                    else:
                        tidy_function()
                        next_line_rowcol = IndexRowCol(self.index(WRAP_NEXT_LINE_MARK))
                        logger.error(
                            f"No closing markup found to match /{block_type} at line {next_line_rowcol.row - 1}"
                        )
                        return

                    # Handle complete no-indent block by skipping the whole thing
                    if block_type in "$xf":
                        pass

                    # Handle complete fixed-indent block
                    elif block_type in "*pl":
                        indent = poetry_indent if block_type == "p" else block_indent
                        left_margin = self.wrap_interpret_single_margin(
                            match[2], block_params_list[-1].left + indent
                        )
                        block_min_left, _ = self.wrap_get_block_limits(
                            line_start, close_index
                        )
                        self.wrap_reindent_block(
                            line_start,
                            close_index,
                            left_margin - block_min_left,
                        )

                    # Handle complete right-align block
                    elif block_type == "r":
                        right_margin = self.wrap_interpret_single_margin(
                            match[2], block_params_list[-1].right
                        )
                        block_min_left, block_max_right = self.wrap_get_block_limits(
                            line_start, close_index
                        )
                        # Ideally, we'd like to insert/delete this number of spaces (negative for delete)
                        # But check we're not deleting so much that block is to the left of left margin
                        n_spaces = right_margin - block_max_right
                        if (
                            n_spaces > 0
                            or -n_spaces <= block_min_left - block_params_list[-1].left
                        ):
                            self.wrap_reindent_block(line_start, close_index, n_spaces)

                    # Handle complete center block
                    elif block_type == "c":
                        default_center = int(
                            (block_params_list[-1].left + block_params_list[-1].right)
                            / 2
                        )
                        center_point = self.wrap_interpret_single_margin(
                            match[2],
                            default_center,
                        )
                        self.wrap_center_block(line_start, close_index, center_point)

                    # Handle complete index block
                    elif block_type == "i":
                        (
                            index_wrap_margin,
                            index_main,
                            index_right,
                        ) = self.wrap_interpret_margins(
                            match[2],
                            match[3],
                            match[4],
                            preferences.get(PrefKey.WRAP_INDEX_WRAP_MARGIN),
                            preferences.get(PrefKey.WRAP_INDEX_MAIN_MARGIN),
                            preferences.get(PrefKey.WRAP_INDEX_RIGHT_MARGIN),
                        )
                        self.wrap_index_block(
                            line_start,
                            close_index,
                            index_wrap_margin,
                            index_main,
                            index_right,
                            wrapper,
                        )

                # End blocks should have been dealt with by the begin block code
                elif match := re.fullmatch(r"([\$\*xfcrpl]/)", trimmed):
                    tidy_function()
                    next_line_rowcol = IndexRowCol(self.index(WRAP_NEXT_LINE_MARK))
                    logger.error(
                        f"{match[1]} markup error at line {next_line_rowcol.row - 1}"
                    )
                    return
                else:
                    # Is it the first line of a paragraph?
                    if not paragraph:
                        paragraph_start = line_start
                    paragraph += line
            else:
                # Blank line - end of paragraph
                paragraph_complete = True

            if paragraph_complete and paragraph:
                self.wrap_paragraph(
                    paragraph_start,
                    line_start,
                    paragraph,
                    block_params_list[bq_depth],
                    wrapper,
                )
                paragraph = ""

            if bq_depth_change < 0:
                # Exiting a block level - discard the params
                try:
                    block_params_list.pop()
                    if len(block_params_list) <= 0:
                        raise IndexError
                except IndexError:
                    tidy_function()
                    next_line_rowcol = IndexRowCol(self.index(WRAP_NEXT_LINE_MARK))
                    logger.error(
                        f"Block quote markup error at line {next_line_rowcol.row - 1}"
                    )
                    return
            bq_depth += bq_depth_change

        # Output any last paragraph
        if paragraph:
            # If paragraph runs right to end of file, ensure it has a terminating newline
            if (
                IndexRowCol(maintext().index(f"{line_start} +1l")).row
                == maintext().end().row
            ):
                maintext().insert(tk.END, "\n")
            self.wrap_paragraph(
                paragraph_start,
                f"{line_start} +1l",
                paragraph,
                block_params_list[bq_depth],
                wrapper,
            )
        tidy_function()

    def wrap_paragraph(
        self,
        paragraph_start: str,
        paragraph_end: str,
        paragraph: str,
        wrap_params: WrapParams,
        wrapper: TextWrapper,
    ) -> None:
        """Wrap a complete paragraph and replace it in the text.

        Args:
            paragraph_start: Index of start of paragraph.
            paragraph_end: Index of end of paragraph (beginning of line following paragraph).
            paragraph: Text of the paragraph to be wrapped.
            wrap_params: Wrapping parameters.
            wrapper: TextWrapper object to perform the wrapping - re-used for efficiency.
        """
        if paragraph.startswith("       *" * 5):
            return
        # Remove leading/trailing space
        paragraph = paragraph.strip()
        # Replace all multiple whitespace with single space
        paragraph = re.sub(r"\s+", " ", paragraph)
        # Don't want pagemark pins to trap spaces around them, so...
        # Remove space between pagemark pins
        paragraph = re.sub(rf"(?<={PAGEMARK_PIN}) (?={PAGEMARK_PIN})", "", paragraph)
        # Remove space after pagemark pins if space (or linestart) before
        paragraph = re.sub(rf"(( |^){PAGEMARK_PIN}+) ", r"\1", paragraph)
        # Remove space before pagemark pins if space (or lineend) after
        paragraph = re.sub(rf" ({PAGEMARK_PIN}+( |$)) ", r"\1", paragraph)

        wrapper.width = wrap_params.right
        wrapper.initial_indent = wrap_params.first * " "
        wrapper.subsequent_indent = wrap_params.left * " "

        wrapped = wrapper.fill(paragraph)
        self.delete(paragraph_start, paragraph_end)
        self.insert(paragraph_start, wrapped + "\n")

    def wrap_center_block(
        self, start_index: str, end_index: str, center_point: int
    ) -> None:
        """Center each line in the block between start_index and end_index
        within the given margins.

        Args:
            start_index: Beginning of first line to center.
            end_index: Beginning of line immediately after text block (the "c/" line).
            center_point: Column to center on.
        """
        line_start = start_index
        while self.compare(line_start, "<", end_index):
            next_start = self.index(f"{line_start} +1l")
            left_limit, right_limit = self.wrap_get_block_limits(line_start, next_start)
            indent = center_point - int((right_limit + left_limit) / 2)
            self.wrap_reindent_block(line_start, next_start, indent)
            line_start = next_start

    def wrap_index_block(
        self,
        start_index: str,
        end_index: str,
        wrap_margin: int,
        main_margin: int,
        right_margin: int,
        wrapper: TextWrapper,
    ) -> None:
        """Wrap the index section between start_index and end_index.

        Index must have been formatted according to DP guidelines, i.e one entry per line,
        indented for sublevels, etc.

        Args:
            start_index: Beginning of first line to center.
            end_index: Beginning of line immediately after text block (the "i/" line).
            left_margin: Left margin that long lines wrap to.
            main_margin: Left margin for main index entries
            right_margin: Right margin to wrap between.
            wrapper: TextWrapper object to perform the wrapping - re-used for efficiency.
        """
        # Mark end_index in case wrapping below changes line numbering
        self.set_mark_position(
            INDEX_END_MARK,
            IndexRowCol(end_index),
            tk.RIGHT,
        )
        line_start = start_index
        while self.compare(line_start, "<", INDEX_END_MARK):
            line_end = self.index(f"{line_start} lineend")
            line = self.get(line_start, line_end).rstrip()
            # No need to wrap blank lines - just move on to the next l
            if line == "":
                line_start = self.index(f"{line_start}+1l")
                continue
            # Don't include pagemark pins in calculations, since removed after wrapping
            line_no_pin = line.replace(PAGEMARK_PIN, "")
            match = re.match(r"( +)", line_no_pin)
            indent = len(match[1]) if match else 0

            # Mark next line postion in case wrapping below changes line numbering
            self.set_mark_position(
                INDEX_NEXT_LINE_MARK,
                IndexRowCol(self.index(f"{line_start} +1l")),
                tk.RIGHT,
            )
            self.wrap_paragraph(
                line_start,
                f"{line_start}+1l",
                line,
                WrapParams(wrap_margin, main_margin + indent, right_margin),
                wrapper,
            )
            line_start = self.index(INDEX_NEXT_LINE_MARK)

    def wrap_reindent_block(
        self, start_index: str, end_index: str, n_spaces: int
    ) -> None:
        """Re-indent the block by adding/removing spaces at start of lines.

        Args:
            start_index: Beginning of first line to center.
            end_index: Beginning of line immediately after text block (the "c/" line).
            n_spaces: Number of spaces to insert (positive) or delete (negative).
        """
        if n_spaces == 0:
            return
        line_start = start_index
        while self.compare(line_start, "<", end_index):
            line = self.get(line_start, f"{line_start} lineend")
            # Don't include pagemark pins in calculations, since removed after wrapping
            line_no_pin = line.replace(PAGEMARK_PIN, "")
            if length := len(line_no_pin):
                if n_spaces < 0 and length >= -n_spaces:
                    # Can't just delete first few chars, since they may be pagemark pins, not spaces
                    # So, replace just spaces in the line, then replace whole line in text widget
                    line = line.replace(" ", "", -n_spaces)
                    self.delete(line_start, f"{line_start} lineend")
                    self.insert(line_start, line)
                else:
                    self.insert(line_start, n_spaces * " ")
            line_start = self.index(f"{line_start} +1l")

    def wrap_get_block_limits(
        self, start_index: str, end_index: str
    ) -> tuple[int, int]:
        """Get the min left and max right non-space columns of the given block. Also ignore
        pagemark pin characters.

        Args:
            start_index: Beginning of first line of block.
            end_index: Beginning of line immediately after text block (the closing markup line).

        Returns:
            Tuple contain columns of leftmost & rightmost non-space chars in block.
        """
        line_start = start_index
        min_left = 1000
        max_right = 0
        while self.compare(line_start, "<", end_index):
            line_end = self.index(f"{line_start} lineend")
            strip_line = (
                self.get(line_start, line_end).replace(PAGEMARK_PIN, "").rstrip()
            )
            right_col = len(strip_line)
            max_right = max(max_right, right_col)
            if right_col > 0:
                left_col = right_col - len(strip_line.lstrip())
                min_left = min(min_left, left_col)
            line_start = self.index(f"{line_start} +1l")
        return min_left, max_right

    def wrap_interpret_margins(
        self,
        match_left: Optional[str],
        match_first: Optional[str],
        match_right: Optional[str],
        default_left: int,
        default_first: int,
        default_right: int,
    ) -> tuple[int, int, int]:
        """Interpret margins from markup, e.g. `/#[6.4,72]`.

        Args:
            match_left: Matched string for left margin.
            match_first: Matched string for first line's left margin.
            match_right: Matched string for right margin.
            default_left: Default value for left margin if not specified in match.
            default_first: Default value for first line's left margin if neither first nor left specified in match.
            default_right: Default value for right margin if not specified in match.

        Returns:
            Tuple containing the three values.
        """
        new_left = default_left if match_left is None else int(match_left[1:])
        if match_first is None:
            new_first = default_first if match_left is None else new_left
        else:
            new_first = int(match_first[1:])
        new_right = default_right if match_right is None else int(match_right[1:])
        return new_left, new_first, new_right

    def wrap_interpret_single_margin(
        self,
        match_group: Optional[str],
        default_value: int,
    ) -> int:
        """Interpret single margin from markup, e.g. `/*[6]`.

        Args:
            match_group: Matched string for single value.
            default_value: Default value for margin if not specified in match.

        Returns:
            The margin value.
        """
        return default_value if match_group is None else int(match_group[1:])

    def strip_end_of_line_spaces(self) -> None:
        """Remove end-of-line spaces from all lines."""
        start = "1.0"
        while start := self.search(" +$", start, regexp=True):
            self.delete(start, f"{start} lineend")

    def get_current_page_mark(self) -> str:
        """Find page mark corresponding to where the insert cursor is.

        Returns:
            Name of preceding mark. Empty string if none found.
        """
        insert = self.get_insert_index().index()
        mark = insert
        good_mark = ""
        # First check for page marks at the current cursor position & return last one
        while (mark := self.page_mark_next(mark)) and self.compare(mark, "==", insert):
            good_mark = mark
        # If not, then find page mark before current position
        if not good_mark:
            if mark := self.page_mark_previous(insert):
                good_mark = mark
        # If not, then maybe we're before the first page mark, so search forward
        if not good_mark:
            if mark := self.page_mark_next(insert):
                good_mark = mark
        return good_mark

    def get_current_image_name(self) -> str:
        """Find basename of the image file corresponding to where the
        insert cursor is.

        Returns:
            Basename of image file. Empty string if none found.
        """
        mark = self.get_current_page_mark()
        if mark == "":
            return ""
        return img_from_page_mark(mark)

    def selection_cursor(self) -> None:
        """Make the insert cursor (in)visible depending on selection."""
        current = maintext().cget("insertontime")
        ontime = 0 if maintext().selected_ranges() else 600
        if ontime != current:
            maintext().configure(insertontime=ontime)

    def is_dark_theme(self) -> bool:
        """Returns True if theme is dark, which is assumed to be the case if
        the brightness of the text color is greater than half strength (mid-gray)."""
        text_color = maintext().cget("foreground")
        rgb_sum = sum(self.winfo_rgb(text_color))  # 0-65535 for each component
        return rgb_sum > 12767 * 3

    def _highlight_configure_tag(
        self, tag_name: str, tag_colors: dict[str, dict[str, str]]
    ) -> None:
        """Configure highlighting tag colors to match the theme.

        Args:
            tag_name: Tag to be configured.
            tag_colors: Dictionary of fg/bg colors for each theme.
        """
        if self.dark_theme:
            theme = "Dark"
        else:
            theme = "Light"

        self.tag_configure(
            tag_name,
            background=tag_colors[theme]["bg"],
            foreground=tag_colors[theme]["fg"],
        )

    def highlight_selection(
        self,
        pat: str,
        tag_name: str,
        nocase: bool = False,
        regexp: bool = False,
    ) -> None:
        """Highlight matches in the current selection.
        Args:
            pat: string or regexp to find in the current selection
            tag_name: tkinter tag to apply to matched text region(s)
        Optional keyword args:
            nocase (default False): set True for case-insensitivity
            regexp (default False): whether to assume 's' is a regexp
        """

        if not (ranges := self.selected_ranges()):
            return

        for _range in ranges:
            matches = self.find_matches(pat, _range, nocase=nocase, regexp=regexp)
            for match in matches:
                self.tag_add(
                    tag_name, match.rowcol.index(), match.rowcol.index() + "+1c"
                )

    def remove_highlights(self) -> None:
        """Remove active highlights."""
        self.tag_remove(HighlightTag.QUOTEMARK, "1.0", tk.END)

    def highlight_quotemarks(self, pat: str) -> None:
        """Highlight quote marks in current selection which match a pattern."""
        self.remove_highlights()
        self.highlight_selection(pat, HighlightTag.QUOTEMARK, regexp=True)

    def highlight_single_quotes(self) -> None:
        """Highlight single quotes (straight or curly) in current selection."""
        self.highlight_quotemarks("['‘’]")

    def highlight_double_quotes(self) -> None:
        """Highlight double quotes (straight or curly) in current selection."""
        self.highlight_quotemarks('["“”]')

    def spotlight_range(self, spot_range: IndexRange) -> None:
        """Highlight the given range in the spotlight color.

        Args:
            spot_range: The range to be spotlighted.
        """
        self.remove_spotlights()
        self.tag_add(
            HighlightTag.SPOTLIGHT, spot_range.start.index(), spot_range.end.index()
        )

    def remove_spotlights(self) -> None:
        """Remove active spotlights"""
        self.tag_remove(HighlightTag.SPOTLIGHT, "1.0", tk.END)

    def get_screen_window_coordinates(
        self, viewport: Text, offscreen_lines: int = 5
    ) -> tuple[str, str]:
        """
        Find start and end coordinates for a viewport (with a margin of offscreen
        text added for padding).

        Args:
            viewport: the viewport to inspect
            offscreen_lines: optional count of offscreen lines to inspect (default: 5)
        """
        (top_frac, bot_frac) = viewport.yview()
        # use maintext() here, not view - there is no TextPeer.rowcol()
        end_index = self.rowcol("end")

        # Don't try to go beyond the boundaries of the document.
        #
        # {top,bot}_frac contain a fractional number representing a percentage into
        # the document; do some math to calculate what the top or bottom row in the
        # viewport should be, then use min/max to make sure that value isn't less
        # than 1 or more than the total row count.
        top_line = max(int((top_frac * end_index.row) - offscreen_lines), 1)
        bot_line = min(int((bot_frac * end_index.row) + offscreen_lines), end_index.row)

        return (f"{top_line}.0", f"{bot_line}.0")

    def search_for_base_character_in_pair(
        self,
        top_index: str,
        searchfromindex: str,
        bot_index: str,
        startchar: str,
        endchar: str,
        *,
        backwards: bool = False,
        charpair: str = "",
    ) -> str:
        """
        If searching backward, count characters (e.g. parenthesis) until finding a
        startchar which does not have a forward matching endchar.

        (<= search backward will return this index
        ()
        START X HERE
        ( (  )  () )
        )<== search forward will return this index

        If searching forward, count characters until finding an endchar which does
        not have a rearward matching startchar.

        Default search direction is forward.

        If charpair is not specified, a default regex is constructed from startchar,
        endchar using f"[{startchar}{endchar}]". For example,

        startchar='(', endchar=')' results in: charpar='[()]'
        """

        forwards = True
        if backwards:
            forwards = False

        if not charpair:
            if startchar == endchar:
                charpair = startchar
            else:
                charpair = f"[{startchar}{endchar}]"

        if forwards:
            plus_one_char = endchar
            search_end_index = bot_index
            index_offset = " +1c"
            done_index = self.index("end")
        else:
            plus_one_char = startchar
            search_end_index = top_index
            index_offset = ""
            done_index = "1.0"

        at_done_index = False
        count = 0

        while True:
            searchfromindex = self.search(
                charpair,
                searchfromindex,
                search_end_index,
                backwards=backwards,
                forwards=forwards,
                regexp=True,
            )

            if not searchfromindex:
                break

            # get one character at the identified index
            char = self.get(searchfromindex)
            if char == plus_one_char:
                count += 1
            else:
                count -= 1

            if count == 1:
                break

            # boundary condition exists when first char in widget is the match char
            # need to be able to determine if search tried to go past index '1.0'
            # if so, set index to undef and return.
            if at_done_index:
                searchfromindex = ""
                break

            if searchfromindex == done_index:
                at_done_index = True

            searchfromindex = self.index(f"{searchfromindex}{index_offset}")

        return searchfromindex

    def highlight_single_pair_bracketing_cursor(
        self,
        startchar: str,
        endchar: str,
        tag_name: str,
        *,
        charpair: str = "",
    ) -> None:
        """
        Search for a pair of matching characters that bracket the cursor and tag
        them with the given tagname. If charpair is not specified, a default regex
        of f"[{startchar}{endchar}]" will be used.

        If a selection is active, the entire selected region is considered to be
        the cursor, and pairs surrounding that region are marked; characters
        inside the selected region will not be considered.

        Args:
            startchar: opening char of pair (e.g. '(')
            endchar: closing chair of pair (e.g. ')')
            tag_name: name of tag for highlighting (class HighlightTag)
            charpair: optional regex override for matching the pair (e.g. '[][]')
        """
        self.tag_remove(tag_name, "1.0", tk.END)
        cursor = self.get_insert_index().index()

        (top_index, bot_index) = self.get_screen_window_coordinates(
            self.focus_widget(), 80
        )

        if sel_ranges := maintext().selected_ranges():
            cursor = sel_ranges[0].start.index()
        else:
            cursor = maintext().get_insert_index().index()

        # search backward for the startchar
        startindex = self.search_for_base_character_in_pair(
            top_index,
            cursor,
            bot_index,
            startchar,
            endchar,
            charpair=charpair,
            backwards=True,
        )
        if not startindex:
            return

        if sel_ranges:
            cursor = sel_ranges[-1].end.index()

        # search forward for the endchar
        endindex = self.search_for_base_character_in_pair(
            top_index, cursor, bot_index, startchar, endchar, charpair=charpair
        )

        if not (startindex and endindex):
            return

        self.tag_add(tag_name, startindex, self.index(f"{startindex}+1c"))
        self.tag_add(tag_name, endindex, self.index(f"{endindex}+1c"))

    def highlight_parens_around_cursor(self) -> None:
        """Highlight pair of parens that most closely brackets the cursor."""
        self.highlight_single_pair_bracketing_cursor(
            "(",
            ")",
            HighlightTag.PAREN,
        )

    def highlight_curly_brackets_around_cursor(self) -> None:
        """Highlight pair of curly brackets that most closely brackets the cursor."""
        self.highlight_single_pair_bracketing_cursor(
            "{",
            "}",
            HighlightTag.CURLY_BRACKET,
        )

    def highlight_square_brackets_around_cursor(self) -> None:
        """Highlight pair of square brackets that most closely brackets the cursor."""
        self.highlight_single_pair_bracketing_cursor(
            "[",
            "]",
            HighlightTag.SQUARE_BRACKET,
            charpair="[][]",
        )

    def highlight_double_quotes_around_cursor(self) -> None:
        """Highlight pair of double quotes that most closely brackets the cursor."""
        self.highlight_single_pair_bracketing_cursor(
            '"',
            '"',
            HighlightTag.STRAIGHT_DOUBLE_QUOTE,
        )
        self.highlight_single_pair_bracketing_cursor(
            "“",
            "”",
            HighlightTag.CURLY_DOUBLE_QUOTE,
        )

    def highlight_single_quotes_around_cursor(self) -> None:
        """Highlight pair of single quotes that most closely brackets the cursor."""
        self.highlight_single_pair_bracketing_cursor(
            "'",
            "'",
            HighlightTag.STRAIGHT_SINGLE_QUOTE,
        )
        self.highlight_single_pair_bracketing_cursor(
            "‘",
            "’",
            HighlightTag.CURLY_SINGLE_QUOTE,
        )

    def highlight_quotbrac(self) -> None:
        """Highlight all the character pairs that most closely bracket the cursor."""
        if preferences.get(PrefKey.HIGHLIGHT_QUOTBRAC):
            self.highlight_parens_around_cursor()
            self.highlight_curly_brackets_around_cursor()
            self.highlight_square_brackets_around_cursor()
            self.highlight_double_quotes_around_cursor()
            self.highlight_single_quotes_around_cursor()

    def remove_highlights_quotbrac(self) -> None:
        """Remove highlights for quotes & brackets"""
        for tag in (
            HighlightTag.PAREN,
            HighlightTag.CURLY_BRACKET,
            HighlightTag.SQUARE_BRACKET,
            HighlightTag.STRAIGHT_DOUBLE_QUOTE,
            HighlightTag.CURLY_DOUBLE_QUOTE,
            HighlightTag.STRAIGHT_SINGLE_QUOTE,
            HighlightTag.CURLY_SINGLE_QUOTE,
        ):
            self.tag_remove(tag, "1.0", tk.END)

    def highlight_aligncol_in_viewport(self, viewport: Text) -> None:
        """Do highlighting of the alignment column in a single viewport."""
        (top_index, bot_index) = self.get_screen_window_coordinates(viewport)

        col = self.aligncol
        row = IndexRowCol(top_index).row
        end_row = IndexRowCol(bot_index).row

        while row <= end_row:
            # find length of row; don't highlight if row is too short to contain col
            rowlen = int(self.index(f"{row}.0 lineend").split(".")[1])
            if 0 <= col < rowlen:
                self.tag_add(HighlightTag.ALIGNCOL, f"{row}.{col}")
            row += 1

    def highlight_aligncol(self) -> None:
        """Add a highlight to all characters in the alignment column."""
        # Check that alignment column is 0 or higher; there are no negative
        # columns in a textview. Since the column is decremented when align
        # highlight is turned on, it's possible that this value is set to -1.
        if self.aligncol_active.get() and self.aligncol >= 0:
            self.tag_remove(HighlightTag.ALIGNCOL, "1.0", tk.END)

            self.highlight_aligncol_in_viewport(self)
            if PrefKey.SPLIT_TEXT_WINDOW:
                self.highlight_aligncol_in_viewport(self.peer)

    def remove_highlights_aligncol(self) -> None:
        """Remove highlights for alignment column"""
        self.tag_remove(HighlightTag.ALIGNCOL, "1.0", tk.END)

    def highlight_aligncol_callback(self, value: bool) -> None:
        """Callback when highlight_aligncol active state is changed."""
        if value:
            col = self.get_insert_index().col
            if col == 0:
                logger.error(
                    "Can't create an alignment column at column 0. Choose another column."
                )
                self.aligncol_active.set(False)
                return

            # Highlight column immediately preceding cursor for consistency with ruler.
            # (There is no ruler as of today, but we'll implement one in GG2 at some point...)
            self.aligncol = col - 1
            self.highlight_aligncol()
        else:
            self.aligncol = -1
            self.remove_highlights_aligncol()

    def highlight_cursor_line(self) -> None:
        """Add a highlight to entire line cursor is focused on."""
        self.tag_remove(HighlightTag.CURSOR_LINE, "1.0", tk.END)

        # Don't re-highlight if there's currently a selection
        if not self.selected_ranges():
            row = self.get_insert_index().row
            self.tag_add(HighlightTag.CURSOR_LINE, f"{row}.0", f"{row + 1}.0")

    def highlight_configure_tags(self, first_run: bool = False) -> None:
        """Configure highlight tags with colors based on the current theme.
        On first run, will also initialize the tag stack order.

        Args:
            first_run: if True, will set the tag ordering/priority
        """
        colors_need_update = False
        order_needs_update = False
        dark_theme = self.is_dark_theme()

        if first_run:
            colors_need_update = True
            order_needs_update = True
            self.dark_theme = dark_theme
        elif self.dark_theme != dark_theme:
            colors_need_update = True
            self.dark_theme = dark_theme

        if not colors_need_update:
            return

        # Loop through a list of tags, in order of priority. Earlier in the list will
        # take precedence over later in the list; that is, the first entry in this
        # list will win over the second; the second wins over the third; and so on
        # down the line.
        #
        # ** THE ORDER MATTERS HERE **
        #
        for tag, colors in (
            (HighlightTag.QUOTEMARK, HighlightColors.QUOTEMARK),
            # "sel" is for active selections - don't override the default color
            ("sel", None),
            (HighlightTag.SPOTLIGHT, HighlightColors.SPOTLIGHT),
            (HighlightTag.PAREN, HighlightColors.PAREN),
            (HighlightTag.CURLY_BRACKET, HighlightColors.CURLY_BRACKET),
            (HighlightTag.SQUARE_BRACKET, HighlightColors.SQUARE_BRACKET),
            (HighlightTag.STRAIGHT_DOUBLE_QUOTE, HighlightColors.STRAIGHT_DOUBLE_QUOTE),
            (HighlightTag.CURLY_DOUBLE_QUOTE, HighlightColors.CURLY_DOUBLE_QUOTE),
            (HighlightTag.STRAIGHT_SINGLE_QUOTE, HighlightColors.STRAIGHT_SINGLE_QUOTE),
            (HighlightTag.CURLY_SINGLE_QUOTE, HighlightColors.CURLY_SINGLE_QUOTE),
            (HighlightTag.ALIGNCOL, HighlightColors.ALIGNCOL),
            (HighlightTag.CURSOR_LINE, HighlightColors.CURSOR_LINE),
        ):
            if colors:
                self._highlight_configure_tag(tag, colors)
            if order_needs_update:
                self.tag_lower(tag)

    def _autoscroll_start(
        self, event: tk.Event  # pylint: disable=unused-argument
    ) -> None:
        """When Button-1 is pressed, turn mouse-drag monitoring on"""
        self._autoscroll_active = True
        self._on_change()

    def _autoscroll_stop(
        self, event: tk.Event  # pylint: disable=unused-argument
    ) -> None:
        """When Button-1 is released, turn mouse-drag monitoring off"""
        self._autoscroll_active = False
        self._on_change()

    def _autoscroll_callback(self, event: tk.Event) -> None:
        """As long as mouse position monitoring is active, continue running
        a callback every (n) ms to watch mouse cursor position, scrolling
        the view as appropriate based on its current position.
        """
        if not self._autoscroll_active:
            return

        # Convert screen coordinates to widget coordinates. Just using the
        # winfo_width, winfo_height data results in endless scrolling.
        widget_x, widget_y = event.widget.winfo_pointerxy()
        widget_x -= event.widget.winfo_rootx()
        widget_y -= event.widget.winfo_rooty()

        width, height = event.widget.winfo_width(), event.widget.winfo_height()

        # Stop scrolling if the mouse pointer is within the text widget bounds
        if 0 <= widget_x <= width and 0 <= widget_y <= height:
            return

        out_of_bounds_distance = 0
        out_of_bounds_direction = ""

        if widget_x < 0:
            out_of_bounds_distance = abs(widget_x)
            out_of_bounds_direction = "left"
        elif widget_x > width:
            out_of_bounds_distance = abs(widget_x - width)
            out_of_bounds_direction = "right"
        elif widget_y < 0:
            out_of_bounds_distance = abs(widget_y)
            out_of_bounds_direction = "up"
        elif widget_y > height:
            out_of_bounds_distance = abs(widget_y - height)
            out_of_bounds_direction = "down"

        # Attempt to "smooth scroll" by basing the delay on math rather than
        # choosing some arbitrary boundaries. Enforce a minimum and maximum
        # value, otherwise we start at about 5 seconds and get to ridiculous
        # minimums.
        scroll_delay_fastest = 100
        scroll_delay_slowest = 500
        scroll_delay = max(
            scroll_delay_slowest,
            min(scroll_delay_fastest, (5000 / out_of_bounds_distance)),
        )

        # Increase speed for horizontal scrolling
        if out_of_bounds_direction in ("left", "right"):
            scroll_delay = scroll_delay / 1.5

        # Scroll in the appropriate direction (x or y).
        if widget_y < 0:
            event.widget.yview_scroll(-1, "units")
        elif widget_y > height:
            event.widget.yview_scroll(1, "units")
        elif widget_x < 0:
            event.widget.xview_scroll(-1, "units")
        elif widget_x > width:
            event.widget.xview_scroll(1, "units")

        self._on_change()
        self.after(int(scroll_delay), lambda: self._autoscroll_callback(event))


def img_from_page_mark(mark: str) -> str:
    """Get base image name from page mark, e.g. "Pg027" gives "027".

    Args:
        mark: String containing name of mark whose image is needed.
          Does not check if mark is a page mark. If it is not, the
          full string is returned.

    Returns:
        Image name.
    """
    return mark.removeprefix(PAGEMARK_PREFIX)


def page_mark_from_img(img: str) -> str:
    """Get page mark from base image name, e.g. "027" gives "Pg027".

    Args:
        img: Name of png img file whose mark is needed.
          Does not check validity of png img file name.

    Returns:
        Page mark string.
    """
    return PAGEMARK_PREFIX + img


class TclRegexCompileError(Exception):
    """Raise if Tcl fails to compile regex."""


# For convenient access, store the single MainText instance here,
# with a function to set/query it.
_single_widget = None  # pylint: disable=invalid-name


def maintext(text_widget: Optional[MainText] = None) -> MainText:
    """Store and return the single MainText widget"""
    global _single_widget
    if text_widget is not None:
        assert _single_widget is None
        _single_widget = text_widget
    assert _single_widget is not None
    return _single_widget
