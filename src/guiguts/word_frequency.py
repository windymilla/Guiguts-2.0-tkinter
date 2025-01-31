"""Store, analyze and report on word frequency and inconsistencies."""

from enum import StrEnum, auto
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import regex as re

from guiguts.file import PAGE_SEPARATOR_REGEX
from guiguts.maintext import maintext
from guiguts.mainwindow import ScrolledReadOnlyText
from guiguts.misc_tools import tool_save
from guiguts.preferences import (
    preferences,
    PersistentBoolean,
    PersistentString,
    PrefKey,
)
from guiguts.search import SearchDialog
from guiguts.utilities import (
    sing_plur,
    DiacriticRemover,
    IndexRange,
    IndexRowCol,
    sound_bell,
    process_accel,
    cmd_ctrl_string,
    is_mac,
)
from guiguts.widgets import (
    ToplevelDialog,
    Combobox,
    ToolTip,
    mouse_bind,
    Busy,
)

_the_word_lists = None  # pylint: disable=invalid-name

RETURN_ARROW = "⏎"
MARKUP_TYPES = "i|b|sc|f|g|u|cite|em|strong"


class WFDisplayType(StrEnum):
    """Enum class to store Word Frequency display types."""

    ALL_WORDS = auto()
    EMDASHES = auto()
    HYPHENS = auto()
    ALPHANUM = auto()
    ALL_CAPS = auto()
    MIXED_CASE = auto()
    INITIAL_CAPS = auto()
    ACCENTS = auto()
    LIGATURES = auto()
    MARKEDUP = auto()
    CHAR_COUNTS = auto()
    REGEXP = auto()


class WFSortType(StrEnum):
    """Enum class to store Word Frequency sort types."""

    ALPHABETIC = auto()
    FREQUENCY = auto()
    LENGTH = auto()


class WFDict(dict[str, int]):
    """Dictionary containing word and frequency."""


class WFWordLists:
    """Word lists used for Word Frequency analysis.

    Attributes:
        all_words: All the words in the file.
        emdash_words: All pairs of words separated by an emdash/double hyphen.
    """

    def __init__(self) -> None:
        """Initialize a WFWordLists object to hold word frequency information."""
        self.reset()

    def reset(self) -> None:
        """Reset the word lists."""
        self.all_words: WFDict = WFDict()
        self.emdash_words: WFDict = WFDict()

    def ensure_file_analyzed(self) -> None:
        """Analyze file to create word lists, unless already done.

        Call reset method first to force reanalysis."""
        if self.all_words:
            return
        for line, _ in maintext().get_lines():
            if re.search(PAGE_SEPARATOR_REGEX, line):
                continue
            if preferences.get(PrefKey.WFDIALOG_IGNORE_CASE):
                line = line.lower()
            line = re.sub(r"<\/?[a-z]*>", " ", line)  # throw away DP tags
            # get rid of nonalphanumeric (retaining combining characters)
            line = re.sub(r"[^'’\.,\p{Alnum}\p{Mark}*_-]", " ", line)

            def strip_punc(word: str) -> str:
                """Strip relevant leading/trailing punctuation from word."""
                return re.sub(r"^[\.,'’_-]+|[\.,'’_-]+$", "", word)

            # Build a list of emdash words, i.e. "word1--word2"
            words = re.split(r"\s+", line)
            for word in words:
                if re.search(r"[^-](--|—)[^-]", word) and "---" not in word:
                    word = strip_punc(word)
                    tally_word(self.emdash_words, word)

            line = re.sub(r"(--|—)", " ", line)  # double-hyphen/emdash

            words = re.split(r"\s+", line)
            for word in words:
                word = re.sub(r"(?<!\-)\*", "", word)  # * not preceded by hyphen
                word = re.sub(r"^\*$", "", word)  # just *
                word = strip_punc(word)
                # Tally single word
                tally_word(self.all_words, word)

    def get_all_words(self) -> WFDict:
        """Return the list of all words in the file.

        Returns:
            Dictionary of words with their frequencies."""
        self.ensure_file_analyzed()
        return self.all_words

    def get_emdash_words(self) -> WFDict:
        """Return the list of emdash words (word1--word2) in the file.

        Returns:
            Dictionary of emdash words with their frequencies."""
        self.ensure_file_analyzed()
        return self.emdash_words


class WordFrequencyEntry:
    """Class to hold one entry in the Word Frequency dialog.

    Attributes:
        text: Single line of text to display in dialog.
        count: Number of occurrences.
        suspect: True if entry is a "suspect".
    """

    SUSPECT = "****"

    def __init__(self, word: str, frequency: int, suspect: bool) -> None:
        """Initialize WordFrequencyEntry object.

        Args:
            word: Word for this entry.
            frequency: Number of occurrences of word.
        """
        self.word = word
        self.frequency = frequency
        self.suspect = suspect


