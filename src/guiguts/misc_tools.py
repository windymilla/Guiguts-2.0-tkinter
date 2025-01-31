"""Miscellaneous tools."""

from enum import Enum, StrEnum, auto
import importlib.resources
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Callable, Optional, Any
import unicodedata

import regex as re

from guiguts.checkers import CheckerDialog, CheckerEntry
from guiguts.data import scannos
from guiguts.file import the_file
from guiguts.maintext import maintext
from guiguts.preferences import (
    PrefKey,
    PersistentString,
    preferences,
    PersistentBoolean,
)
from guiguts.search import get_regex_replacement
from guiguts.utilities import (
    IndexRowCol,
    IndexRange,
    cmd_ctrl_string,
    is_mac,
    sound_bell,
    load_dict_from_json,
)
from guiguts.widgets import ToolTip, ToplevelDialog, Combobox, insert_in_focus_widget

logger = logging.getLogger(__package__)

BLOCK_TYPES = "[$*XxFf]"
POEM_TYPES = "[Pp]"
ALL_BLOCKS_REG = f"[{re.escape('#$*FILPXCR')}]"
QUOTE_APOS_REG = "[[:alpha:]]’[[:alpha:]]"
NEVER_MATCH_REG = "NEVER"
ALWAYS_MATCH_REG = "ALWAYS"
DEFAULT_SCANNOS_DIR = importlib.resources.files(scannos)
DEFAULT_REGEX_SCANNOS = "regex.json"
DEFAULT_STEALTH_SCANNOS = "en-common.json"
DEFAULT_MISSPELLED_SCANNOS = "misspelled.json"


class BasicFixupCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Tools_Menu#Basic_Fixup"


class UnmatchedCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = 'Tools_Menu#"Unmatched"_Submenu'


class AsteriskCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Navigation#Find_Asterisks_w/o_Slash"


def tool_save() -> bool:
    """File must be saved before running tool, so check if it has been,
    and if not, check if user wants to save, or cancel the tool run.

    Returns:
        True if OK to continue with intended operation.
    """
    if the_file().filename:
        return True

    save = messagebox.askokcancel(
        title="Save document",
        message="Document must be saved before running tool",
        icon=messagebox.INFO,
    )
    # User could cancel from messagebox or save-as dialog
    return save and bool(the_file().save_file())


def process_fixup(checker_entry: CheckerEntry) -> None:
    """Process the fixup error."""
    if checker_entry.text_range is None:
        return
    entry_type = checker_entry.text.split(":", 1)[0]
    start_mark = BasicFixupCheckerDialog.mark_from_rowcol(
        checker_entry.text_range.start
    )
    end_mark = BasicFixupCheckerDialog.mark_from_rowcol(checker_entry.text_range.end)
    match_text = maintext().get(start_mark, end_mark)
    # Several fixup errors are "Spaced ..." and need spaces removing.
    if entry_type.startswith("Spaced "):
        replacement_text = match_text.replace(" ", "")
        maintext().replace(start_mark, end_mark, replacement_text)
    elif entry_type == "Trailing spaces":
        replacement_text = match_text.replace(" ", "")
        maintext().replace(start_mark, end_mark, replacement_text)
    elif entry_type == "Multiple spaces":
        # Leave the non-space and first space alone
        replacement_text = match_text[2:].replace(" ", "")
        maintext().replace(f"{start_mark} + 2c", end_mark, replacement_text)
    elif entry_type == "Thought break":
        maintext().replace(start_mark, end_mark, "<tb>")
    elif entry_type == "1/l scanno":
        replacement_text = match_text.replace("llth", "11th").replace("lst", "1st")
        maintext().replace(start_mark, end_mark, replacement_text)
    elif entry_type == "Ellipsis spacing":
        maintext().replace(start_mark, end_mark, " ...")
    else:
        return


def sort_key_type(
    entry: CheckerEntry,
) -> tuple[int, str, int, int]:
    """Sort key function to sort Fixup entries by text, putting identical upper
        and lower case versions together.

    Differs from default alpha sort in using the text up to the colon
    (the type of error) as the primary sort key. No need to deal with different
    entry types for Fixup, and all entries have a text_range.
    """
    assert entry.text_range is not None
    return (
        entry.section,
        entry.text.split(":")[0],
        entry.text_range.start.row,
        entry.text_range.start.col,
    )


def basic_fixup_check() -> None:
    """Check the currently loaded file for basic fixup errors."""

    if not tool_save():
        return

    checker_dialog = BasicFixupCheckerDialog.show_dialog(
        "Basic Fixup Check Results",
        rerun_command=basic_fixup_check,
        process_command=process_fixup,
        sort_key_alpha=sort_key_type,
    )
    ToolTip(
        checker_dialog.text,
        "\n".join(
            [
                "Left click: Select & find issue",
                "Right click: Remove issue from list",
                f"With {cmd_ctrl_string()} key: Also fix issue",
                "With Shift key: Also remove/fix matching issues",
            ]
        ),
        use_pointer_pos=True,
    )
    checker_dialog.reset()

    # Check lines that aren't in block markup
    in_block = in_poem = False
    for line, line_num in maintext().get_lines():
        # Ignore lines within block markup
        if re.match(rf"/{BLOCK_TYPES}", line):
            in_block = True
        elif re.match(rf"{BLOCK_TYPES}/", line):
            in_block = False
        if in_block:
            continue

        # Ignore any poem line number (with preceding/trailing spaces)
        if re.match(rf"/{POEM_TYPES}", line):
            in_poem = True
        elif re.match(rf"{POEM_TYPES}/", line):
            in_poem = False
        test_line = line
        if in_poem and (match := re.search(r"\s{2,}\d+\s*$", line)):
            test_line = line[: match.start(0)]

        # Dictionary of regexes:
        # One or two capture groups in each regex, with the characters being checked
        # captured by one of them - these will be highlighted in error message.
        #
        # Multiple spaces between non-space characters
        # Space around single hyphen (not at start of line)
        # Space before single period (not at start of line and not decimal point)
        # Space before exclamation mark, question mark, comma, colon, or semicolon
        # Space before close or after open quotes (where unambiguous)
        # Space before close or after open brackets
        # Trailing spaces
        # Thought break (possibly poorly formed)
        # 1/l scanno, e.g. llth
        fixup_regexes = {
            "Multiple spaces: ": r"(\S\s{2,})(?=\S)",  # Capture character before too
            "Spaced hyphen: ": r"\S( +- *)(?!-)|(?<!-)( *- +)",
            "Spaced period: ": r"\S( +\.)(?![\d\.])",
            "Spaced exclamation mark: ": r"( +!)",
            "Spaced question mark: ": r"( +\?)",
            "Spaced comma: ": r"( +,)",
            "Spaced colon: ": r"( +:)",
            "Spaced semicolon: ": r"( +;)",
            "Spaced open quote: ": r'(^" +|[‘“] +)',
            "Spaced close quote: ": r'( +"$| +”)',
            "Spaced open bracket: ": r"([[({] )",
            "Spaced close bracket: ": r"( [])}])",
            "Trailing spaces: ": r"(.? +$)",
            "Thought break: ": r"^(\s*(\*\s*){4,})$",
            "1/l scanno: ": r"(?<!\p{Letter})(lst|llth)\b",
            "Ellipsis spacing: ": r"(?<=[^\.\!\? \"'‘“])(\.{3})(?![\.\!\?])",
            "Spaced guillemet: ": r"(«\s+|\s+»)",
        }
        # Some languages use inward pointing guillemets - catch most of them.
        # Do inward check if first language is one of these, or English with one of these.
        inward_langs = r"(de|hr|cs|da|hu|pl|sr|sk|sl|sv)"
        languages = maintext().get_language_list()
        if re.match(inward_langs, languages[0]) or (
            re.match("en", languages[0])
            and any(re.match(inward_langs, lang) for lang in languages)
        ):
            fixup_regexes["Spaced guillemet: "] = r"(»\s+|\s+«)"

        for prefix, regex in fixup_regexes.items():
            prefix_len = len(prefix)
            for match in re.finditer(regex, test_line):
                group = 2 if match[1] is None else 1
                checker_dialog.add_entry(
                    prefix + line,
                    IndexRange(
                        IndexRowCol(line_num, match.start(group)),
                        IndexRowCol(line_num, match.end(group)),
                    ),
                    match.start(group) + prefix_len,
                    match.end(group) + prefix_len,
                )
    checker_dialog.display_entries()


class PageSepAutoType(StrEnum):
    """Enum class to store Page Separator sort types."""

    NO_AUTO = auto()
    AUTO_ADVANCE = auto()
    AUTO_FIX = auto()


