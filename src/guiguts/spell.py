"""Spell checking functionality"""

import importlib.resources
import logging
from pathlib import Path
from tkinter import ttk
from typing import Callable, Optional
import regex as re

from guiguts.data import dictionaries
from guiguts.file import ProjectDict
from guiguts.checkers import CheckerDialog, CheckerEntry
from guiguts.maintext import maintext, FindMatch
from guiguts.misc_tools import tool_save
from guiguts.preferences import preferences, PersistentInt, PrefKey
from guiguts.utilities import (
    IndexRowCol,
    IndexRange,
    load_wordfile_into_dict,
    cmd_ctrl_string,
    process_accel,
)
from guiguts.widgets import ToolTip

logger = logging.getLogger(__package__)

DEFAULT_DICTIONARY_DIR = importlib.resources.files(dictionaries)

SPELL_CHECK_OK_YES = 0
SPELL_CHECK_OK_NO = 1
SPELL_CHECK_OK_BAD = 2

_the_spell_checker: Optional["SpellChecker"] = None  # pylint: disable=invalid-name


class DictionaryNotFoundError(Exception):
    """Raised when no dictionary found to match languages."""

    def __init__(self, language: str) -> None:
        self.language = language


class SpellingError(FindMatch):
    """Class containing info about spelling error.

    Attributes:
        rowcol: Location of start of word in file.
        count: Number of characters in word.
        word: Word spelt wrongly.
        frequency: Number of occurrences of word.
        bad_word: True if word is in project's bad word list
    """

    def __init__(self, index: IndexRowCol, word: str, bad_word: bool):
        """Initialize SpellingError class."""
        super().__init__(index, len(word))
        self.word = word
        self.frequency = 0
        self.bad_word = bad_word


class SpellCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Tools_Menu#Spelling"