class WordFrequencyDialog(ToplevelDialog):
    """Dialog to show results of word frequency analysis.

    Attributes:
        text: Text widget to contain results.
    """

    manual_page = "Tools_Menu#Word_Frequency"
    CHAR_DISPLAY: dict[str, str] = {
        " ": "*space*",
        "\u00a0": "*nbsp*",
        "\n": RETURN_ARROW,
    }

    def __init__(
        self,
    ) -> None:
        """Initialize the dialog."""
        super().__init__("Word Frequency")
        self.top_frame.rowconfigure(0, weight=0)
        header_frame = ttk.Frame(self.top_frame, padding=2)
        header_frame.grid(row=0, column=0, sticky="NSEW")
        header_frame.columnconfigure(0, weight=1)

        # Message label
        label_frame = ttk.Frame(header_frame, borderwidth=1, relief=tk.GROOVE)
        label_frame.grid(row=0, column=0, sticky="NSEW")
        # label_frame.columnconfigure(0, weight=1)
        self.message = tk.StringVar()
        ttk.Label(label_frame, textvariable=self.message).grid(
            row=0, column=0, sticky="NSW", padx=5, pady=2
        )

        # Re-run buttons
        rerun_frame = ttk.Frame(header_frame, borderwidth=1, relief=tk.GROOVE)
        rerun_frame.grid(row=0, column=1, rowspan=2, padx=(0, 15))
        ttk.Button(
            rerun_frame, text="Re-run", command=word_frequency, takefocus=False
        ).grid(row=0, column=0, sticky="NSEW", padx=5, pady=2)

        def change_ignore_case() -> None:
            """Handle changing of ignore case flag - enable/disable buttons & re-run tool."""
            self.set_case_sensitive_btns()
            word_frequency()

        ttk.Checkbutton(
            rerun_frame,
            text="Ignore Case",
            command=change_ignore_case,
            variable=PersistentBoolean(PrefKey.WFDIALOG_IGNORE_CASE),
            takefocus=False,
        ).grid(row=1, column=0, sticky="NSEW", padx=5, pady=2)

        # Options
        options_frame = ttk.Frame(
            header_frame, borderwidth=1, relief=tk.GROOVE, padding=2
        )
        options_frame.grid(row=1, column=0, sticky="NSEW")
        options_frame.columnconfigure(0, weight=1)
        self.suspects_btn = ttk.Checkbutton(
            options_frame,
            text="Suspects Only",
            variable=PersistentBoolean(PrefKey.WFDIALOG_SUSPECTS_ONLY),
            command=lambda: wf_populate(self),
            takefocus=False,
        )
        self.suspects_btn.grid(row=0, column=0, sticky="NSW", padx=5)

        def copy_errors() -> None:
            """Copy text messages to clipboard."""
            self.clipboard_clear()
            self.clipboard_append(self.text.get("1.0", tk.END))

        copy_button = ttk.Button(
            options_frame, text="Copy Results", command=copy_errors
        )
        copy_button.grid(row=0, column=1, sticky="NSE", padx=(0, 20))

        ttk.Label(
            options_frame,
            text="Sort:",
        ).grid(row=0, column=2, sticky="NSE", padx=5)
        sort_type = PersistentString(PrefKey.WFDIALOG_SORT_TYPE)
        ttk.Radiobutton(
            options_frame,
            text="Alph",
            command=lambda: wf_populate(self),
            variable=sort_type,
            value=WFSortType.ALPHABETIC,
            takefocus=False,
        ).grid(row=0, column=3, sticky="NSE", padx=2)
        ttk.Radiobutton(
            options_frame,
            text="Freq",
            command=lambda: wf_populate(self),
            variable=sort_type,
            value=WFSortType.FREQUENCY,
            takefocus=False,
        ).grid(row=0, column=4, sticky="NSE", padx=2)
        ttk.Radiobutton(
            options_frame,
            text="Len",
            command=lambda: wf_populate(self),
            variable=sort_type,
            value=WFSortType.LENGTH,
            takefocus=False,
        ).grid(row=0, column=5, sticky="NSE", padx=(2, 5))

        # Display type radio buttons
        display_frame = ttk.Frame(
            header_frame, borderwidth=1, relief=tk.GROOVE, padding=2
        )
        display_frame.grid(row=2, column=0, columnspan=2, sticky="NSEW", padx=(0, 15))
        for col in range(0, 4):
            display_frame.columnconfigure(index=col, weight=1)

        display_type = PersistentString(PrefKey.WFDIALOG_DISPLAY_TYPE)

        def display_radio(
            row: int,
            column: int,
            text: str,
            value: str,
            frame: ttk.Frame = display_frame,
        ) -> ttk.Radiobutton:
            """Add a radio button to change display type.

            Args:
                row: Row to place button.
                column: Column to place button.
                text: Text for button.
                value: Value to be set when button is selected - a constant from WFDisplayType
            """
            button = ttk.Radiobutton(
                frame,
                text=text,
                command=lambda: wf_populate(self),
                variable=display_type,
                value=value,
                takefocus=False,
            )
            button.grid(row=row, column=column, sticky="NSW", padx=5)
            return button

        display_radio(0, 0, "All Words", WFDisplayType.ALL_WORDS)
        display_radio(0, 1, "Emdashes", WFDisplayType.EMDASHES)
        display_radio(0, 2, "Hyphens", WFDisplayType.HYPHENS)
        self.all_caps_btn = display_radio(1, 0, "ALL CAPITALS", WFDisplayType.ALL_CAPS)
        self.mixed_case_btn = display_radio(
            1, 1, "MiXeD CasE", WFDisplayType.MIXED_CASE
        )
        self.initial_caps_btn = display_radio(
            1, 2, "Initial Capitals", WFDisplayType.INITIAL_CAPS
        )
        display_radio(2, 0, "Alpha/Num", WFDisplayType.ALPHANUM)
        display_radio(2, 1, "Accents", WFDisplayType.ACCENTS)
        display_radio(2, 2, "Ligatures", WFDisplayType.LIGATURES)
        display_radio(3, 0, "Character Cnts", WFDisplayType.CHAR_COUNTS)
        italic_frame = ttk.Frame(display_frame)
        italic_frame.grid(row=3, column=1, columnspan=2, sticky="NSEW")
        italic_frame.columnconfigure(index=1, weight=1)
        display_radio(
            0,
            0,
            "Ital/Bold/SC/etc - Word Threshold:",
            WFDisplayType.MARKEDUP,
            italic_frame,
        )

        def is_nonnegative_int(new_value: str) -> bool:
            """Validation routine for Combobox - check value is a non-negative integer,
            also allowing empty string."""
            return re.fullmatch(r"\d*", new_value) is not None

        self.threshold_box = Combobox(
            italic_frame,
            PrefKey.WFDIALOG_ITALIC_THRESHOLD,
            width=6,
            validate=tk.ALL,
            validatecommand=(self.register(is_nonnegative_int), "%P"),
        )
        self.threshold_box.grid(row=0, column=1, sticky="NSEW", padx=(0, 5))
        self.threshold_box.display_latest_value()
        ToolTip(
            self.threshold_box,
            "Only show marked up phrases with no more than this number of words",
        )
        # Override default behavior for combobox up/down arrow
        self.threshold_box.bind("<Up>", lambda _e: self.goto_word_by_arrow(-1))
        self.threshold_box.bind("<Down>", lambda _e: self.goto_word_by_arrow(1))

        def display_markedup(*_args: Any) -> None:
            """Callback to display the marked up words with new threshold value."""
            preferences.set(PrefKey.WFDIALOG_DISPLAY_TYPE, WFDisplayType.MARKEDUP)
            wf_populate(self)

        self.threshold_box.bind("<Return>", display_markedup)
        self.threshold_box.bind("<<ComboboxSelected>>", display_markedup)

        display_radio(4, 0, "Regular Expression", WFDisplayType.REGEXP)
        self.regex_box = Combobox(display_frame, PrefKey.WFDIALOG_REGEX)
        self.regex_box.grid(row=4, column=1, columnspan=2, sticky="NSEW", padx=(0, 5))
        ToolTip(self.regex_box, "Only show words matching this regular expression")
        # Override default behavior for combobox up/down arrow
        self.regex_box.bind("<Up>", lambda _e: self.goto_word_by_arrow(-1))
        self.regex_box.bind("<Down>", lambda _e: self.goto_word_by_arrow(1))

        def display_regexp(*_args: Any) -> None:
            """Callback to display the regex-matching words with new regex."""
            preferences.set(PrefKey.WFDIALOG_DISPLAY_TYPE, WFDisplayType.REGEXP)
            wf_populate(self)

        self.regex_box.bind("<Return>", display_regexp)
        self.regex_box.bind("<<ComboboxSelected>>", display_regexp)
        self.regex_box.display_latest_value()

        # Main display list
        self.top_frame.rowconfigure(1, weight=1)
        self.text = ScrolledReadOnlyText(
            self.top_frame,
            context_menu=False,
            wrap=tk.NONE,
            font=maintext().font,
        )
        self.text.grid(row=1, column=0, sticky="NSEW")
        mouse_bind(self.text, "1", self.goto_word_by_click)
        _, event = process_accel("Cmd/Ctrl+1")
        self.text.bind(event, self.search_word)
        self.text.bind("<Key>", self.goto_word_by_letter)
        _, event = process_accel("Cmd/Ctrl+a")
        self.text.bind(event, lambda _e: self.text.event_generate("<<SelectAll>>"))
        _, event = process_accel("Cmd/Ctrl+A")
        self.text.bind(event, lambda _e: self.text.event_generate("<<SelectAll>>"))
        _, event = process_accel("Cmd/Ctrl+c")
        self.text.bind(event, lambda _e: self.text.event_generate("<<Copy>>"))
        _, event = process_accel("Cmd/Ctrl+C")
        self.text.bind(event, lambda _e: self.text.event_generate("<<Copy>>"))
        self.text.bind("<Home>", lambda _e: self.goto_word(0))
        self.text.bind("<Shift-Home>", lambda _e: self.goto_word(0))
        self.text.bind("<End>", lambda _e: self.goto_word(len(self.entries) - 1))
        self.text.bind("<Shift-End>", lambda _e: self.goto_word(len(self.entries) - 1))
        # Bind same keys as main window uses for top/bottom on Mac.
        # Above bindings work already for Windows
        if is_mac():
            self.text.bind("<Command-Up>", lambda _e: self.goto_word(0))
            self.text.bind("<Shift-Command-Up>", lambda _e: self.goto_word(0))
            self.text.bind(
                "<Command-Down>", lambda _e: self.goto_word(len(self.entries) - 1)
            )
            self.text.bind(
                "<Shift-Command-Down>", lambda _e: self.goto_word(len(self.entries) - 1)
            )
        ToolTip(
            self.text,
            "\n".join(
                [
                    "Left click: Find first match, click again for other matches",
                    f"{cmd_ctrl_string()}-left click: Find using Search dialog",
                ]
            ),
            use_pointer_pos=True,
        )

        self.bind("<Up>", lambda _e: self.goto_word_by_arrow(-1))
        self.bind("<Down>", lambda _e: self.goto_word_by_arrow(1))

        self.previous_word = ""
        # Store tooltips so they can be added/destroyed depending on Ignore Case
        self.tooltip_dict: dict[ttk.Widget, ToolTip] = {}
        self.set_case_sensitive_btns()

        self.minsize(450, 100)
        self.reset()

    def reset(self) -> None:
        """Reset dialog."""
        super().reset()
        self.entries: list[WordFrequencyEntry] = []
        if maintext().winfo_exists():
            maintext().remove_spotlights()
        if not self.text.winfo_exists():
            return
        self.text.delete("1.0", tk.END)
        self.message.set("")

    def set_case_sensitive_btns(self) -> None:
        """Enable/disable buttons depending on Ignore Case setting.

        Show/hide a tooltip to explain to the user.
        """
        ignore_case = preferences.get(PrefKey.WFDIALOG_IGNORE_CASE)
        for widget in (self.all_caps_btn, self.mixed_case_btn, self.initial_caps_btn):
            widget["state"] = tk.DISABLED if ignore_case else tk.NORMAL
            if ignore_case:
                self.tooltip_dict[widget] = ToolTip(
                    widget, "Cannot be used with 'Ignore Case'"
                )
            else:
                try:
                    self.tooltip_dict[widget].destroy()
                except (tk.TclError, KeyError):
                    pass  # OK for tooltip not to exist

    def add_entry(self, word: str, frequency: int, suspect: bool = False) -> None:
        """Add an entry to be displayed in the dialog.

        Args:
            word: Word for this entry.
            frequency: Number of occurrences of word.
            suspect: Optional bool to flag this word as "suspect".
        """
        entry = WordFrequencyEntry(word, frequency, suspect)
        self.entries.append(entry)

    def display_entries(self) -> None:
        """Display all the stored entries in the dialog according to
        the sort setting."""

        def sort_key_alpha(
            entry: WordFrequencyEntry,
        ) -> tuple[str, ...]:
            no_dia = DiacriticRemover.remove_diacritics(entry.word)
            return (no_dia.lower(), no_dia, entry.word)

        def sort_key_alpha_no_markup(
            entry: WordFrequencyEntry,
        ) -> tuple[str, ...]:
            unmarked = re.sub(rf"^<({MARKUP_TYPES})>", "", entry.word)
            unmarked = re.sub(rf"</({MARKUP_TYPES})>$", "", unmarked)
            unmarked_no_dia = DiacriticRemover.remove_diacritics(unmarked)
            no_dia = DiacriticRemover.remove_diacritics(entry.word)
            return (
                unmarked_no_dia.lower(),
                unmarked_no_dia,
                no_dia.lower(),
                no_dia,
                entry.word,
            )

        def sort_key_freq(entry: WordFrequencyEntry) -> tuple[int | str, ...]:
            no_dia = DiacriticRemover.remove_diacritics(entry.word)
            return (-entry.frequency,) + (no_dia.lower(), no_dia, entry.word)

        def sort_key_len(entry: WordFrequencyEntry) -> tuple[int | str, ...]:
            no_dia = DiacriticRemover.remove_diacritics(entry.word)
            return (-len(entry.word), no_dia.lower(), no_dia, entry.word)

        key: Callable[[WordFrequencyEntry], tuple]
        match preferences.get(PrefKey.WFDIALOG_SORT_TYPE):
            case WFSortType.ALPHABETIC:
                key = (
                    sort_key_alpha_no_markup
                    if preferences.get(PrefKey.WFDIALOG_DISPLAY_TYPE)
                    == WFDisplayType.MARKEDUP
                    else sort_key_alpha
                )
            case WFSortType.FREQUENCY:
                key = sort_key_freq
            case WFSortType.LENGTH:
                key = sort_key_len
            case _ as bad_value:
                assert False, f"Invalid WFSortType: {bad_value}"

        # Sort stored list, rather than just displayed list, since later
        # we'll want to index into list based on index in display.
        self.entries.sort(key=key)
        # Get longest frequency to aid formatting
        max_freq = 0
        for entry in self.entries:
            max_freq = max(max_freq, entry.frequency)
        max_freq_len = len(str(max_freq))
        # Display entries
        for entry in self.entries:
            suspect = f" {WordFrequencyEntry.SUSPECT}" if entry.suspect else ""
            # Single whitespace characters are replaced with a visible label
            try:
                word = WordFrequencyDialog.CHAR_DISPLAY[entry.word]
            except KeyError:
                word = entry.word
            message = f"{entry.frequency:>{max_freq_len}}  {word}{suspect}\n"
            self.text.insert(tk.END, message)

    def whole_word_search(self, word: str) -> bool:
        """Return if a whole word search should be done for given word.

        Args:
            word: The "word" being searched for (may be multiple word string).

        Returns:
            True if "word" should be searched for with "wholeword" flag. This is
            not the case for character count or words that begin/end with
            a non-word character."""
        return not (
            preferences.get(PrefKey.WFDIALOG_DISPLAY_TYPE) == WFDisplayType.CHAR_COUNTS
            or re.search(r"^\W|\W$", word)
        )

    def goto_word_by_click(self, event: tk.Event) -> str:
        """Go to first/next occurrence of word selected by `event`.

        If a different word to last click, then start search from beginning
        of file, otherwise just continue from current location.

        Args:
            event: Event containing mouse click coordinates.

        Returns:
            "break" to stop further processing of event
        """
        try:
            entry_index = self.entry_index_from_click(event)
        except IndexError:
            return "break"
        self.goto_word(entry_index)
        return "break"

    def goto_word_by_letter(self, event: tk.Event) -> str:
        """Go to first occurrence of word beginning with the character in `event`.

        Args:
            event: Event containing keystroke.

        Returns:
            "break" to stop further processing of events if valid character
        """
        if not event.char:
            return ""
        low_char = event.char.lower()
        for idx, entry in enumerate(self.entries):
            if entry.word.lower().startswith(low_char):
                self.text.select_line(idx + 1)
                return "break"
        return ""

    def goto_word_by_arrow(self, increment: int) -> str:
        """Select next/previous line in dialog, and jump to the line in the
        main text widget that corresponds to it.

        Args:
            increment: +1 to move to next line, -1 to move to previous line.
        """
        entry_index = self.text.get_select_line_num()
        if entry_index is not None:
            entry_index -= 1
        if (
            entry_index is None
            or entry_index + increment < 0
            or entry_index + increment >= len(self.entries)
        ):
            return "break"
        self.goto_word(entry_index + increment)
        return "break"

    def goto_word(self, entry_index: int, force_first: bool = False) -> None:
        """Go to first/next occurrence of word listed at `entry_index`.

        If a different word to last click, then start search from beginning
        of file, otherwise just continue from current location.

        Args:
            entry_index: Index into `self.entries` indicating which to select.
            force_first: True to force first occurrence, False for next occurrence.
        """
        if entry_index >= len(self.entries):
            return
        self.text.tag_remove("sel", "1.0", tk.END)
        self.text.select_line(entry_index + 1)
        self.text.mark_set(tk.INSERT, f"{entry_index + 1}.0")
        self.text.focus()
        word = self.entries[entry_index].word
        if word == self.previous_word and not force_first:
            start = maintext().get_insert_index()
            start.col += 1
        else:
            start = maintext().start()
        # Special handling for newline characters (displayed as RETURN_ARROW)
        newline_word = (
            re.sub(RETURN_ARROW, "\n", word) if RETURN_ARROW in word else word
        )
        # Also special handling for non-marked-up phrase from marked-up check
        # (Regex to specifically find non-marked-up version), for char count,
        # and for words that begin/end with non-word chars
        if preferences.get(
            PrefKey.WFDIALOG_DISPLAY_TYPE
        ) == WFDisplayType.MARKEDUP and not word.startswith("<"):
            regexp = True
            match_word = r"(^|[^>\w])" + re.escape(newline_word) + r"($|[^<\w])"
        elif (
            preferences.get(PrefKey.WFDIALOG_DISPLAY_TYPE) == WFDisplayType.CHAR_COUNTS
        ):
            regexp = False
            match_word = newline_word
        elif self.whole_word_search(word):
            regexp = True
            match_word = (
                rf"\y{re.escape(newline_word)}\y"  # Tcl regex: \y is word boundary
            )
        else:  # Word begins/ends with non-word char - do manual whole-word
            regexp = True
            match_word = r"(^|\W)" + re.escape(newline_word) + r"($|\W)"

        # If hyphen matching and there's one (escaped) space in the word, let that match
        # any amount of whitespace
        if (
            preferences.get(PrefKey.WFDIALOG_DISPLAY_TYPE) == WFDisplayType.HYPHENS
            and r"\ " in match_word
        ):
            regexp = True
            match_word = match_word.replace(r"\ ", r"[\n ]+")

        match = maintext().find_match(
            match_word,
            IndexRange(start, maintext().end()),
            nocase=preferences.get(PrefKey.WFDIALOG_IGNORE_CASE),
            regexp=regexp,
            backwards=False,
        )
        if match is None:
            sound_bell()
        else:
            # If searched with regex, it may have matched up to 1 extra character each side,
            # so adjust match length (count) and start column to match the actual word.
            if regexp:
                match_str = maintext().get_match_text(match)
                match.count = len(newline_word)
                if preferences.get(PrefKey.WFDIALOG_IGNORE_CASE):
                    if match_str[1 : match.count + 1].lower() == newline_word.lower():
                        match.rowcol.col += 1
                else:
                    if match_str[1 : match.count + 1] == newline_word:
                        match.rowcol.col += 1
            maintext().set_insert_index(match.rowcol, focus=False)
            maintext().remove_spotlights()
            start_index = match.rowcol.index()
            end_index = maintext().index(start_index + f"+{match.count}c")
            maintext().spotlight_range(IndexRange(start_index, end_index))
            self.previous_word = word

    def search_word(self, event: tk.Event) -> str:
        """Open search dialog ready to search for selected word."""
        try:
            entry_index = self.entry_index_from_click(event)
        except IndexError:
            return "break"
        self.text.select_line(entry_index + 1)
        word = self.entries[entry_index].word

        # Special handling for newline characters (displayed as RETURN_ARROW)
        match_word = re.sub(RETURN_ARROW, "\n", word) if RETURN_ARROW in word else word

        preferences.set(
            PrefKey.SEARCHDIALOG_MATCH_CASE,
            not preferences.get(PrefKey.WFDIALOG_IGNORE_CASE),
        )
        preferences.set(PrefKey.SEARCHDIALOG_WHOLE_WORD, self.whole_word_search(word))
        preferences.set(PrefKey.SEARCHDIALOG_REGEX, False)
        dlg = SearchDialog.show_dialog()
        dlg.search_box_set(match_word)
        dlg.display_message()

        return "break"

    def entry_index_from_click(self, event: tk.Event) -> int:
        """Get the index into the list of entries based on the mouse position
        in the click event.

        Args:
            event: Event object containing mouse click position.

        Returns:
            Index into self.entries list
            Raises IndexError exception if out of range
        """
        entry_index = IndexRowCol(self.text.index(f"@{event.x},{event.y}")).row - 1
        if entry_index < 0 or entry_index >= len(self.entries):
            raise IndexError
        return entry_index