class PageSeparatorDialog(ToplevelDialog):
    """Dialog for fixing page separators."""

    manual_page = "Tools_Menu#Page_Separator_Fixup"
    SEPARATOR_REGEX = r"^-----File: .+"
    BTN_WIDTH = 16

    def __init__(self) -> None:
        """Initialize page separator dialog."""
        super().__init__("Page Separator Fixup", resize_x=False, resize_y=False)
        for col in range(0, 3):
            self.top_frame.columnconfigure(col, pad=2, weight=1)

        btn_frame = ttk.Frame(
            self.top_frame, borderwidth=1, relief=tk.GROOVE, padding=5
        )
        btn_frame.grid(column=0, row=0, columnspan=3, sticky="NSEW")

        for row in range(0, 3):
            btn_frame.rowconfigure(row, pad=2)
        for col in range(0, 3):
            btn_frame.columnconfigure(col, pad=2, weight=1)
        join_button = ttk.Button(
            btn_frame,
            text="Join",
            underline=0,
            command=lambda: self.join(False),
            width=self.BTN_WIDTH,
        )
        join_button.grid(column=0, row=0, sticky="NSEW")
        self.key_bind("j", join_button.invoke)

        keep_button = ttk.Button(
            btn_frame,
            text="Join, Keep Hyphen",
            underline=6,
            command=lambda: self.join(True),
            width=self.BTN_WIDTH,
        )
        keep_button.grid(column=1, row=0, sticky="NSEW")
        self.key_bind("k", keep_button.invoke)

        delete_button = ttk.Button(
            btn_frame,
            text="Delete",
            underline=0,
            command=self.delete,
            width=self.BTN_WIDTH,
        )
        delete_button.grid(column=2, row=0, sticky="NSEW")
        self.key_bind("d", delete_button.invoke)

        blank_button = ttk.Button(
            btn_frame,
            text="Blank (1 line)",
            underline=0,
            command=lambda: self.blank(1),
            width=self.BTN_WIDTH,
        )
        blank_button.grid(column=0, row=1, sticky="NSEW")
        self.key_bind("b", blank_button.invoke)
        self.key_bind("Key-1", blank_button.invoke)

        section_button = ttk.Button(
            btn_frame,
            underline=0,
            text="Section (2 lines)",
            command=lambda: self.blank(2),
            width=self.BTN_WIDTH,
        )
        section_button.grid(column=1, row=1, sticky="NSEW")
        self.key_bind("s", section_button.invoke)
        self.key_bind("Key-2", section_button.invoke)

        chapter_button = ttk.Button(
            btn_frame,
            underline=0,
            text="Chapter (4 lines)",
            command=lambda: self.blank(4),
            width=self.BTN_WIDTH,
        )
        chapter_button.grid(column=2, row=1, sticky="NSEW")
        self.key_bind("c", chapter_button.invoke)
        self.key_bind("Key-4", chapter_button.invoke)

        auto_type = PersistentString(PrefKey.PAGESEP_AUTO_TYPE)
        ttk.Radiobutton(
            self.top_frame,
            text="No Auto",
            command=self.view,
            variable=auto_type,
            value=PageSepAutoType.NO_AUTO,
            takefocus=False,
        ).grid(column=0, row=1, sticky="NSEW", padx=(20, 2), pady=2)
        ttk.Radiobutton(
            self.top_frame,
            text="Auto Advance",
            command=self.view,
            variable=auto_type,
            value=PageSepAutoType.AUTO_ADVANCE,
            takefocus=False,
        ).grid(column=1, row=1, sticky="NSEW", padx=2, pady=2)
        ttk.Radiobutton(
            self.top_frame,
            text="Auto Fix",
            command=self.view,
            variable=auto_type,
            value=PageSepAutoType.AUTO_FIX,
            takefocus=False,
        ).grid(column=2, row=1, sticky="NSEW", padx=2, pady=2)

        end_frame = ttk.Frame(
            self.top_frame, borderwidth=1, relief=tk.GROOVE, padding=5
        )
        end_frame.grid(column=0, row=2, columnspan=3, sticky="NSEW")
        for col in range(0, 3):
            end_frame.columnconfigure(col, pad=2, weight=1)

        undo_button = ttk.Button(
            end_frame,
            text="Undo",
            command=self.undo,
            underline=0,
            width=self.BTN_WIDTH,
        )
        undo_button.grid(column=0, row=0, sticky="NSEW")
        self.key_bind("u", undo_button.invoke)
        self.key_bind("Cmd/Ctrl+Z", undo_button.invoke)

        redo_button = ttk.Button(
            end_frame,
            text="Redo",
            command=self.redo,
            underline=1,
            width=self.BTN_WIDTH,
        )
        redo_button.grid(column=1, row=0, sticky="NSEW")
        self.key_bind("e", redo_button.invoke)
        self.key_bind("Cmd+Shift+Z" if is_mac() else "Ctrl+Y", redo_button.invoke)

        refresh_button = ttk.Button(
            end_frame,
            text="Refresh",
            command=self.refresh,
            underline=0,
            width=self.BTN_WIDTH,
        )
        refresh_button.grid(column=2, row=0, sticky="NSEW")
        self.key_bind("r", refresh_button.invoke)

    def do_join(self, keep_hyphen: bool) -> None:
        """Join 2 lines if hyphenated, otherwise just remove separator line(s).

        Args:
            keep_hyphen: True to keep hyphen when lines are joined.
        """
        if (sep_range := self.find()) is None:
            return

        sep_range = self.fix_pagebreak_markup(sep_range)
        maintext().delete(sep_range.start.index(), sep_range.end.index())
        prev_eol = f"{sep_range.start.index()} -1l lineend"
        maybe_hyphen = maintext().get(f"{prev_eol}-1c", prev_eol)
        # If "*" at end of line, check previous character for "-"
        if maybe_hyphen == "*":
            prev_eol = f"{prev_eol} -1c"
            maybe_hyphen = maintext().get(f"{prev_eol}-1c", prev_eol)
        # Check for emdash (double-hyphen)
        maybe_second_hyphen = ""
        if maybe_hyphen == "-":
            maybe_second_hyphen = maintext().get(f"{prev_eol}-2c", f"{prev_eol} -1c")
        # Adjust effective end of line position depending if hyphen is being kept
        # (always keep emdash)
        if not keep_hyphen and maybe_hyphen == "-" and maybe_second_hyphen != "-":
            prev_eol = f"{prev_eol} -1c"
        # If emdash at start of second page, also want to join,
        # even if no hyphen at end of first page.
        next_bol = f"{sep_range.start.index()} linestart"
        # Omit any "*" at beginning of next line
        if maintext().get(next_bol, f"{next_bol}+1c") == "*":
            maintext().delete(next_bol)
        maybe_page2_emdash = maintext().get(next_bol, f"{next_bol}+2c")

        # If word is hyphenated or emdash, move second part of word to end of first part
        if maybe_hyphen == "-" or maybe_page2_emdash == "--":
            word_end = maintext().search(" ", next_bol, f"{next_bol} lineend")
            if not word_end:
                word_end = maintext().index(f"{next_bol} lineend")
            # Delete one character (space or newline) after second part of word
            maintext().delete(word_end)
            second_part = maintext().get(next_bol, word_end)
            maintext().delete(next_bol, word_end)
            # Delete maybe hyphen, & asterisks at end of prev line
            maintext().delete(prev_eol, f"{prev_eol} lineend")
            # Insert second part of word at end of prev line
            maintext().insert(f"{prev_eol} lineend", second_part)
        maintext().set_insert_index(sep_range.start, focus=False)

    def do_delete(self) -> None:
        """Just remove separator line."""
        if (sep_range := self.find()) is None:
            return
        maintext().delete(sep_range.start.index(), sep_range.end.index())
        maintext().set_insert_index(sep_range.start, focus=False)

    def do_blank(self, num_lines: int) -> None:
        """Replace separator & adjacent blank lines with given number of blank lines.

        Args:
            num_lines: How many blank lines to use.
        """
        if (sep_range := self.find()) is None:
            return
        # Find previous end and next start of non-whitespace, to replace with given number of blank lines
        end_prev = maintext().search(
            r"\S",
            sep_range.start.index(),
            "1.0",
            backwards=True,
            regexp=True,
        )
        end_prev = f"{end_prev}+1l linestart" if end_prev else "1.0"
        beg_next = maintext().search(r"\S", sep_range.end.index(), tk.END, regexp=True)
        beg_next = f"{beg_next} linestart" if beg_next else tk.END
        maintext().replace(end_prev, beg_next, num_lines * "\n")
        maintext().set_insert_index(sep_range.start, focus=False)

    def refresh(self) -> None:
        """Refresh to show the first available page separator."""
        maintext().undo_block_begin()
        self.do_auto()
        self.view()

    def join(self, keep_hyphen: bool) -> None:
        """Handle click on Join buttons.

        Args:
            keep_hyphen: True to keep hyphen when lines are joined.
        """
        maintext().undo_block_begin()
        self.do_join(keep_hyphen)
        self.do_auto()

    def delete(self) -> None:
        """Handle click on Delete button."""
        maintext().undo_block_begin()
        self.do_delete()
        self.do_auto()

    def blank(self, num_lines: int) -> None:
        """Handle click on Blank buttons.

        Args:
            num_lines: How many blank lines to use.
        """
        maintext().undo_block_begin()
        self.do_blank(num_lines)
        self.do_auto()

    def undo(self) -> None:
        """Handle click on Undo button, by undoing latest changes and re-viewing
        the first available page separator."""
        maintext().event_generate("<<Undo>>")
        self.view()

    def redo(self) -> None:
        """Handle click on Redo button, by re-doing latest undo and re-viewing
        the first available page separator."""
        maintext().event_generate("<<Redo>>")
        self.view()

    def view(self) -> None:
        """Show the first available separator line to be processed."""
        if (sep_range := self.find()) is None:
            return
        maintext().do_select(sep_range)
        maintext().set_insert_index(sep_range.start, focus=False)

    def find(self) -> Optional[IndexRange]:
        """Find the first available separator line.

        Returns:
            IndexRange containing start & end of separator, or None.
        """
        match = maintext().find_match(
            PageSeparatorDialog.SEPARATOR_REGEX,
            maintext().start_to_end(),
            nocase=False,
            regexp=True,
            backwards=False,
        )
        if match is None:
            return None
        end_rowcol = IndexRowCol(match.rowcol.row + 1, match.rowcol.col)
        return IndexRange(match.rowcol, end_rowcol)

    def fix_pagebreak_markup(self, sep_range: IndexRange) -> IndexRange:
        """Remove markup, e.g. italics or blockquote, if closed immediately before
        page break and same markup is reopened immediately afterwards.

        Args:
            sep_range: Range of page separator line.

        Returns:
            Updated page separator range (removal of markup may have affected page sep line number)
        """
        # Remove all but one page sep lines at this location. As each is deleted
        # the next will be move up to lie at the same location.
        line = ""
        while True:
            del_range = self.find()
            if del_range is None or del_range.start != sep_range.start:
                break
            line = maintext().get(sep_range.start.index(), sep_range.end.index())
            maintext().delete(sep_range.start.index(), sep_range.end.index())
        # Add last one back
        maintext().insert(sep_range.start.index(), line)
        ps_start = sep_range.start.index()
        ps_end = sep_range.end.index()
        maybe_more_to_remove = True
        while maybe_more_to_remove:
            maybe_more_to_remove = False
            markup_prev = maintext().get(
                f"{ps_start}-1l lineend -6c",
                f"{ps_start}-1l lineend",
            )
            markup_next = maintext().get(
                f"{ps_end}",
                f"{ps_end} +4c",
            )
            # Remove blockquote or nowrap markup
            if match := re.search(r"(\*|#)/$", markup_prev):
                markup_type = re.escape(match[1])
                if re.search(rf"^/{markup_type}", markup_next):
                    maintext().delete(
                        ps_end,
                        f"{ps_end} +1l",
                    )
                    maintext().delete(
                        f"{ps_start}-1l",
                        ps_start,
                    )
                    # Compensate for having deleted a line before the page separator
                    ps_start = maintext().index(f"{ps_start}-1l")
                    ps_end = maintext().index(f"{ps_end}-1l")
                    maybe_more_to_remove = True
            # Remove inline markup
            elif match := re.search(r"</(i|b|f|g|sc)>([,;*]?)$", markup_prev):
                markup_type = match[1]
                len_markup = len(markup_type)
                len_punc = len(match[2])
                if re.search(rf"^<{markup_type}>", markup_next):
                    maintext().delete(
                        f"{ps_end}",
                        f"{ps_end} +{len_markup + 2}c",
                    )
                    maintext().delete(
                        f"{ps_start}-1l lineend -{len_markup + len_punc + 3}c",
                        f"{ps_start}-1l lineend -{len_punc}c",
                    )
                    maybe_more_to_remove = True
        return IndexRange(ps_start, ps_end)

    def do_auto(self) -> None:
        """Do auto page separator fixing if allowed by settings."""
        if preferences.get(PrefKey.PAGESEP_AUTO_TYPE) == PageSepAutoType.NO_AUTO:
            return
        if preferences.get(PrefKey.PAGESEP_AUTO_TYPE) == PageSepAutoType.AUTO_ADVANCE:
            self.view()
            return

        # Auto-fix: Loop through page separators, fixing them if possible
        while sep_range := self.find():
            # Fix markup across page break, even though the join function would fix it later,
            # because otherwise it would interfere with check for automated joining below.
            sep_range = self.fix_pagebreak_markup(sep_range)
            line_prev = maintext().get(
                f"{sep_range.start.index()}-1l lineend -10c",
                f"{sep_range.start.index()}-1l lineend",
            )
            line_next = maintext().get(
                f"{sep_range.end.index()}",
                f"{sep_range.end.index()}+10c",
            )
            if line_next.startswith(4 * "\n"):
                self.do_blank(4)
            elif line_next.startswith(2 * "\n"):
                self.do_blank(2)
            elif line_next.startswith(1 * "\n"):
                self.do_blank(1)
            elif line_next.startswith("-----File:"):
                self.do_delete()
            elif not (
                re.search(r"^\*?-(?!-)", line_next)
                or re.search(r"(?<!-)-\*?$", line_prev)
            ):
                # No hyphen before/after page break, so OK to join
                self.do_join(False)
            else:
                break
        self.view()


