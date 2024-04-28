"""Miscellaneous tools."""

import logging
from tkinter import messagebox

import regex as re

from guiguts.checkers import CheckerDialog, CheckerEntry
from guiguts.file import the_file
from guiguts.maintext import maintext
from guiguts.utilities import IndexRowCol, IndexRange, cmd_ctrl_string
from guiguts.widgets import ToolTip

logger = logging.getLogger(__package__)

BLOCK_TYPES = "[$*XxFf]"
POEM_TYPES = "[Pp]"


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
    start_mark = CheckerDialog.mark_from_rowcol(checker_entry.text_range.start)
    end_mark = CheckerDialog.mark_from_rowcol(checker_entry.text_range.end)
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

    checker_dialog = CheckerDialog.show_dialog(
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