def word_frequency() -> None:
    """Do word frequency analysis on file."""
    global _the_word_lists

    if not tool_save():
        return

    _the_word_lists = WFWordLists()

    wf_dialog = WordFrequencyDialog.show_dialog()
    wf_populate(wf_dialog)


def wf_populate(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with words based on the display type.

    Args:
        wf_dialog: The word frequency dialog.
    """
    Busy.busy()
    wf_dialog.previous_word = ""
    display_type = preferences.get(PrefKey.WFDIALOG_DISPLAY_TYPE)

    # Suspects Only is only relevant for some modes
    if display_type in (
        WFDisplayType.EMDASHES,
        WFDisplayType.HYPHENS,
        WFDisplayType.MARKEDUP,
        WFDisplayType.ACCENTS,
        WFDisplayType.LIGATURES,
    ):
        wf_dialog.suspects_btn.grid()
    else:
        wf_dialog.suspects_btn.grid_remove()

    match display_type:
        case WFDisplayType.ALL_WORDS:
            wf_populate_all(wf_dialog)
        case WFDisplayType.EMDASHES:
            wf_populate_emdashes(wf_dialog)
        case WFDisplayType.HYPHENS:
            wf_populate_hyphens(wf_dialog)
        case WFDisplayType.ALPHANUM:
            wf_populate_alphanum(wf_dialog)
        case WFDisplayType.ALL_CAPS:
            wf_populate_allcaps(wf_dialog)
        case WFDisplayType.MIXED_CASE:
            wf_populate_mixedcase(wf_dialog)
        case WFDisplayType.INITIAL_CAPS:
            wf_populate_initialcaps(wf_dialog)
        case WFDisplayType.MARKEDUP:
            wf_populate_markedup(wf_dialog)
        case WFDisplayType.ACCENTS:
            wf_populate_accents(wf_dialog)
        case WFDisplayType.LIGATURES:
            wf_populate_ligatures(wf_dialog)
        case WFDisplayType.CHAR_COUNTS:
            wf_populate_charcounts(wf_dialog)
        case WFDisplayType.REGEXP:
            wf_populate_regexps(wf_dialog)
        case _ as bad_value:
            assert False, f"Invalid WFDisplayType: {bad_value}"
    wf_dialog.goto_word(0)
    Busy.unbusy()


def wf_populate_all(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    total_cnt = 0
    for word, freq in all_words.items():
        wf_dialog.add_entry(word, freq)
        total_cnt += freq
    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'total word')}; {sing_plur(len(all_words), 'distinct word')}"
    )


def wf_populate_emdashes(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of emdashed words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    emdash_words = _the_word_lists.get_emdash_words()
    suspect_cnt = 0
    for emdash_word, freq in emdash_words.items():
        # Check for suspect, i.e. also seen with a single hyphen
        word = re.sub("(--|—)", "-", emdash_word)
        word_output = False
        if not preferences.get(PrefKey.WFDIALOG_SUSPECTS_ONLY):
            wf_dialog.add_entry(emdash_word, freq)
            word_output = True
        if word in all_words:
            if not word_output:
                wf_dialog.add_entry(emdash_word, freq)
            wf_dialog.add_entry(word, all_words[word], suspect=True)
            suspect_cnt += 1
    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(len(emdash_words), 'emdash phrase')}; {sing_plur(suspect_cnt, 'suspect')} ({WordFrequencyEntry.SUSPECT})"
    )


def wf_populate_hyphens(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of word pairs.

    Args:
        wf_dialog: The word frequency dialog.
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    emdash_words = _the_word_lists.get_emdash_words()

    # See if word pair suspects exist, e.g. "flash light" for "flash-light"
    word_pairs: WFDict = WFDict()
    # Replace single newline or multiple spaces with single space
    # (Multiple newlines is probably deliberate rather than error)
    whole_text = re.sub(r"(\n| +)", " ", maintext().get_text())
    for word in all_words:
        if "-" in word:
            pair = word.replace("-", " ")
            # Find word pair not preceded/followed by a letter - this is so
            # that space or punctuation surrounding the pair doesn't break things.
            count = len(re.findall(rf"(?<!\w){pair}(?!\w)", whole_text))
            if count:
                word_pairs[pair] = count

    suspect_cnt = 0
    total_cnt = 0
    word_output = {}
    for word, freq in all_words.items():
        if "-" not in word:
            continue
        total_cnt += 1
        # Check for suspects - given "w1-w2", then "w1w2", "w1 w2" and "w1--w2" are suspects.
        word_pair = re.sub(r"-\*?", " ", word)
        nohyp_word = re.sub(r"-\*?", "", word)
        twohyp_word = re.sub(r"-\*?", "--", word)
        suspect = (
            word_pair in word_pairs
            or nohyp_word in all_words
            or twohyp_word in emdash_words
        )

        if not preferences.get(PrefKey.WFDIALOG_SUSPECTS_ONLY):
            wf_dialog.add_entry(word, freq, suspect=suspect)
            word_output[word] = True
        if word_pair in word_pairs:
            if word not in word_output:
                wf_dialog.add_entry(word, freq, suspect=suspect)
                word_output[word] = True
            if word_pair not in word_output:
                wf_dialog.add_entry(word_pair, word_pairs[word_pair], suspect=suspect)
                word_output[word_pair] = True
                suspect_cnt += 1
        nohyp_word = re.sub(r"-\*?", "", word)
        if nohyp_word in all_words:
            if word not in word_output:
                wf_dialog.add_entry(word, freq, suspect=suspect)
                word_output[word] = True
            if nohyp_word not in word_output:
                wf_dialog.add_entry(nohyp_word, all_words[nohyp_word], suspect=suspect)
                word_output[nohyp_word] = True
                suspect_cnt += 1
        twohyp_word = re.sub(r"-\*?", "--", word)
        if twohyp_word in emdash_words:
            if word not in word_output:
                wf_dialog.add_entry(word, freq, suspect=suspect)
                word_output[word] = True
            if twohyp_word not in word_output:
                wf_dialog.add_entry(
                    twohyp_word, emdash_words[twohyp_word], suspect=suspect
                )
                word_output[twohyp_word] = True
                suspect_cnt += 1
    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'hyphenated word')}; {sing_plur(suspect_cnt, 'suspect')} ({WordFrequencyEntry.SUSPECT})"
    )


def wf_populate_by_match(
    wf_dialog: WordFrequencyDialog,
    desc: str,
    match_func: Callable[[str], Any],
) -> None:
    """Populate the WF dialog with the list of all words that match_func matches.

    Args:
        wf_dialog: The word frequency dialog.
        desc: Description for message, e.g. desc "ALLCAPS" -> message "27 ALLCAPS words"
        match_func: Function that returns Truthy result if word matches the required criteria
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    count = 0
    for word, freq in all_words.items():
        if match_func(word):
            wf_dialog.add_entry(word, freq)
            count += 1
    wf_dialog.display_entries()
    wf_dialog.message.set(f"{sing_plur(count, desc + ' word')}")


def wf_populate_alphanum(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all alphanumeric words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_populate_by_match(
        wf_dialog,
        "alphanumeric",
        lambda word: re.search(r"\d", word) and re.search(r"\p{Alpha}", word),
    )


def wf_populate_allcaps(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all ALLCAPS words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_populate_by_match(
        wf_dialog,
        "ALLCAPS",
        lambda word: re.search(r"\p{IsUpper}", word)
        and not re.search(r"\p{IsLower}", word),
    )


def wf_populate_mixedcase(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all MiXeD CasE words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_populate_by_match(
        wf_dialog,
        "MiXeD CasE",
        lambda word: re.search(r"\p{IsUpper}", word)
        and re.search(r"\p{IsLower}", word)
        and not re.fullmatch(r"\p{Upper}[\p{IsLower}\p{Mark}\d'’*-]+", word),
    )


def wf_populate_initialcaps(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all Initial Caps words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_populate_by_match(
        wf_dialog,
        "Initial Caps",
        lambda word: re.fullmatch(r"\p{Upper}\P{Upper}+", word),
    )


def wf_populate_markedup(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all phrases marked up
    with DP markup, e.g. <i>, <b>, etc.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_dialog.reset()

    marked_dict: WFDict = WFDict()
    nocase = preferences.get(PrefKey.WFDIALOG_IGNORE_CASE)
    search_flags = re.IGNORECASE if nocase else 0

    whole_text = maintext().get_text()

    matches = re.findall(
        rf"(?<!\w)(<({MARKUP_TYPES})>([^<]|\n)+</\2>)(?!\w)",
        whole_text,
        flags=search_flags,
    )
    for match in matches:
        marked_phrase: str = match[0]
        if nocase:
            marked_phrase = marked_phrase.lower()
        marked_phrase = marked_phrase.replace("\n", RETURN_ARROW)
        tally_word(marked_dict, marked_phrase)

    total_cnt = 0
    suspect_cnt = 0
    unmarked_count = {}
    for marked_phrase, marked_count in marked_dict.items():
        # Suspect if the bare phrase appears an excess number of times,
        # i.e. all occurrences > marked up occurrences
        unmarked_phrase = re.sub(rf"^<({MARKUP_TYPES})>", "", marked_phrase)
        unmarked_phrase = re.sub(rf"</({MARKUP_TYPES})>$", "", unmarked_phrase)
        unmarked_search = unmarked_phrase.replace(RETURN_ARROW, "\n")
        unmarked_search = r"(^|[^>\w])" + re.escape(unmarked_search) + r"($|[^<\w])"
        num_words = len(unmarked_search.split())
        # If phrase is longer than "threshold" words, skip it - zero/empty threshold allows any length
        threshold_str = wf_dialog.threshold_box.get()
        wf_dialog.threshold_box.add_to_history(threshold_str)
        threshold = int(threshold_str) if threshold_str else 0
        if num_words > threshold > 0:
            continue
        total_cnt += 1

        # Store unmarked counts so we don't do unmarked check twice,
        # e.g. if <i>dog</i>, <b>dog</b> and dog all exist
        if unmarked_phrase not in unmarked_count:
            unmarked_count[unmarked_phrase] = len(
                re.findall(unmarked_search, whole_text, flags=search_flags)
            )
            if unmarked_count[unmarked_phrase] > 0:
                wf_dialog.add_entry(
                    unmarked_phrase, unmarked_count[unmarked_phrase], suspect=True
                )
                suspect_cnt += 1

        if unmarked_count[unmarked_phrase] > 0 or not preferences.get(
            PrefKey.WFDIALOG_SUSPECTS_ONLY
        ):
            wf_dialog.add_entry(marked_phrase, marked_count)

    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'marked-up phrase')}; {sing_plur(suspect_cnt, 'suspect')} ({WordFrequencyEntry.SUSPECT})"
    )


def wf_populate_accents(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all accented words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    suspect_cnt = 0
    total_cnt = 0
    for word, freq in all_words.items():
        no_accent_word = DiacriticRemover.remove_diacritics(word)
        if no_accent_word != word:
            total_cnt += 1
            word_output = False
            if not preferences.get(PrefKey.WFDIALOG_SUSPECTS_ONLY):
                wf_dialog.add_entry(word, freq)
                word_output = True
            # Check for suspect, i.e. also seen without accents
            if no_accent_word in all_words:
                if not word_output:
                    wf_dialog.add_entry(word, freq)
                wf_dialog.add_entry(
                    no_accent_word, all_words[no_accent_word], suspect=True
                )
                suspect_cnt += 1
    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'accented word')}; {sing_plur(suspect_cnt, 'suspect')} ({WordFrequencyEntry.SUSPECT})"
    )


def wf_populate_ligatures(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all ligature words.

    Args:
        wf_dialog: The word frequency dialog.
    """
    assert _the_word_lists is not None
    wf_dialog.reset()

    all_words = _the_word_lists.get_all_words()
    suspect_cnt = 0
    total_cnt = 0
    suspects_only = preferences.get(PrefKey.WFDIALOG_SUSPECTS_ONLY)
    for word, freq in all_words.items():
        if not re.search("(ae|AE|Ae|oe|OE|Oe|æ|Æ|œ|Œ)", word):
            continue
        total_cnt += 1
        # If actual ligature, only output it here if not suspects-only
        if re.search("(æ|Æ|œ|Œ)", word):
            if not suspects_only:
                wf_dialog.add_entry(word, freq)
        # Use the non-ligature version to check for suspects - because AE and Ae are both Æ
        else:
            lig_word = re.sub("ae", "æ", word)
            lig_word = re.sub("(AE|Ae)", "Æ", lig_word)
            lig_word = re.sub("oe", "œ", lig_word)
            lig_word = re.sub("(OE|Oe)", "Œ", lig_word)
            if lig_word in all_words:
                suspect_cnt += 1
                if suspects_only:
                    wf_dialog.add_entry(lig_word, all_words[lig_word])
                wf_dialog.add_entry(word, freq, suspect=True)
            elif not suspects_only:
                wf_dialog.add_entry(word, freq)
    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'ligature word')}; {sing_plur(suspect_cnt, 'suspect')} ({WordFrequencyEntry.SUSPECT})"
    )


def wf_populate_charcounts(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all the characters.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_dialog.reset()

    char_dict: WFDict = WFDict()

    total_cnt = 0
    for line, _ in maintext().get_lines():
        total_cnt += len(line)
        for char in line:
            tally_word(char_dict, char)

    for char, count in char_dict.items():
        wf_dialog.add_entry(char, count)

    wf_dialog.display_entries()
    wf_dialog.message.set(
        f"{sing_plur(total_cnt, 'character')}; {sing_plur(len(char_dict), 'distinct character')}"
    )


def wf_populate_regexps(wf_dialog: WordFrequencyDialog) -> None:
    """Populate the WF dialog with the list of all words that match the regexp.

    Args:
        wf_dialog: The word frequency dialog.
    """
    wf_dialog.reset()

    regexp = wf_dialog.regex_box.get()
    wf_dialog.regex_box.add_to_history(regexp)
    try:
        wf_populate_by_match(
            wf_dialog,
            "regex-matching",
            lambda word: re.search(regexp, word),
        )
    except re.error as exc:
        wf_dialog.message.set("Bad regex: " + str(exc))


def tally_word(wf_dict: WFDict, word: str) -> None:
    """Tally word in given WF dictionary, unless word is empty.

    If word already in dictionary, increment the count.
    If not, add word and set count to 1.

    Args:
        wf_dict: WFDict to tally word in.
        word: Word to be tallied."""
    if word:
        try:
            wf_dict[word] += 1
        except KeyError:
            wf_dict[word] = 1