def page_separator_fixup() -> None:
    """Fix and remove page separator lines."""
    dlg = PageSeparatorDialog.show_dialog()
    dlg.view()


def unmatched_brackets() -> None:
    """Check for unmatched brackets."""

    def toggle_bracket(bracket_in: str) -> tuple[str, bool]:
        """Get regex that matches open and close bracket.

        Args:
            bracket_in: Bracket - must be one of ( [ { ) ] }

        Returns:
            Tuple with regex, True if bracket_in was close bracket.
        """
        match bracket_in:
            case "(":
                return "[)(]", False
            case ")":
                return "[)(]", True
            case "{":
                return "[}{]", False
            case "}":
                return "[}{]", True
            case "[":
                return "[][]", False
            case "]":
                return "[][]", True
        assert False, f"'{bracket_in}' is not a bracket character"

    unmatched_markup_check(
        "Unmatched Brackets",
        rerun_command=unmatched_brackets,
        match_reg="[][}{)(]",
        match_pair_func=toggle_bracket,
    )


def unmatched_curly_quotes() -> None:
    """Check for unmatched curly quotes."""

    def toggle_quote(quote_in: str) -> tuple[str, bool]:
        """Get regex that matches open and close quote.

        Args:
            quote_in: Quote - must be one of ‘ ’ “ ”

        Returns:
            Tuple with regex, True if bracket_in was close quote.
        """
        match quote_in:
            case "“":
                return "[“”]", False
            case "”":
                return "[“”]", True
            case "‘":
                return "[‘’]", False
            case "’":
                return "[‘’]", True
        assert False, f"'{quote_in}' is not a curly quote"

    def unmatched_single_quotes(dialog: UnmatchedCheckerDialog) -> None:
        """Add warnings about unmatched single quotes to given dialog.

        Args:
            dialog: UnmatchedCheckerDialog to receive error messages.
        """
        nestable = preferences.get(PrefKey.UNMATCHED_NESTABLE)
        prefix = "Unmatched: "
        search_range = maintext().start_to_end()
        # Find open & close single quotes
        while match := maintext().find_match("[‘’]", search_range, regexp=True):
            quote_type = maintext().get_match_text(match)
            match_pair_reg, reverse = toggle_quote(quote_type)
            match_index = match.rowcol.index()
            after_match = maintext().index(f"{match_index}+1c")
            search_range = IndexRange(after_match, maintext().end())
            # If close quote surrounded by alphabetic characters, assume it's an apostrophe
            context = maintext().get(f"{match_index}-1c", f"{match_index}+2c")
            if re.fullmatch(QUOTE_APOS_REG, context):
                continue
            # Search for the matching pair to this markup
            if not find_match_pair(
                match_index,
                quote_type,
                match_pair_reg,
                reverse,
                nestable,
                ignore_func=ignore_apostrophes,
            ):
                dialog.add_entry(
                    f"{prefix}{quote_type}",
                    IndexRange(match_index, after_match),
                    len(prefix),
                    len(prefix) + 1,
                )

    unmatched_markup_check(
        "Unmatched Curly Quotes",
        rerun_command=unmatched_curly_quotes,
        match_reg="[“”]",
        match_pair_func=toggle_quote,
        additional_check_command=unmatched_single_quotes,
    )


def ignore_apostrophes(match_index: str) -> bool:
    """Return whether to ignore match because context implies
    it's an apostrophe rather than a close-quote, i.e. it's surrounded
    by alphabetic characters.

    Args:
        match_index: Index to location of match.
    """
    context = maintext().get(f"{match_index}-1c", f"{match_index}+2c")
    return bool(re.fullmatch(QUOTE_APOS_REG, context))


def unmatched_dp_markup() -> None:
    """Check for unmatched DP markup."""

    def matched_pair_dp_markup(markup_in: str) -> tuple[str, bool]:
        """Get regex that matches open and close DP markup.

        Args:
            markup_in: Markup string - must be "<i>", "</b>", "<sc>", etc.

        Returns:
            Tuple with regex, True if markup_in was close markup.
        """
        if markup_in[1] == "/":
            return f"(<{markup_in[2:-1]}>|{markup_in})", True
        return f"({markup_in}|</{markup_in[1:-1]}>)", False

    def sort_key_dp_markup(
        entry: CheckerEntry,
    ) -> tuple[str, bool, int, int]:
        """Sort key function to sort bad DP  entries by casefolded text.

        Order is type of markup (e.g. b, i, sc), open/close, row, col.
        """
        assert entry.text_range is not None
        text = entry.text[entry.hilite_start : entry.hilite_end]
        type_lower = text.lower().replace("/", "")[1:-1]
        return (
            type_lower,
            "/" in text,
            entry.text_range.start.row,
            entry.text_range.start.col,
        )

    unmatched_markup_check(
        "Unmatched DP markup",
        rerun_command=unmatched_dp_markup,
        match_reg="<(i|b|u|g|f|sc)>|</(i|b|u|g|f|sc)>",
        match_pair_func=matched_pair_dp_markup,
        nest_reg=NEVER_MATCH_REG,
        ignore_reg="<tb>",
        sort_key_alpha=sort_key_dp_markup,
    )