class SpellChecker:
    """Provides spell check functionality."""

    def __init__(self) -> None:
        """Initialize SpellChecker class."""
        self.dictionary: dict[str, bool] = {}
        self.language_list = maintext().get_language_list()
        for lang in self.language_list:
            self.add_words_from_language(lang)

    def do_spell_check(self, project_dict: ProjectDict) -> list[SpellingError]:
        """Spell check the currently loaded file, or just the selected range(s).

        Returns:
            List of spelling errors.
        """
        spelling_errors = []
        spelling_counts: dict[str, int] = {}

        minrow = mincol = maxrow = maxcol = 0
        if sel_ranges := maintext().selected_ranges():
            minrow = sel_ranges[0].start.row
            mincol = sel_ranges[0].start.col
            maxrow = sel_ranges[-1].end.row
            maxcol = sel_ranges[-1].end.col
        column_selection = len(sel_ranges) > 1
        for line, line_num in maintext().get_lines():
            # Handle doing selection only
            if sel_ranges:
                # If haven't reached the line range, skip
                if line_num < minrow:
                    continue
                # If past the line range, stop
                if line_num > maxrow:
                    break
                # Clear the columns outside the selection
                if column_selection or line_num == minrow:
                    line = mincol * " " + line[mincol:]
                if column_selection or line_num == maxrow:
                    line = line[:maxcol]

            words = re.split(r"[^\p{Alnum}\p{Mark}'’]", line)
            next_col = 0
            for word in words:
                col = next_col
                next_col = col + len(word) + 1
                if word:
                    spell_check_result = self.spell_check_word(word, project_dict)

                    # If word has leading straight apostrophe, it might be
                    # open single quote; trim it and check again
                    if spell_check_result == SPELL_CHECK_OK_NO and word.startswith("'"):
                        word = word[1:]
                        col += 1
                        spell_check_result = self.spell_check_word(word, project_dict)

                    # If trailing straight/curly apostrophe, it might be
                    # close single quote; trim it and check again
                    if spell_check_result == SPELL_CHECK_OK_NO and re.search(
                        r"['’]$", word
                    ):
                        word = word[:-1]
                        spell_check_result = self.spell_check_word(word, project_dict)

                    # If not found in dictionary, and word is not now empty
                    # or only consisting of single quotes, add it to errors list
                    if spell_check_result != SPELL_CHECK_OK_YES and not re.fullmatch(
                        r"['’]*", word
                    ):
                        spelling_errors.append(
                            SpellingError(
                                IndexRowCol(line_num, col),
                                word,
                                spell_check_result == SPELL_CHECK_OK_BAD,
                            )
                        )
                        try:
                            spelling_counts[word] += 1
                        except KeyError:
                            spelling_counts[word] = 1
        # Now all spelling errors have been found, update the frequency of each one
        for spelling in spelling_errors:
            spelling.frequency = spelling_counts[spelling.word]
        return spelling_errors

    def spell_check_word(self, word: str, project_dict: ProjectDict) -> int:
        """Spell check the given word.

        Allows things like numbers, years, decades, etc.

        Args:
            word: Word to be checked.

        Returns:
            SPELL_CHECK_OK_BAD if word is in the bad_words list
            SPELL_CHECK_OK_YES if word is in dictionary
            SPELL_CHECK_OK_NO if neither (wrong spelling)
        """
        word_status = self.spell_check_word_apos(word, project_dict)
        # If it's a bad word or in the dictionary, we're done
        if word_status in (SPELL_CHECK_OK_BAD, SPELL_CHECK_OK_YES):
            return word_status

        # Some languages use l', quest', etc., before word.
        # Accept if the "prefix" and the remainder are both good.
        # Prefix can be with or without apostrophe, but dictionary
        # is safer if prefix has apostrophe to avoid prefix being
        # accepted it it is used as a standalone word.
        match = re.match(r"(?P<prefix>\w+)['’](?P<remainder>\w+)", word)
        if (
            match is not None
            and self.spell_check_word_apos(match.group("remainder"), project_dict)
            == SPELL_CHECK_OK_YES
            and (
                self.spell_check_word_apos(match.group("prefix"), project_dict)
                == SPELL_CHECK_OK_YES
                or self.spell_check_word_apos(match.group("prefix") + "'", project_dict)
                == SPELL_CHECK_OK_YES
                or self.spell_check_word_apos(match.group("prefix") + "’", project_dict)
                == SPELL_CHECK_OK_YES
            )
        ):
            return SPELL_CHECK_OK_YES

        # Now check numbers
        if (
            # word is all digits
            re.fullmatch(r"\d+", word)  # pylint: disable=too-many-boolean-expressions
            # ...1st, ...21st, ...31st, etc
            or re.fullmatch(r"(\d*[02-9])?1st", word, flags=re.IGNORECASE)
            # ...2nd, ...22nd, ...32nd, etc (also 2d, 22d, etc)
            or re.fullmatch(r"(\d*[02-9])?2n?d", word, flags=re.IGNORECASE)
            # ...3rd, ...23rd, ...33rd, etc (also 3d, 33d, etc)
            or re.fullmatch(r"(\d*[02-9])?3r?d", word, flags=re.IGNORECASE)
            # ...0th, ...4th, ...5th, etc
            or re.fullmatch(r"\d*[04-9]th", word, flags=re.IGNORECASE)
            # ...11th, ...12th, ...13th
            or re.fullmatch(r"\d*1[123]th", word, flags=re.IGNORECASE)
        ):
            return SPELL_CHECK_OK_YES

        # Allow decades/years
        if (
            # e.g. '20s or 20s (abbreviation for 1820s)
            re.fullmatch(r"['’]?\d\ds", word)
            # e.g. '62 (abbreviation for 1862)
            or re.fullmatch(r"['’]\d\d", word)
            # e.g. 1820s
            or re.fullmatch(r"1\d{3}s", word)
        ):
            return SPELL_CHECK_OK_YES

        # Allow abbreviations for shillings and pence (not pounds
        # because 20l is common scanno for the number 201)
        # e.g. 15s or 6d (up to 2 digits of old English shillings and pence)
        if re.fullmatch(r"\d{1,2}[sd]", word):
            return SPELL_CHECK_OK_YES

        # <sc> DP markup
        if re.fullmatch(r"sc", word, flags=re.IGNORECASE):
            return SPELL_CHECK_OK_YES

        return SPELL_CHECK_OK_NO

    def spell_check_word_apos(self, word: str, project_dict: ProjectDict) -> int:
        """Spell check the given word.

        Copes with straight or curly apostrophes.

        Args:
            word: Word to be checked.

        Returns:
            SPELL_CHECK_OK_BAD if word is in the bad_words list
            SPELL_CHECK_OK_YES if word is in dictionary
            SPELL_CHECK_OK_NO if neither (wrong spelling)
        """
        word_status = self.spell_check_word_case(word, project_dict)
        # If it's a bad word or in the dictionary, we're done
        if word_status in (SPELL_CHECK_OK_BAD, SPELL_CHECK_OK_YES):
            return word_status

        # Otherwise, try swapping apostrophes if there are any, and re-check
        if "'" in word:
            word = re.sub(r"'", r"’", word)
        elif "’" in word:
            word = re.sub(r"’", r"'", word)
        else:
            return word_status
        return self.spell_check_word_case(word, project_dict)

    def spell_check_word_case(self, word: str, project_dict: ProjectDict) -> int:
        """Spell check word, allowing for differences of case.

        Check same case, lower case, or title case (e.g. LONDON matches London)
        Can't just do case-insensitive check because we don't want "london" to be OK.

        Args:
            word: Word to be checked.

        Returns:
            SPELL_CHECK_OK_BAD if word is in the bad_words list
            SPELL_CHECK_OK_YES if word is in dictionary
            SPELL_CHECK_OK_NO if neither (wrong spelling)
        """
        # Check if word is a bad word - case must match for bad words
        if word in project_dict.bad_words:
            return SPELL_CHECK_OK_BAD

        lower_word = word.lower()
        if lower_word == word:
            lower_word = ""
        # Caution converting to title case - we want "LONDON" to match "London",
        # but we don't want "london" to match London, so leave first letter case
        # as-is, and lowercase the rest
        if len(word) > 1:
            title_word = word[0:1] + word[1:].lower()
            if title_word == word:
                title_word = ""
        else:
            title_word = ""

        # Now check global & project dictionaries for good words
        for dictionary in (self.dictionary, project_dict.good_words):
            if (
                word in dictionary
                or (lower_word and lower_word in dictionary)
                or (title_word and title_word in dictionary)
            ):
                return SPELL_CHECK_OK_YES

        return SPELL_CHECK_OK_NO

    def add_words_from_language(self, lang: str) -> None:
        """Add dictionary words for given language.

        Attempts to load default and user dictionaries.
        Outputs warning if default dictionary doesn't exist.
        Raises DictionaryNotFoundError exception if neither dictionary exists.

        Args:
            lang: Language to be loaded.
        """
        path = DEFAULT_DICTIONARY_DIR.joinpath(f"dict_{lang}_default.txt")
        words_loaded = load_wordfile_into_dict(path, self.dictionary)
        if not words_loaded:
            logger.warning(f"No default dictionary for language {lang}")

        path = Path(preferences.prefsdir, f"dict_{lang}_user.txt")
        if load_wordfile_into_dict(path, self.dictionary):
            words_loaded = True
        if not words_loaded:  # Neither default nor user dictionary exist
            raise DictionaryNotFoundError(lang)