def unmatched_html_markup() -> None:
    """Check for unmatched HTML markup."""

    open_regex = "<([[:alnum:]]+)( [^>\n]+)?>"
    close_regex = "</([[:alnum:]]+)>"

    def get_tag_type_regex(markup_in: str) -> str:
        """Get the type of the given tag, e.g. "<div class='abc'>" gives "div".

        Escaped so suitable for use in a regex.
        """
        return re.escape(re.sub(open_regex, r"\1", markup_in.replace("/", "")))

    def matched_pair_html_markup(markup_in: str) -> tuple[str, bool]:
        """Get regex that matches open and close HTML markup.

        Args:
            markup_in: Markup string - must be "<i>", "</b>", "<div class='...'>", etc.

        Returns:
            Tuple with regex, True if markup_in was close markup.
        """
        mtype = get_tag_type_regex(markup_in)
        return f"(<{mtype}( [^>\n]+)?>|</{mtype}>)", (markup_in[1] == "/")

    def sort_key_html_markup(
        entry: CheckerEntry,
    ) -> tuple[str, bool, int, int]:
        """Sort key function to sort bad HTML tags.

        Order is type of markup (e.g. b, div, span), open/close, row, col.
        """
        assert entry.text_range is not None
        text = entry.text[entry.hilite_start : entry.hilite_end]
        mtype = get_tag_type_regex(text)
        return (
            mtype,
            "/" in text,
            entry.text_range.start.row,
            entry.text_range.start.col,
        )

    unmatched_markup_check(
        "Unmatched HTML tags",
        rerun_command=unmatched_html_markup,
        match_reg=f"{open_regex}|{close_regex}",
        match_pair_func=matched_pair_html_markup,
        nest_reg=ALWAYS_MATCH_REG,
        sort_key_alpha=sort_key_html_markup,
    )


def unmatched_block_markup() -> None:
    """Check for unmatched block markup."""

    def match_pair_block_markup(markup_in: str) -> tuple[str, bool]:
        """Get regex that matches open and close block markup.

        Args:
            markup_in: Markup string - must be "/#", "/*", "C/", etc.

        Returns:
            Tuple with regex, True if markup_in was close markup.
        """
        close = markup_in[1] == "/"
        block_type = markup_in[0] if close else markup_in[1]
        block_type = re.escape(block_type)
        return rf"^(/{block_type}(\[\d+)?(\.\d+)?(,\d+)?]?|{block_type}/)$", close

    def malformed_block_markup(dialog: UnmatchedCheckerDialog) -> None:
        """Add warnings about malformed block markup to given dialog.

        Args:
            dialog: UnmatchedCheckerDialog to receive error messages.
        """
        search_range = maintext().start_to_end()
        prefix = "Badly formed markup: "
        while match := maintext().find_match(
            f"(/{ALL_BLOCKS_REG}|{ALL_BLOCKS_REG}/)",
            search_range,
            regexp=True,
            nocase=True,
        ):
            match_index = match.rowcol.index()
            after_match = maintext().index(f"{match_index}+{match.count}c")
            search_range = IndexRange(after_match, maintext().end())
            match_str = maintext().get_match_text(match)
            # Don't report if open markup preceded by "<", e.g. "</i>"
            if match_str[0] == "/" and maintext().get(f"{match_index}-1c") == "<":
                continue
            # Now check if markup is malformed - get whole line and check against regex
            line = maintext().get(f"{match_index} linestart", f"{match_index} lineend")
            try:
                idx_start = len(prefix) + line.index(match_str)
                idx_end = idx_start + len(match_str)
            except ValueError:
                idx_start = None
                idx_end = None
            block_type = re.escape(match_str.replace("/", ""))
            regex = rf"^(/{block_type}(\[\d+)?(\.\d+)?(,\d+)?]?|{block_type}/)$"
            if not re.fullmatch(regex, line):
                dialog.add_entry(
                    f"{prefix}{line}",
                    IndexRange(match_index, after_match),
                    idx_start,
                    idx_end,
                )

    def sort_key_block_markup(
        entry: CheckerEntry,
    ) -> tuple[str, str, int, int, int]:
        """Sort key function to sort bad markup entries by casefolded text.

        Order is type of error (badly formed or unmatched), type of markup (e.g. #, *, P),
        open/close markup, row, col.
        """
        assert entry.text_range is not None
        text = entry.text[entry.hilite_start : entry.hilite_end]
        type_lower = text.lower().replace("/", "")
        open_close_index = text.index("/")
        return (
            entry.text.split(":")[0],
            type_lower,
            open_close_index,
            entry.text_range.start.row,
            entry.text_range.start.col,
        )

    unmatched_markup_check(
        "Unmatched Block markup",
        rerun_command=unmatched_block_markup,
        match_reg=f"^(/{ALL_BLOCKS_REG}|{ALL_BLOCKS_REG}/)",
        match_pair_func=match_pair_block_markup,
        nest_reg="/#|#/",
        sort_key_alpha=sort_key_block_markup,
        additional_check_command=malformed_block_markup,
    )


def unmatched_markup_check(
    title: str,
    rerun_command: Callable[[], None],
    match_reg: str,
    match_pair_func: Callable[[str], tuple[str, bool]],
    nest_reg: Optional[str] = None,
    ignore_reg: Optional[str] = None,
    sort_key_alpha: Optional[Callable[[CheckerEntry], tuple]] = None,
    additional_check_command: Optional[Callable[[UnmatchedCheckerDialog], None]] = None,
) -> None:
    """Check the currently loaded file for unmatched markup errors.

    Args:
        title: Title for dialog.
        rerun_command: Function to re-run check.
        match_reg: Regex matching open & close markup.
        nest_reg: Regex matching markup that is allowed to be nested.
            None means user-controlled via Pref.
        ignore_reg: Regex matching markup that is to be ignored during check.
        sort_key_alpha: Function to provide type/alphabetic sorting
        additional_check_command: Function to perform extra checks
    """

    if not tool_save():
        return

    checker_dialog = UnmatchedCheckerDialog.show_dialog(
        title,
        rerun_command=rerun_command,
        sort_key_alpha=sort_key_alpha,
    )
    ToolTip(
        checker_dialog.text,
        "\n".join(
            [
                "Left click: Select & find issue",
                "Right click: Remove issue from list",
                "Shift-Right click: Remove all matching issues",
            ]
        ),
        use_pointer_pos=True,
    )
    # User can control nestability of some unmatched check types
    if nest_reg is None:
        frame = ttk.Frame(checker_dialog.header_frame)
        frame.grid(column=0, row=1, sticky="NSEW")
        ttk.Checkbutton(
            frame,
            text="Allow nesting",
            variable=PersistentBoolean(PrefKey.UNMATCHED_NESTABLE),
        ).grid(row=0, column=0, sticky="NSEW")

    checker_dialog.reset()

    search_range = maintext().start_to_end()
    # Find each piece of markup that matches the regex
    while match := maintext().find_match(
        match_reg, search_range, regexp=True, nocase=True
    ):
        match_index = match.rowcol.index()
        after_match = maintext().index(f"{match_index}+{match.count}c")
        search_range = IndexRange(after_match, maintext().end())
        match_str = maintext().get_match_text(match)
        # Ignore if it matches the ignore regex
        if ignore_reg and re.fullmatch(ignore_reg, match_str, flags=re.IGNORECASE):
            continue
        # Get a regex that will find the match and the pair( e.g. "(<i>|</i>)")
        match_pair_reg, reverse = match_pair_func(match_str)
        # Is this markup permitted to nest?
        if nest_reg is None:
            nestable = preferences.get(PrefKey.UNMATCHED_NESTABLE)
        elif nest_reg == NEVER_MATCH_REG:
            nestable = False
        elif nest_reg == ALWAYS_MATCH_REG:
            nestable = True
        else:
            nestable = bool(re.fullmatch(nest_reg, match_str, flags=re.IGNORECASE))
        prefix = "Unmatched: "
        # Search for the matching pair to this markup
        if not find_match_pair(
            match_index, match_str, match_pair_reg, reverse, nestable
        ):
            checker_dialog.add_entry(
                f"{prefix}{match_str}",
                IndexRange(match_index, after_match),
                len(prefix),
                len(prefix) + match.count,
            )

    if additional_check_command:
        additional_check_command(checker_dialog)
    checker_dialog.display_entries()


def find_match_pair(
    match_index: str,
    match_str: str,
    match_pair_reg: str,
    reverse: bool,
    nestable: bool,
    ignore_func: Optional[Callable[[str], bool]] = None,
) -> str:
    """Find the pair to the given match.

    Args:
        match_index: Index of start of match_str in file.
        match_str: String to find the pair of.
        pair_str: The pair string to search for.
        reverse: True to search backwards (i.e. given close, look for open).
        nestable: True if markup is allowed to nest.
        ignore_func: Optional function that returns whether to ignore a potential match.
    """
    found = ""
    match_len = len(match_str)
    depth = 1
    start = match_index if reverse else maintext().index(f"{match_index}+{match_len}c")
    end = maintext().start() if reverse else maintext().end()
    # Keep searching until we find the markup that brings us back
    # to the same depth as the given markup (or until there's an error)
    while depth > 0:
        # Search for the given markup and its pair in order to spot nesting
        match = maintext().find_match(
            match_pair_reg,
            IndexRange(start, end),
            regexp=True,
            backwards=reverse,
            nocase=True,
        )
        if match is None:
            found = ""
            break

        # If match shouldn't be ignored, adjust depth and check if nesting is allowed
        match_index = match.rowcol.index()
        if ignore_func is None or not ignore_func(match_index):
            depth += (
                1
                if maintext().get_match_text(match).lower() == match_str.lower()
                else -1
            )
            # Check it's not nested when nesting isn't allowed
            if depth > 1 and not nestable:
                found = ""
                break
            found = match_index

        # Adjust start point for next search
        start = (
            match_index
            if reverse
            else maintext().index(f"{match_index}+{match.count}c")
        )
    return found


class FractionConvertType(Enum):
    """Enum class to store fraction conversion types."""

    UNICODE = auto()  # convert only those that have a Unicode fraction
    MIXED = auto()  # convert to Unicode if possible, otherwise super/subscript
    SUPSUB = auto()  # convert all to super/subscript form


def fraction_convert(conversion_type: FractionConvertType) -> None:
    """Convert fractions in selection or whole file, e.g. 1/2 --> ½

    Args:
        type: Determines which/how fractions will be converted.
    """

    unicode_fractions = {
        "1/4": "¼",
        "1/2": "½",
        "3/4": "¾",
        "1/7": "⅐",
        "1/9": "⅑",
        "1/10": "⅒",
        "1/3": "⅓",
        "2/3": "⅔",
        "1/5": "⅕",
        "2/5": "⅖",
        "3/5": "⅗",
        "4/5": "⅘",
        "1/6": "⅙",
        "5/6": "⅚",
        "1/8": "⅛",
        "3/8": "⅜",
        "5/8": "⅝",
        "7/8": "⅝",
    }
    superscripts = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    subscripts = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

    maintext().undo_block_begin()
    maintext().selection_ranges_store_with_marks()

    sel_ranges = maintext().selected_ranges()
    if not sel_ranges:
        sel_ranges = [maintext().start_to_end()]

    last_match = ""
    frac_slash = "⁄"
    any_slash = f"[/{frac_slash}]"
    for sel_range in sel_ranges:
        # Use mark for the end of the range, since end index can move as changes are made
        maintext().set_mark_position("TempEndSelection", sel_range.end)

        search_range = IndexRange(
            sel_range.start, maintext().rowcol("TempEndSelection")
        )
        match_regex = rf"(\d-)?(\d+){any_slash}(\d+)(?!\d*,\d)"
        while match := maintext().find_match(match_regex, search_range, regexp=True):
            match_str = maintext().get_match_text(match)
            gmatch = re.fullmatch(match_regex, match_str)
            assert gmatch is not None  # Has to match because we used the same regex

            # Allow for matching the "1-" in "1-2/3"
            # match_index is start of section being replaced, i.e. "-2/3"
            offset = 0 if gmatch[1] is None else 1
            match_index = f"{match.rowcol.index()}+{offset}c"

            base_frac = f"{gmatch[2]}/{gmatch[3]}"
            new_frac = ""
            if (
                base_frac in unicode_fractions
                and conversion_type != FractionConvertType.SUPSUB
            ):
                new_frac = unicode_fractions[base_frac]
            elif conversion_type != FractionConvertType.UNICODE:
                new_frac = f"{gmatch[2].translate(superscripts)}{frac_slash}{gmatch[3].translate(subscripts)}"
            # Only convert if we found one that should be converted. Don't convert strings like
            # "B1/2" or "C-1/3" - probably a plate/serial number, but not a fraction.
            # Also don't convert ".2/18" - probably "4.2/18.6" and converting the "2/18" would be wrong.
            prefix = maintext().get(
                f"{match.rowcol.index()} linestart", match.rowcol.index()
            )
            if (
                new_frac
                and not re.search(r"\p{L}-?$", prefix)
                and not prefix.endswith(".")
            ):
                len_frac = len(new_frac)
                maintext().insert(match_index, new_frac)
                maintext().delete(
                    f"{match_index}+{len(new_frac)}c",
                    f"{match_index}+{len(new_frac) + len(gmatch[0]) - offset}c",
                )
                last_match = f"{match_index}+{len(new_frac)}c"
            else:
                len_frac = len(base_frac) - offset

            after_match = maintext().rowcol(f"{match_index}+{len_frac}c")
            search_range = IndexRange(
                after_match, maintext().rowcol("TempEndSelection")
            )
    maintext().selection_ranges_restore_from_marks()
    if last_match:
        maintext().set_insert_index(IndexRowCol(maintext().index(last_match)))


def unicode_normalize() -> None:
    """Normalize selected characters into Unicode Normalization Form C.

    Replaces one line at a time to avoid page marker drift.
    """
    sel_ranges = maintext().selected_ranges()
    if not sel_ranges:
        return

    for sel_range in sel_ranges:
        for row in range(sel_range.start.row, sel_range.end.row + 1):
            # Set start/end columns of first/last rows, else normalize whole line
            if row == sel_range.start.row:
                start_idx = f"{row}.{sel_range.start.col}"
            else:
                start_idx = f"{row}.0"
            if row == sel_range.end.row:
                end_idx = f"{row}.{sel_range.end.col}"
            else:
                end_idx = f"{row}.end"
            text = maintext().get(start_idx, end_idx)
            if not unicodedata.is_normalized("NFC", text):
                normalized_text = unicodedata.normalize("NFC", text)
                maintext().replace(start_idx, end_idx, normalized_text)


def proofer_comment_check() -> None:
    """Find all proofer comments."""

    matches = maintext().find_matches(
        "[**",
        maintext().start_to_end(),
        nocase=False,
        regexp=False,
    )

    class ProoferCommentCheckerDialog(CheckerDialog):
        """Minimal class to identify dialog type so that it can exist
        simultaneously with other checker dialogs."""

        manual_page = "Navigation#Find_Proofer_Comments_(_[**notes]_)"

    checker_dialog = ProoferCommentCheckerDialog.show_dialog(
        "Proofer Comments", rerun_command=proofer_comment_check
    )
    ToolTip(
        checker_dialog.text,
        "\n".join(
            [
                "Left click: Select & find comment",
                "Right click: Remove comment from this list",
            ]
        ),
        use_pointer_pos=True,
    )
    checker_dialog.reset()
    for match in matches:
        line = maintext().get(
            f"{match.rowcol.index()} linestart",
            f"{match.rowcol.index()}+{match.count}c lineend",
        )
        end_rowcol = IndexRowCol(
            maintext().index(match.rowcol.index() + f"+{match.count}c")
        )
        checker_dialog.add_entry(
            line, IndexRange(match.rowcol, end_rowcol), match.rowcol.col, end_rowcol.col
        )
    checker_dialog.display_entries()


def asterisk_check() -> None:
    """Find all asterisks without slashes."""

    # Match single or multiple asterisks including when separated by spaces, e.g. thought break
    matches = maintext().find_matches(
        r"\*( *\*)*",
        maintext().start_to_end(),
        nocase=False,
        regexp=True,
    )

    checker_dialog = AsteriskCheckerDialog.show_dialog(
        "Asterisk Check", rerun_command=asterisk_check
    )
    ToolTip(
        checker_dialog.text,
        "\n".join(
            [
                "Left click: Select & find occurrence of asterisk",
                "Right click: Remove occurrence from this list",
            ]
        ),
        use_pointer_pos=True,
    )
    checker_dialog.reset()
    for match in matches:
        line = maintext().get(
            f"{match.rowcol.index()} linestart",
            f"{match.rowcol.index()} lineend",
        )
        # Skip block markup
        if match.count == 1 and (
            (match.rowcol.col > 0 and line[match.rowcol.col - 1] == "/")
            or (match.rowcol.col < len(line) - 1 and line[match.rowcol.col + 1] == "/")
        ):
            continue
        end_rowcol = IndexRowCol(
            maintext().index(match.rowcol.index() + f"+{match.count}c")
        )
        checker_dialog.add_entry(
            line, IndexRange(match.rowcol, end_rowcol), match.rowcol.col, end_rowcol.col
        )
    checker_dialog.display_entries()