def get_spell_checker() -> SpellChecker | None:
    """Avoid duplicate spell checker by returning a SpellChecker object to
    calling tool/application.

    Returns:
        A SpellChecker object if required dictionary present, otherwise None
    """

    global _the_spell_checker

    # If we already have a spell checker with the wrong languages, delete it
    if (
        _the_spell_checker is not None
        and _the_spell_checker.language_list != maintext().get_language_list()
    ):
        _the_spell_checker = None
    if _the_spell_checker is None:
        try:
            _the_spell_checker = SpellChecker()
        except DictionaryNotFoundError as exc:
            logger.error(f"Dictionary not found for language: {exc.language}")
            return None
    return _the_spell_checker


def spell_check(
    project_dict: ProjectDict,
    add_project_word_callback: Callable[[str], None],
    add_global_word_callback: Callable[[str], None],
) -> None:
    """Spell check the currently loaded file."""

    if not tool_save():
        return

    checker = get_spell_checker()
    if checker is None:
        return
    bad_spellings = checker.do_spell_check(project_dict)

    def process_spelling(checker_entry: CheckerEntry) -> None:
        """Process the spelling error by adding the word to the project dictionary."""
        if checker_entry.text_range:
            add_project_word_callback(checker_entry.text.split(maxsplit=1)[0])

    checker_dialog = SpellCheckerDialog.show_dialog(
        "Spelling Check Results",
        rerun_command=lambda: spell_check(
            project_dict, add_project_word_callback, add_global_word_callback
        ),
        process_command=process_spelling,
        tooltip="\n".join(
            [
                "Left click: Select & find spelling error",
                "Right click: Remove spelling error from list",
                "Shift Right click: Remove all occurrences of spelling error from list",
                f"With {cmd_ctrl_string()} key: Also add spelling to project dictionary",
            ]
        ),
        switch_focus_when_clicked=False,
    )
    frame = ttk.Frame(checker_dialog.header_frame)
    frame.grid(column=0, row=1, sticky="NSEW", pady=5)
    ttk.Label(
        frame,
        text="Threshold ≤ ",
    ).grid(column=0, row=0, sticky="NSW")
    threshold_spinbox = ttk.Spinbox(
        frame,
        textvariable=PersistentInt(PrefKey.SPELL_THRESHOLD),
        from_=1,
        to=999,
        width=4,
    )
    threshold_spinbox.grid(column=1, row=0, sticky="NW", padx=(0, 10))
    ToolTip(
        threshold_spinbox,
        "Do not show errors that appear more than this number of times",
    )

    def invoke_and_break(button: ttk.Button) -> str:
        """Invoke a button and return "break" to avoid further callbacks."""
        button.invoke()
        return "break"

    def add_to_global_dict() -> None:
        """Add current word to global dictionary."""
        current_index = checker_dialog.current_entry_index()
        if current_index is None:
            return
        checker_entry = checker_dialog.entries[current_index]
        if checker_entry.text_range:
            word = checker_entry.text.split(maxsplit=1)[0]
            add_global_word_callback(word)
            checker.dictionary[word] = True
        checker_dialog.remove_entry_current(all_matching=True)

    lang = maintext().get_language_list()[0]
    global_dict_button = ttk.Button(
        frame,
        text=f"Add to Global Dict ({lang})",
        command=add_to_global_dict,
    )
    global_dict_button.grid(column=2, row=0, sticky="NSW")
    for accel in ("Cmd/Ctrl+a", "Cmd/Ctrl+A"):
        _, key_event = process_accel(accel)
        checker_dialog.bind(key_event, lambda _: invoke_and_break(global_dict_button))
    ToolTip(
        global_dict_button,
        f"{cmd_ctrl_string()}+A",
        use_pointer_pos=True,
    )
    project_dict_button = ttk.Button(
        frame,
        text="Add to Project Dict",
        command=lambda: checker_dialog.process_remove_entry_current(all_matching=True),
    )
    project_dict_button.grid(column=3, row=0, sticky="NSW")
    for accel in ("Cmd/Ctrl+p", "Cmd/Ctrl+P"):
        _, key_event = process_accel(accel)
        checker_dialog.bind(key_event, lambda _: invoke_and_break(project_dict_button))
    ToolTip(
        project_dict_button,
        f"{cmd_ctrl_string()}+P",
        use_pointer_pos=True,
    )
    skip_button = ttk.Button(
        frame,
        text="Skip",
        command=lambda: checker_dialog.remove_entry_current(all_matching=False),
    )
    skip_button.grid(column=4, row=0, sticky="NSW")
    for accel in ("Cmd/Ctrl+s", "Cmd/Ctrl+S"):
        _, key_event = process_accel(accel)
        checker_dialog.bind(key_event, lambda _: invoke_and_break(skip_button))
    ToolTip(
        skip_button,
        f"{cmd_ctrl_string()}+S",
        use_pointer_pos=True,
    )
    skip_all_button = ttk.Button(
        frame,
        text="Skip All",
        command=lambda: checker_dialog.remove_entry_current(all_matching=True),
    )
    skip_all_button.grid(column=5, row=0, sticky="NSW")
    for accel in ("Cmd/Ctrl+i", "Cmd/Ctrl+I"):
        _, key_event = process_accel(accel)
        checker_dialog.bind(key_event, lambda _: invoke_and_break(skip_all_button))
    ToolTip(
        skip_all_button,
        f"{cmd_ctrl_string()}+I",
        use_pointer_pos=True,
    )

    checker_dialog.reset()
    # Construct opening line describing the search
    sel_only = " (selected text only)" if len(maintext().selected_ranges()) > 0 else ""
    checker_dialog.add_header("Start of Spelling Check" + sel_only, "")

    threshold = preferences.get(PrefKey.SPELL_THRESHOLD)
    for spelling in bad_spellings:
        if spelling.frequency > threshold:
            continue
        end_rowcol = IndexRowCol(
            maintext().index(spelling.rowcol.index() + f"+{spelling.count}c")
        )
        bad_str = " ***" if spelling.bad_word else ""
        checker_dialog.add_entry(
            f"{spelling.word} ({spelling.frequency})" + bad_str,
            IndexRange(spelling.rowcol, end_rowcol),
            0,
            spelling.count,
        )
    checker_dialog.add_footer("", "End of Spelling Check" + sel_only)
    checker_dialog.display_entries()