class TextMarkupConvertorDialog(ToplevelDialog):
    """Dialog for converting DP markup to text markup."""

    manual_page = "Text_Menu#Convert_Markup"

    def __init__(self) -> None:
        """Initialize text markup convertor dialog."""
        super().__init__("Text Markup Convertor", resize_x=False, resize_y=False)

        def add_row(row: int, markup: str, prefkey: PrefKey) -> None:
            ttk.Label(
                self.top_frame, text=f"<{markup}>...</{markup}>", anchor=tk.CENTER
            ).grid(column=0, row=row, sticky="NSEW")
            ttk.Button(
                self.top_frame,
                text="Convert ⟹",
                command=lambda: self.convert(
                    rf"</?{markup}>", preferences.get(prefkey)
                ),
            ).grid(
                column=1,
                row=row,
                pady=2,
                sticky="NSEW",
            )
            ttk.Entry(
                self.top_frame,
                width=2,
                textvariable=PersistentString(prefkey),
            ).grid(column=2, row=row, padx=5, pady=2, sticky="NSEW")

        add_row(0, "i", PrefKey.TEXT_MARKUP_ITALIC)
        add_row(1, "b", PrefKey.TEXT_MARKUP_BOLD)
        ttk.Label(self.top_frame, text="<sc>...</sc>", anchor=tk.CENTER).grid(
            column=0, row=2, sticky="NSEW"
        )
        ttk.Button(
            self.top_frame, text="Convert ⟹", command=self.convert_smallcaps_to_allcaps
        ).grid(column=1, row=2, pady=2, sticky="NSEW")
        ttk.Label(self.top_frame, text="ALLCAPS").grid(
            column=2, row=2, padx=5, sticky="NSEW"
        )
        add_row(3, "sc", PrefKey.TEXT_MARKUP_SMALLCAPS)
        add_row(4, "g", PrefKey.TEXT_MARKUP_GESPERRT)
        add_row(5, "f", PrefKey.TEXT_MARKUP_FONT)
        ttk.Label(self.top_frame, text="<tb>", anchor=tk.CENTER).grid(
            column=0, row=6, sticky="NSEW"
        )
        ttk.Button(
            self.top_frame,
            text="Convert ⟹",
            command=lambda: self.convert(r"<tb>", "       *" * 5),
        ).grid(column=1, row=6, pady=2, sticky="NSEW")
        ttk.Label(self.top_frame, text="Asterisks").grid(
            column=2, row=6, padx=5, sticky="NSEW"
        )

    def convert(self, regex: str, replacement: str) -> None:
        """Convert one type of DP markup to text markup.

        Args:
            regex: Regex that matches open/close markup.
            replacement: String to replace markup with.
        """
        found = False
        maintext().undo_block_begin()
        search_range = maintext().start_to_end()
        # Find each piece of markup that matches the regex
        while match := maintext().find_match(
            regex, search_range, regexp=True, nocase=True
        ):
            match_index = match.rowcol.index()
            maintext().replace(
                match_index, f"{match_index}+{match.count}c", replacement
            )
            search_range = IndexRange(
                maintext().index(f"{match_index}+1c"), maintext().end()
            )
            found = True
        if not found:
            sound_bell()

    def convert_smallcaps_to_allcaps(self) -> None:
        """Convert text marked up with <sc> to ALLCAPS."""
        found = False
        maintext().undo_block_begin()
        search_range = maintext().start_to_end()
        # Find start of each smallcap markup
        while match := maintext().find_match(
            "<sc>", search_range, regexp=False, nocase=True
        ):
            match_index = match.rowcol.index()
            search_range = IndexRange(
                maintext().index(f"{match_index}+4c"), maintext().end()
            )
            end_match = maintext().find_match(
                "</sc>", search_range, regexp=False, nocase=True
            )
            if end_match is None:
                logger.error(f"Unclosed <sc> markup on line {match.rowcol.row}")
                maintext().set_insert_index(match.rowcol, focus_widget=maintext())
                return
            end_match_index = end_match.rowcol.index()
            replacement = maintext().get(f"{match_index}+4c", end_match_index).upper()
            maintext().replace(match_index, f"{end_match_index}+5c", replacement)
            found = True
        if not found:
            sound_bell()


class Scanno(dict):
    """Dictionary to hold info about one scanno type.

    Implemented as a dictionary to aid load from JSON file."""

    MATCH = "match"
    REPLACEMENT = "replacement"
    HINT = "hint"

    def __init__(self, match: str, replacement: str, hint: str) -> None:
        self[Scanno.MATCH] = match
        self[Scanno.REPLACEMENT] = replacement
        self[Scanno.HINT] = hint


class ScannoCheckerDialog(CheckerDialog):
    """Dialog to handle stealth scanno checks."""

    manual_page = "Tools_Menu#Stealth_Scannos"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize scanno checker dialog."""
        super().__init__(*args, **kwargs)

        ToolTip(
            self.text,
            "\n".join(
                [
                    "Left click: Select & find occurrence of scanno",
                    "Right click: Remove occurrence of scanno from list",
                    f"With {cmd_ctrl_string()} key: Also fix this occurrence",
                    "With Shift key: Also remove/fix matching occurrences",
                ]
            ),
            use_pointer_pos=True,
        )

        frame = ttk.Frame(self.header_frame)
        frame.grid(column=0, row=1, sticky="NSEW")
        frame.columnconfigure(1, weight=1)

        self.file_combo = Combobox(
            frame,
            PrefKey.SCANNOS_HISTORY,
            textvariable=PersistentString(PrefKey.SCANNOS_FILENAME),
        )
        self.file_combo.grid(column=1, row=0, sticky="NSEW", pady=5)
        self.file_combo["state"] = "readonly"
        self.file_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.rerun_button.invoke()
        )
        ttk.Button(
            frame, text="Load File", command=self.choose_file, takefocus=False
        ).grid(column=2, row=0, sticky="NSEW", padx=(5, 0), pady=5)

        self.prev_btn = ttk.Button(
            frame,
            text="Prev",
            command=lambda: self.prev_next_scanno(prev=True),
            takefocus=False,
        )
        self.prev_btn.grid(column=0, row=1, sticky="NSEW", padx=(0, 5), pady=5)
        self.scanno_textvariable = tk.StringVar(self, "")
        search = ttk.Entry(
            frame,
            textvariable=self.scanno_textvariable,
            state="readonly",
            width=30,
            font=maintext().font,
        )
        search.grid(column=1, row=1, sticky="NSEW", pady=5)
        self.next_btn = ttk.Button(
            frame,
            text="Next",
            command=lambda: self.prev_next_scanno(prev=False),
            takefocus=False,
        )
        self.next_btn.grid(column=2, row=1, sticky="NSEW", padx=(5, 0), pady=5)

        self.replacement_textvariable = tk.StringVar(self, "")
        replace = ttk.Entry(
            frame,
            textvariable=self.replacement_textvariable,
            state="readonly",
            width=30,
            font=maintext().font,
        )
        replace.grid(column=1, row=2, sticky="NSEW")
        ttk.Button(
            frame, text="Replace", command=self.replace_scanno, takefocus=False
        ).grid(column=2, row=2, sticky="NSEW", padx=(5, 0))

        ttk.Label(
            frame,
            text="Hint: ",
        ).grid(column=0, row=3, sticky="NSE", pady=5)
        self.hint_textvariable = tk.StringVar(self, "")
        self.hint = ttk.Entry(
            frame,
            textvariable=self.hint_textvariable,
            state="readonly",
        )
        self.hint.grid(column=1, row=3, sticky="NSEW", pady=5)

        self.scanno_list: list[Scanno] = []
        self.whole_word = False
        self.scanno_number = 0
        self.load_scannos()

    def choose_file(self) -> None:
        """Choose & load a scannos file."""
        filename = filedialog.askopenfilename(
            filetypes=(
                ("Scannos files", "*.json"),
                ("All files", "*.*"),
            )
        )
        if filename:
            self.focus()
            preferences.set(PrefKey.SCANNOS_FILENAME, filename)
            self.load_scannos()

    def load_scannos(self) -> None:
        """Load a scannos file."""
        path = preferences.get(PrefKey.SCANNOS_FILENAME)
        self.file_combo.add_to_history(path)
        scanno_dict = load_dict_from_json(path)
        if scanno_dict is None:
            logger.error(f"Unable to load scannos file {path}")
            return
        # Some scannos files require whole-word searching
        try:
            self.whole_word = scanno_dict["wholeword"]
        except KeyError:
            self.whole_word = False
        try:
            self.scanno_list = scanno_dict["scannos"]
        except KeyError:
            logger.error("Error in scannos file - no 'scannos' array")
            return
        # Validate scannos - must have a match, but replacement and/or hint can
        # be omitted, and default to empty strings
        for scanno in self.scanno_list:
            try:
                scanno[Scanno.MATCH]
            except KeyError:
                logger.error("Error in scannos file - entry with no 'match' string")
                self.scanno_list = []
                return
            try:
                scanno[Scanno.REPLACEMENT]
            except KeyError:
                scanno[Scanno.REPLACEMENT] = ""
            try:
                scanno[Scanno.HINT]
            except KeyError:
                scanno[Scanno.HINT] = ""

        if self.scanno_list:
            self.scanno_number = 0
            self.list_scannos()

    def prev_next_scanno(self, prev: bool) -> None:
        """Display previous/next scanno & list of results.

        Auto-advances until it finds a scanno that has some results.

        Args:
            prev: True for Previous, False for Next.
        """
        slurp_text = maintext().get_text()
        find_range = IndexRange(maintext().start().index(), maintext().end().index())
        while (prev and self.scanno_number > 0) or (
            not prev and self.scanno_number < len(self.scanno_list) - 1
        ):
            self.scanno_number += -1 if prev else 1
            if self.any_matches(slurp_text, find_range):
                self.list_scannos()
                return
        # No matches found, so display first/last scanno
        self.scanno_number = 0 if prev else len(self.scanno_list) - 1
        self.list_scannos()

    def any_matches(self, slurp_text: str, find_range: IndexRange) -> bool:
        """Return quickly whether there are any matches for current scanno.

        Args:
            slurp_text: Whole text of file (to avoid multiple slurping)
            find_range: Whole range of file

        Returns:
            True if there is at least 1 match for the current scanno."""

        match, _ = maintext().find_match_in_range(
            self.scanno_list[self.scanno_number][Scanno.MATCH],
            slurp_text,
            find_range,
            nocase=False,
            regexp=True,
            wholeword=self.whole_word,
            backwards=False,
        )
        return bool(match)

    def list_scannos(self) -> None:
        """Display current scanno and list of results."""
        self.reset()
        scanno = self.scanno_list[self.scanno_number]
        self.scanno_textvariable.set(scanno[Scanno.MATCH])
        self.replacement_textvariable.set(scanno[Scanno.REPLACEMENT])
        self.hint_textvariable.set(scanno[Scanno.HINT])

        find_range = IndexRange(maintext().start().index(), maintext().end().index())
        slurp_text = maintext().get_text()
        slice_start = 0

        while True:
            match, match_start = maintext().find_match_in_range(
                scanno[Scanno.MATCH],
                slurp_text[slice_start:],
                find_range,
                nocase=False,
                regexp=True,
                wholeword=self.whole_word,
                backwards=False,
            )
            if match is None:
                break
            line = maintext().get(
                f"{match.rowcol.index()} linestart",
                f"{match.rowcol.index()}+{match.count}c lineend",
            )
            end_rowcol = IndexRowCol(
                maintext().index(match.rowcol.index() + f"+{match.count}c")
            )
            hilite_start = match.rowcol.col

            # If multiline, lines will be concatenated, so adjust end hilite point
            if end_rowcol.row > match.rowcol.row:
                not_matched = maintext().get(
                    f"{match.rowcol.index()}+{match.count}c",
                    f"{match.rowcol.index()}+{match.count}c lineend",
                )
                hilite_end = len(line) - len(not_matched)
            else:
                hilite_end = end_rowcol.col
            self.add_entry(
                line, IndexRange(match.rowcol, end_rowcol), hilite_start, hilite_end
            )

            # Adjust start of slice of slurped text, and where that point is in the file
            advance = max(match.count, 1)
            slice_start += match_start + advance
            if slice_start >= len(slurp_text):  # No text left to match
                break
            slurp_start = IndexRowCol(
                maintext().index(f"{match.rowcol.index()}+{advance}c")
            )
            find_range = IndexRange(slurp_start, find_range.end)

        # Disable prev/next scanno buttons if at start/end of list
        self.prev_btn["state"] = tk.DISABLED if self.scanno_number <= 0 else tk.NORMAL
        self.next_btn["state"] = (
            tk.DISABLED
            if self.scanno_number >= len(self.scanno_list) - 1
            else tk.NORMAL
        )

        self.display_entries()

    def replace_scanno(self) -> None:
        """Replace current match using replacement"""
        current_index = self.current_entry_index()
        if current_index is None:
            return
        do_replace_scanno(self.entries[current_index])


_the_stealth_scannos_dialog: Optional[ScannoCheckerDialog] = None


def do_replace_scanno(checker_entry: CheckerEntry) -> None:
    """Process the scanno by replacing with the replacement regex/string."""
    assert _the_stealth_scannos_dialog is not None
    if checker_entry.text_range:
        search_string = _the_stealth_scannos_dialog.scanno_textvariable.get()
        replace_string = _the_stealth_scannos_dialog.replacement_textvariable.get()
        start = _the_stealth_scannos_dialog.mark_from_rowcol(
            checker_entry.text_range.start
        )
        end = _the_stealth_scannos_dialog.mark_from_rowcol(checker_entry.text_range.end)
        match_text = maintext().get(start, end)
        replacement = get_regex_replacement(
            search_string, replace_string, match_text, flags=0
        )
        maintext().undo_block_begin()
        maintext().replace(start, end, replacement)


def stealth_scannos() -> None:
    """Report potential stealth scannos in file."""
    global _the_stealth_scannos_dialog

    if not tool_save():
        return

    _the_stealth_scannos_dialog = ScannoCheckerDialog.show_dialog(
        "Stealth Scanno Results",
        rerun_command=stealth_scannos,
        process_command=do_replace_scanno,
    )

    _the_stealth_scannos_dialog.display_entries()


DQUOTES = "“”"
SQUOTES = "‘’"
INIT_APOS_WORDS = (
    "'em",
    "'Tis",
    "'Tisn't",
    "'Tweren't",
    "'Twere",
    "'Twould",
    "'Twouldn't",
    "'Twas",
    "'Im",
    "'Twixt",
    "'Til",
    "'Scuse",
    "'Gainst",
    "'Twon't",
    "'Tain't",
)
SAFE_APOS_RQUOTE_REGEXES = (
    r"(?<=\w)'(?=\w)",  # surrounded by letters
    r"(?<=[\.,\w])'",  # preceded by period, comma or letter
    r"(?<=\w)'(?=\.)",  # between letter & period
    r"'$",  # end of line
    rf"'(?=[ {DQUOTES[1]}])",  # followed by space or close double quote
)


def convert_to_curly_quotes() -> None:
    """Convert straight quotes to curly in whole file, and display list of queries.

    Adapted from https://github.com/DistributedProofreaders/ppwb/blob/master/bin/ppsmq.py
    """
    maintext().undo_block_begin()
    end_line = maintext().end().row
    dqtype = 0
    for line_num in range(1, end_line + 1):
        edited = False
        lstart = f"{line_num}.0"
        lend = f"{line_num}.end"
        line = maintext().get(lstart, lend)
        if line == "":
            dqtype = 0  # Reset double quotes at paragraph break
            continue

        # Apart from special cases, alternate open/close double quotes through each paragraph
        # Ditto marks first - surrounded by double-space. Also permit 0/1 space
        # before ditto if at start of line, or 0/1 space after ditto if at end of line.
        line, count = re.subn('(?<=  )"(?=  )', DQUOTES[1], line)
        if count:
            edited = True
        line, count = re.subn('(?<=^ ?)"(?=  )', DQUOTES[1], line)
        if count:
            edited = True
        line, count = re.subn('(?<=  )"(?= ?$)', DQUOTES[1], line)
        if count:
            edited = True
        # Start of line, must be open quotes
        line, count = re.subn('^"', DQUOTES[0], line)
        if count:
            edited = True
            dqtype = 1
        # Mid-line, alternate open/close quotes
        while True:
            line, count = re.subn('"(?!$)', DQUOTES[dqtype], line, count=1)
            if count:
                edited = True
                dqtype = 0 if dqtype == 1 else 1
            else:
                break
        # End of line must be close quotes
        line, count = re.subn('"$', DQUOTES[1], line)
        if count:
            edited = True
            dqtype = 0

        # Convert apostrophes in specific words
        for straight_word in INIT_APOS_WORDS:
            curly_word = straight_word.replace("'", "’")
            line, count = re.subn(rf"(?!<\w){straight_word}(?!\w)", curly_word, line)
            if count:
                edited = True
            line, count = re.subn(
                rf"(?!<\w){straight_word.lower()}(?!\w)", curly_word.lower(), line
            )
            if count:
                edited = True
        # Now convert specific safe cases to close single quote/apostrophe
        for regex in SAFE_APOS_RQUOTE_REGEXES:
            line, count = re.subn(regex, SQUOTES[1], line)
            if count:
                edited = True

        if edited:
            maintext().replace(lstart, lend, line)

    check_curly_quotes()


class CurlyQuotesDialog(CheckerDialog):
    """Dialog to handle curly quotes checks."""

    manual_page = "Tools_Menu#Convert_to_Curly_Quotes"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize curly quotes checker dialog."""
        super().__init__(*args, **kwargs)

        ToolTip(
            self.text,
            "\n".join(
                [
                    "Left click: Select & find curly quote warning",
                    "Right click: Remove warning from list",
                    f"With {cmd_ctrl_string()} key: Convert straight to curly, or swap open⇔close",
                ]
            ),
            use_pointer_pos=True,
        )

        frame = ttk.Frame(self.header_frame)
        frame.grid(column=0, row=1, sticky="NSEW", pady=5)
        frame.columnconfigure(3, weight=1)
        ttk.Button(
            frame,
            text="Open⇔Close",
            command=self.swap_open_close,
            takefocus=False,
        ).grid(column=0, row=0, sticky="NSW")
        ttk.Button(
            frame,
            text="Straight⇔Curly",
            command=self.swap_straight_curly,
            takefocus=False,
        ).grid(column=1, row=0, sticky="NSW")
        ttk.Button(
            frame,
            text="Swap Quote/Space",
            command=self.swap_quote_space,
            takefocus=False,
        ).grid(column=2, row=0, sticky="NSW")
        ttk.Button(
            frame,
            text="Delete Space",
            command=lambda: self.swap_quote_space(delete=True),
            takefocus=False,
        ).grid(column=3, row=0, sticky="NSW")
        ttk.Label(
            frame,
            text="Insert:",
        ).grid(column=4, row=0, sticky="NSE")
        ttk.Button(
            frame,
            text=DQUOTES[0],
            command=lambda: insert_in_focus_widget(DQUOTES[0]),
            takefocus=False,
        ).grid(column=5, row=0, sticky="NSE")
        ttk.Button(
            frame,
            text=DQUOTES[1],
            command=lambda: insert_in_focus_widget(DQUOTES[1]),
            takefocus=False,
        ).grid(column=6, row=0, sticky="NSE")
        ttk.Button(
            frame,
            text=SQUOTES[0],
            command=lambda: insert_in_focus_widget(SQUOTES[0]),
            takefocus=False,
        ).grid(column=7, row=0, sticky="NSE")
        ttk.Button(
            frame,
            text=SQUOTES[1],
            command=lambda: insert_in_focus_widget(SQUOTES[1]),
            takefocus=False,
        ).grid(column=8, row=0, sticky="NSE")
        self.populate()

    def populate(self) -> None:
        """Populate list with suspect curly quotes."""
        self.reset()
        dqtype = 0
        search_start = maintext().start()
        search_end = maintext().index(tk.END)
        last_open_double_idx = ""
        punctuation = ".,;:!?"
        while match := maintext().find_match(
            rf"^$|[{DQUOTES}{SQUOTES}\"']",
            IndexRange(search_start, search_end),
            regexp=True,
        ):
            search_start = maintext().rowcol(f"{match.rowcol.index()}+1c")
            line_num = match.rowcol.row
            match_text = maintext().get_match_text(match)
            linebeg = f"{line_num}.0"
            lineend = maintext().index(f"{line_num}.end")

            def add_quote_entry(prefix: str) -> None:
                """Add entry highlighting matched quote.

                Args:
                    prefix: Message prefix.
                """
                self.add_entry(
                    maintext().get(linebeg, lineend),
                    IndexRange(
                        match.rowcol,
                        IndexRowCol(match.rowcol.row, match.rowcol.col + 1),
                    ),
                    hilite_start=match.rowcol.col,
                    hilite_end=match.rowcol.col + 1,
                    error_prefix=prefix,
                )

            if match_text == DQUOTES[0]:  # Open double
                context = maintext().get(
                    f"{match.rowcol.index()}-1c", f"{match.rowcol.index()}+2c"
                )
                if dqtype == 1:
                    add_quote_entry("DOUBLE OPEN QUOTE UNEXPECTED: ")
                elif len(context) > 2 and context[2] == "\n":
                    add_quote_entry("DOUBLE OPEN QUOTE AT END OF LINE: ")
                elif len(context) > 2 and context[2] == " ":
                    add_quote_entry("DOUBLE OPEN QUOTE FOLLOWED BY SPACE: ")
                elif context[0].isalnum():
                    add_quote_entry("DOUBLE OPEN QUOTE PRECEDED BY WORD CHARACTER: ")
                elif context[0] in punctuation:
                    add_quote_entry("DOUBLE OPEN QUOTE PRECEDED BY PUNCTUATION: ")
                dqtype = 1
                last_open_double_idx = match.rowcol.index()
            elif match_text == DQUOTES[1]:  # Close double
                # If surrounded by double-spaces or beg/end of line (with optional single space), it's ditto mark, so OK
                context = maintext().get(
                    f"{match.rowcol.index()}-2c", f"{match.rowcol.index()}+3c"
                )
                if (
                    len(context) < 5
                    or re.fullmatch(
                        rf"([\n ] {DQUOTES[1]}  |  {DQUOTES[1]} \n)", context
                    )
                    or context[1:] == f"\n{DQUOTES[1]}  "
                    or context[:4] == f"  {DQUOTES[1]}\n"
                ):
                    continue
                if dqtype == 0:
                    add_quote_entry("DOUBLE CLOSE QUOTE UNEXPECTED: ")
                elif context[1] == "\n":
                    add_quote_entry("DOUBLE CLOSE QUOTE AT START OF LINE: ")
                elif context[1] == " ":
                    add_quote_entry("DOUBLE CLOSE QUOTE PRECEDED BY SPACE: ")
                elif context[3].isalnum():
                    add_quote_entry("DOUBLE CLOSE QUOTE FOLLOWED BY LETTER: ")
                dqtype = 0
            elif match_text == SQUOTES[0]:  # Open single
                context = maintext().get(
                    f"{match.rowcol.index()}-1c", f"{match.rowcol.index()}+2c"
                )
                if len(context) > 2 and context[2] == "\n":
                    add_quote_entry("SINGLE OPEN QUOTE AT END OF LINE: ")
                elif len(context) > 2 and context[2] == " ":
                    add_quote_entry("SINGLE OPEN QUOTE FOLLOWED BY SPACE: ")
                elif context[0].isalnum():
                    add_quote_entry("SINGLE OPEN QUOTE PRECEDED BY WORD CHARACTER: ")
                elif context[0] in punctuation:
                    add_quote_entry("SINGLE OPEN QUOTE PRECEDED BY PUNCTUATION: ")
            elif match_text == SQUOTES[1]:  # Close single
                pass  # Close singles/apostrophes can go almost anywhere
            elif match_text == '"':  # Straight double
                add_quote_entry("DOUBLE QUOTE NOT CONVERTED: ")
            elif match_text == "'":  # Straight single
                add_quote_entry("SINGLE QUOTE NOT CONVERTED: ")
            elif match_text == "":  # Blank line
                # Expect dqtype == 0 unless next line starts with open double quote
                if dqtype == 1 and maintext().get(f"{linebeg} +1l") != DQUOTES[0]:
                    hilite_start = IndexRowCol(last_open_double_idx).col
                    self.add_entry(
                        maintext().get(
                            f"{last_open_double_idx} linestart",
                            f"{last_open_double_idx} lineend",
                        ),
                        IndexRange(
                            last_open_double_idx,
                            maintext().rowcol(f"{last_open_double_idx}+1c"),
                        ),
                        hilite_start=hilite_start,
                        hilite_end=hilite_start + 1,
                        error_prefix="DOUBLE QUOTE NOT CLOSED: ",
                    )
                dqtype = 0  # Reset double quotes at paragraph break

    def swap_open_close(self) -> None:
        """Swap current quote open<-->close."""
        entry_index = self.current_entry_index()
        if entry_index is None:
            return
        do_swap_open_close(self.entries[entry_index])

    def swap_quote_space(self, delete: bool = False) -> None:
        """Swap current quote with adjacent space, or just delete it.

        Args:
            delete: True to just delete space instead of swapping.
        """
        entry_index = self.current_entry_index()
        if entry_index is None:
            return
        checker_entry = self.entries[entry_index]
        if checker_entry.text_range:
            start_mark = self.mark_from_rowcol(checker_entry.text_range.start)
            end_mark = self.mark_from_rowcol(checker_entry.text_range.end)
            start = maintext().index(f"{start_mark}-1c")
            end = maintext().index(f"{start_mark}+2c")
            match_text = maintext().get(start, end)
            if match_text[1] not in "“”‘’":
                return
            maintext().undo_block_begin()
            # Temporarily change mark gravities so moved space ends up outside marks
            maintext().mark_gravity(start_mark, tk.RIGHT)
            maintext().mark_gravity(end_mark, tk.LEFT)
            # If only space before, or space both sides and it's close quote, swap/delete space before
            if match_text[0] == " " and (match_text[2] != " " or match_text[1] in "”’"):
                maintext().delete(start)
                if not delete:
                    maintext().insert(f"{start}+1c", " ")
            # If only space after, or space both sides and it's open quote, swap/delete space after
            elif (match_text[0] != " " or match_text[1] in "“‘") and match_text[
                2
            ] == " ":
                maintext().delete(f"{start}+2c")
                if not delete:
                    maintext().insert(f"{start}+1c", " ")
            maintext().mark_gravity(start_mark, tk.LEFT)
            maintext().mark_gravity(end_mark, tk.RIGHT)

    def swap_straight_curly(self) -> None:
        """Swap current quote with straight/curly(open) equivalent."""
        entry_index = self.current_entry_index()
        if entry_index is None:
            return
        do_swap_straight_curly(self.entries[entry_index])


_the_curly_quotes_dialog: Optional[CurlyQuotesDialog] = None


def do_fix_quote(checker_entry: CheckerEntry) -> None:
    """Fix the quote problem."""
    assert _the_curly_quotes_dialog is not None
    assert checker_entry.text_range is not None
    if (
        checker_entry.error_prefix
        in (
            "DOUBLE QUOTE NOT CONVERTED: ",
            "SINGLE QUOTE NOT CONVERTED: ",
        )
        and maintext().get(
            _the_curly_quotes_dialog.mark_from_rowcol(checker_entry.text_range.start),
            _the_curly_quotes_dialog.mark_from_rowcol(checker_entry.text_range.end),
        )
        in "'\""
    ):
        do_swap_straight_curly(checker_entry)
    else:
        do_swap_open_close(checker_entry)


def do_swap_straight_curly(checker_entry: CheckerEntry) -> None:
    """Process given entry by swapping straight/curly quotes."""
    do_process_with_dict(
        checker_entry, {'"': "“", "'": "‘", "“": '"', "”": '"', "‘": "'", "’": "'"}
    )


def do_swap_open_close(checker_entry: CheckerEntry) -> None:
    """Process given entry by swapping the quote open<-->close."""
    do_process_with_dict(checker_entry, {"“": "”", "”": "“", "‘": "’", "’": "‘"})


def do_process_with_dict(
    checker_entry: CheckerEntry, swap_dict: dict[str, str]
) -> None:
    """Process given entry by swapping the selected character for the one in the dict.

    Args:
        swap_dict: Dictionary of which character to swap for which.
    """
    assert _the_curly_quotes_dialog is not None
    if not checker_entry.text_range:
        return
    start = _the_curly_quotes_dialog.mark_from_rowcol(checker_entry.text_range.start)
    end = _the_curly_quotes_dialog.mark_from_rowcol(checker_entry.text_range.end)
    match_text = maintext().get(start, end)
    maintext().undo_block_begin()
    try:
        maintext().replace(start, end, swap_dict[match_text])
    except KeyError:
        pass  # User has edited since tool was run
    # Reselect to refresh highlighting
    if cur_idx := _the_curly_quotes_dialog.current_entry_index():
        _the_curly_quotes_dialog.select_entry_by_index(cur_idx)


def sort_key_error(
    entry: CheckerEntry,
) -> tuple[int, str, int, int]:
    """Sort key function to sort Curly Quote entries by text, putting identical upper
        and lower case versions together.

    Differs from default alpha sort in using the error prefix text (the type of error)
    as the primary text sort key.
    No need to deal with different entry types and all entries have a text_range.
    """
    assert entry.text_range is not None
    return (
        entry.section,
        entry.error_prefix,
        entry.text_range.start.row,
        entry.text_range.start.col,
    )


def check_curly_quotes() -> None:
    """Check for suspect curly quotes."""
    global _the_curly_quotes_dialog

    _the_curly_quotes_dialog = CurlyQuotesDialog.show_dialog(
        "Curly Quotes Check",
        rerun_command=check_curly_quotes,
        process_command=do_fix_quote,
        sort_key_alpha=sort_key_error,
    )

    _the_curly_quotes_dialog.display_entries()
