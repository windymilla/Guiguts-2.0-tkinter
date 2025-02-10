"""Footnote checking, fixing and tidying functionality"""

import logging
from enum import StrEnum, auto
import tkinter as tk
from tkinter import ttk
from typing import Optional
import regex as re
import roman  # type: ignore[import-untyped]

from guiguts.checkers import CheckerDialog, CheckerEntry
from guiguts.maintext import maintext
from guiguts.misc_tools import tool_save
from guiguts.preferences import (
    PersistentString,
    PrefKey,
)
from guiguts.utilities import (
    IndexRowCol,
    IndexRange,
)

logger = logging.getLogger(__package__)

_the_footnote_checker: Optional["FootnoteChecker"] = (
    None  # pylint: disable=invalid-name
)

INSERTION_MARK_PREFIX = "Shadow"


class FootnoteIndexStyle(StrEnum):
    """Enum class to store FN index style types."""

    NUMBER = auto()
    LETTER = auto()
    ROMAN = auto()


class AnchorRecord:
    """Record of anchor in file."""

    def __init__(
        self,
        text: str,
        start: IndexRowCol,
        end: IndexRowCol,
        hilite_start: int,
        hilite_end: int,
        fn_index: int,
    ) -> None:
        """Initialize AnchorRecord.

        Args:
            text - Text of the anchor.
            start - Start rowcol of anchor in file.
            end - End rowcol of anchor in file.
            hilite_start - Start column of highlighting in text.
            hilite_end - End column of highlighting in text.
            fn_index - Index into footnotes array of linked footnote.
        """
        self.text = text
        self.start = start
        self.end = end
        self.hilite_start = hilite_start
        self.hilite_end = hilite_end
        self.fn_index = fn_index


class FootnoteRecord:
    """Record of footnote in file."""

    def __init__(
        self,
        text: str,
        start: IndexRowCol,
        end: IndexRowCol,
        hilite_start: int,
        hilite_end: int,
        an_index: Optional[int],
    ) -> None:
        """Initialize FootnoteRecord.

        Args:
            text - Text of the footnote.
            start - Start rowcol of footnote in file.
            end - End rowcol of footnote in file.
            hilite_start - Start column of highlighting in text.
            hilite_end - End column of highlighting in text.
            an_index - Index into anchors array of linked anchor.
        """
        self.text = text
        self.start = start
        self.end = end
        self.hilite_start = hilite_start
        self.hilite_end = hilite_end
        self.an_index = an_index


class FootnoteCheckerDialog(CheckerDialog):
    """Minimal class to identify dialog type."""

    manual_page = "Tools_Menu#Footnote_Fixup"


class FootnoteChecker:
    """Find, check & record footnotes."""

    def __init__(self, checker_dialog: FootnoteCheckerDialog) -> None:
        """Initialize footnote checker."""
        self.fn_records: list[FootnoteRecord] = []
        self.an_records: list[AnchorRecord] = []
        self.checker_dialog: FootnoteCheckerDialog = checker_dialog
        self.fn_index_style = PersistentString(PrefKey.FOOTNOTE_INDEX_STYLE)
        self.fn_check_errors = False
        self.fns_have_been_moved = False

    def reset(self) -> None:
        """Reset FootnoteChecker."""
        self.fn_records = []
        self.an_records = []

    def get_fn_records(self) -> list[FootnoteRecord]:
        """Return the list of footnote records."""
        return self.fn_records

    def get_an_records(self) -> list[AnchorRecord]:
        """Return the list of anchor records."""
        return self.an_records

    def join_to_previous(self) -> None:
        """Join the selected footnote to the previous one."""
        maintext().undo_block_begin()
        fn_index = self.get_selected_fn_index()
        if fn_index < 0:
            return  # No selection
        if fn_index == 0:
            return  # Can't join first footnote to previous
        fn_records = self.get_fn_records()
        fn_record = fn_records[fn_index]
        fn_cur_start = self.checker_dialog.mark_from_rowcol(fn_record.start)
        fn_cur_end = self.checker_dialog.mark_from_rowcol(fn_record.end)
        prev_record = fn_records[fn_index - 1]
        fn_prev_end = self.checker_dialog.mark_from_rowcol(prev_record.end)
        continuation_text = maintext().get(fn_cur_start, fn_cur_end)[11:]
        maintext().delete(
            f"{fn_cur_start} -1l linestart", f"{fn_cur_end} +1l linestart"
        )
        maintext().delete(f"{fn_prev_end} -1c", f"{fn_prev_end} lineend")
        maintext().insert(fn_prev_end, "\n" + continuation_text)
        self.checker_dialog.remove_entry_current()
        # Note index of the record to which we joined the continuation text.
        saved_prev_rec_index = fn_index - 1
        self.run_check()
        display_footnote_entries(auto_select_line=False)
        fn_records = self.get_fn_records()
        prev_record = fn_records[saved_prev_rec_index]
        # Get index into dialog entries for this record.
        dialog_entry_index = self.map_fn_record_to_dialog_index(prev_record)
        if dialog_entry_index < 0:
            logger.error("Unexpected return from 'join_to_previous()'")
            return
        # Make it the selected entry in the dialog.
        self.checker_dialog.select_entry_by_index(dialog_entry_index)

    def autoset_chapter_lz(self) -> None:
        """Insert a 'FOOTNOTES:' landing zone (LZ) header line before each
        chapter break.

        If chapters are separated correctly from the next chapter by 4 blank
        lines then there will always be a LZ header inserted at the end of
        each chapter, except at the end of the last chapter. Append a header
        to end of file to catch any footnotes in the last chapter. This means
        that all footnotes will then have a LZ header below them.

        No LZ headers are inserted before the first anchor to avoid adding
        them at pseudo chapter breaks in front matter, etc. Unused LZ headers
        are deleted after footnotes have been moved to LZs - see call to
        remove_unused_lz_headers() in the function move_footnotes_to_lz().
        """
        maintext().undo_block_begin()
        # If the last line of the file is not a blank line then add one.
        end_of_file = maintext().end().index()
        if maintext().get(f"{end_of_file} linestart", f"{end_of_file} lineend") != "":
            self.add_blank_line_at_eof()
        # Append LZ header to end of file to catch footnotes in the last chapter
        self.autoset_end_lz()
        # Set search range for finding 'chapter breaks'. Look for them from the
        # start of the first anchor rather than after the first 200 lines as
        # in GG1.
        an_records = self.get_an_records()
        first_an = an_records[0]
        search_range = IndexRange(first_an.start, maintext().end())
        # Loop, finding all chapter breaks; i.e. block of 4 blank lines. Method
        # used is to find next blank line then look ahead for 3 more blank lines.
        # If present then set a LZ and advance 6 lines and repeat. If look ahead
        # does not find 3 more blank lines then advance 1 line and repeat. This
        # is based on GG1 method.
        match_regex = r"^$"
        while True:
            beg_match = maintext().find_match(match_regex, search_range, regexp=True)
            if not beg_match:
                break
            start_index = beg_match.rowcol.index()
            # If next three lines are also empty, insert a LZ "FOOTNOTES:" header
            # before the 4-line block.
            if (
                maintext().get(
                    f"{start_index} +1l linestart", f"{start_index} +1l lineend"
                )
                == ""
                and maintext().get(
                    f"{start_index} +2l linestart", f"{start_index} +2l lineend"
                )
                == ""
                and maintext().get(
                    f"{start_index} +3l linestart", f"{start_index} +3l lineend"
                )
                == ""
            ):
                # Insert the LZ header.
                self.set_lz(start_index)
                # Advance past inserted LZ.
                restart_point = maintext().rowcol(f"{start_index} +6l")
            else:
                # Advance to avoid finding same blank line again.
                restart_point = maintext().rowcol(f"{start_index} +1l")
            search_range = IndexRange(restart_point, maintext().end())

        # The file has changed so rebuild AN/FN records and refresh dialog.
        self.run_check()
        # Order below is important. A flag is set in display_footnote_entries()
        # that will determine which, if any, buttons are disabled when they are
        # displayed.
        display_footnote_entries()
        self.display_buttons()

    def set_lz(self, lz_index: str) -> None:
        """Add a 'FOOTNOTES:' LZ header at the given index.

        Arg:
            lz_index: index of position at which to add LZ header.
        """
        # If the insert point is a blank line immediately after a Page Marker,
        # place the insert point at the start of the Page Marker as GG1 does. If
        # Page Markers have been removed then look for a page mark at the insertion
        # point and place the insertion point before that page mark. Recall that
        # the gravity setting used for marks is tk.RIGHT.
        #
        # Get line immediately before the insertion point. We'll test below if it
        # is a Page Marker record.
        line_before = maintext().get(
            f"{lz_index} -1l linestart", f"{lz_index} -1l lineend"
        )
        # Get page mark if we are relying on page marks because Page Separator Fixup
        # has been run.
        prev_page_mark = maintext().page_mark_previous(lz_index)
        # Set 'prev_page_mark' to be lz_index if it's not a page mark.
        if not maintext().is_page_mark(prev_page_mark):
            prev_page_mark = lz_index
        if line_before[0:11] == "-----File: ":
            lz_index = maintext().rowcol(f"{lz_index} -1l linestart").index()
        maintext().insert(f"{lz_index} -1c", "\n\n\n" + "FOOTNOTES:")

    def autoset_end_lz(self) -> None:
        """Insert a 'FOOTNOTES:' LZ header line at end of file."""
        maintext().undo_block_begin()
        # If the last line of the file is not a blank line then add one.
        end_of_file = maintext().end().index()
        if maintext().get(f"{end_of_file} linestart", f"{end_of_file} lineend") != "":
            self.add_blank_line_at_eof()
        end_of_file = maintext().end().index()
        self.set_lz(end_of_file)

    def clear_marks(self, mark_prefix: str) -> None:
        """Clear marks of type 'mark_prefix'.

        Used to unset marks with names such as "FootnoteCheckerShadow3.15".

        Arg:
            mark_prefix: Prefix of marks to be cleared.
        """
        for mark in maintext().mark_names():
            if mark.startswith(mark_prefix):
                maintext().mark_unset(mark)

    def add_blank_line_at_eof(self) -> None:
        """Add a blank line at end of the file.

        An issue arises if the last line of the file is a footnote.
        Inserting a newline character programatically will place it
        before the footnote's end Checker mark if tk.RIGHT gravity
        was specified when the mark was set. We want the newline
        placed after the mark otherwise the blank line will be
        carried with the footnote if it is moved.

        There will be at most one such Checker mark at the end of
        file in this case.

        Search for it. If present, remember its position at the current
        end of file then delete the mark. Insert a blank line after the
        current end of file then set the mark again at its old position.
        The mark will now be positioned before the blank line we just
        added.
        """
        mark_prefix = self.checker_dialog.get_mark_prefix()
        # Note the index of current end of file.
        old_end_of_file = maintext().end().index()
        blank_line_inserted = False
        # Initialise start position of mark search.
        mark_name = old_end_of_file
        while mark_name := maintext().mark_next(mark_name):  # type: ignore[assignment]
            if mark_name.startswith(mark_prefix):
                maintext().mark_unset(mark_name)
                maintext().insert(old_end_of_file, "\n")
                # Set mark again at its original position; i.e. the old end of file.
                maintext().set_mark_position(
                    mark_name,
                    maintext().rowcol(old_end_of_file),
                    gravity=tk.RIGHT,
                )
                blank_line_inserted = True
                break
        if not blank_line_inserted:
            # No Checker mark at end of file.
            maintext().insert(old_end_of_file, "\n")

    def change_gravity_right_to_left(self) -> None:
        """Change gravity of a FN-end Checker mark to tk.LEFT."""
        mark_prefix = self.checker_dialog.get_mark_prefix()
        fn_records = self.get_fn_records()
        for fn_record in fn_records:
            fn_cur_end = fn_record.end.index()
            mark_name = fn_cur_end
            while mark_name := maintext().mark_next(mark_name):  # type: ignore[assignment]
                if mark_name.startswith(mark_prefix):
                    save_name = mark_name
                    if maintext().compare(fn_cur_end, "==", save_name):
                        maintext().mark_unset(save_name)
                        maintext().set_mark_position(
                            save_name,
                            fn_record.end,
                            gravity=tk.LEFT,
                        )
                        break

    def change_gravity_left_to_right(self) -> None:
        """Change gravity of a FN-end Checker mark to tk.RIGHT."""
        mark_prefix = self.checker_dialog.get_mark_prefix()
        fn_records = self.get_fn_records()
        for fn_record in fn_records:
            fn_cur_end = fn_record.end.index()
            mark_name = fn_cur_end
            while mark_name := maintext().mark_next(mark_name):  # type: ignore[assignment]
                if mark_name.startswith(mark_prefix):
                    save_name = mark_name
                    if maintext().compare(fn_cur_end, "==", save_name):
                        maintext().mark_unset(save_name)
                        maintext().set_mark_position(
                            save_name,
                            fn_record.end,
                            gravity=tk.RIGHT,
                        )
                        break

    def move_footnotes_to_paragraphs(self) -> None:
        """Implements the 'Move FNs to Paragraphs' button.

        Moves footnotes to below the paragraph in which they are anchored. There
        are no 'FOOTNOTES:' headers.

        It is a three-pass process. In the first pass a named mark for each anchor
        record is set. The location of each mark is the insertion point to which
        the corresponding footnote will be moved. There may be many marks at the
        same location.

        The insertion point for marks is immediately BEFORE the line that separates
        the end of a paragraph from all that follows. That line is either a blank
        line that is not followed by a '[Footnote ...' line or is a Page Marker if
        that immediately precedes such a blank line. In this way care is taken to
        ensure insertion marks are located on the same page as the paragraph that
        they follow. Special action is required for blank lines that separate
        paragraphs in a multi-paragraph footnote.

        The second pass simply iterates through the footnote record array copying
        the text of each footnote before deleting the original and then inserting
        the unchanged footnote text at its named insertion mark from the first pass.

        The third pass iterates through footnote records in reverse order removing
        any blank lines left behind when footnotes are deleted.
        """
        maintext().undo_block_begin()
        # Check for any duplicate footnote labels and warn user that they should
        # reindex footnotes before attempting to move them to paragraphs or LZ(s).
        if not self.ok_to_move_fns():
            logger.error(
                "Duplicate labels - reindex footnotes before moving them to paragraphs"
            )
            return
        # Flip the gravity of all Checker marks at the end of each FN to tk.LEFT.
        # Can now reliably place insertion marks after the Checker mark so that
        # they maintain the required ordering. We'll flip the gravity back again
        # at the end of this function.
        self.change_gravity_right_to_left()
        an_records = self.get_an_records()
        # If the last line of the file is not a blank line then add one. If the
        # last line of the file is a footnote then because its end Checker mark
        # is currently tk.LEFT the newline will be correctly placed after the
        # Checker mark. Dont' have to call self.add_blank_line_at_eof() to do this
        file_end = maintext().end().index()
        if maintext().get(f"{file_end} linestart", f"{file_end} lineend") != "":
            maintext().insert(file_end, "\n")
        file_end = maintext().end().index()

        # First pass.

        # Set marks at the start of the blank line that separates a paragraph from the start
        # of the next paragraph. If that blank line is preceded by a Page Marker, set marks
        # at the start of the Page Marker. Those two locations are the same.

        mark_prefix = self.checker_dialog.get_mark_prefix() + INSERTION_MARK_PREFIX
        file_end = maintext().end().index()
        match_regex = r"^$"
        for an_record_index, an_record in enumerate(an_records):
            # Find the end of the paragraph in which the footnote anchor is located.
            # Start searching from the line containing the anchor.
            search_range = IndexRange(
                an_record.start, maintext().rowcol(f"{file_end} + 1l linestart")
            )
            while blank_line_match := maintext().find_match(
                match_regex, search_range, regexp=True
            ):
                # Found the first blank line after the paragraph.
                blank_line_start = blank_line_match.rowcol.index()
                # It might separate paragraph text from a footnote at the bottom of the page
                # with the paragraph continuing at the top of the next page.
                line_after_text = maintext().get(
                    f"{blank_line_start} +1l linestart",
                    f"{blank_line_start} +1l lineend",
                )
                if line_after_text[0:10] == "[Footnote ":
                    # Ignore this blank line and continue searching. However there is a problem
                    # if the footnote is a multi-paragraph one since there will be one or more
                    # blank lines within the footnote. We need to skip past these to the end of
                    # the footnote before restarting the 'paragraph separator' search.
                    #
                    # Find closing square bracket - allow open/close bracket within footnote.
                    # Start of line beginning '[Footnote ...'.
                    start = maintext().rowcol(f"{blank_line_start} +1l linestart")
                    # One character on from the above.
                    end_point = maintext().rowcol(f"{blank_line_start} +1l +1c")
                    end_match = None
                    nested = False
                    while True:
                        end_match = maintext().find_match(
                            "[][]", IndexRange(end_point, maintext().end()), regexp=True
                        )
                        if end_match is None:
                            break
                        end_point = maintext().rowcol(f"{end_match.rowcol.index()}+1c")
                        if maintext().get_match_text(end_match) == "]":
                            if not nested:
                                break  # Found the one we want
                            nested = False  # Closing nesting
                        else:
                            if nested:
                                end_match = (
                                    None  # Not attempting to handle double nesting
                                )
                                break
                            nested = True  # Opening nesting

                    # If closing [ not found, use end of line
                    if end_match is None:
                        end_point = maintext().rowcol(f"{start.row}.end")
                    # We have the end point of the footnote. Restart blank line search
                    # from there.
                    search_range = IndexRange(
                        end_point,
                        maintext().rowcol(f"{file_end} + 1l linestart"),
                    )
                    continue
                # If preceded by a Page Marker, position at the start of the Page Marker line.
                line_before_text = maintext().get(
                    f"{blank_line_start} -1l linestart",
                    f"{blank_line_start} -1l lineend",
                )
                if line_before_text[0:11] == "-----File: ":
                    blank_line_start = (
                        maintext().rowcol(f"{blank_line_start} -1l linestart").index()
                    )
                # 'blank_line_start' is now same location whether paragraph is followed by a blank
                # line or followed by a Page Marker then a blank line. Make sure mark point is on
                # the same page as the paragraph it follows. Note that we may end up with more than
                # one named mark at this location; that is, when there is more than one anchor
                # in the paragraph there will be more than one footnote following the paragraph.
                mark_point = maintext().rowcol(f"{blank_line_start} -1c")
                maintext().set_mark_position(
                    f"{mark_prefix}{an_record_index}",
                    mark_point,
                    gravity=tk.RIGHT,
                )
                break  # and repeat 'while' loop for the next anchor record

        # Second pass.

        # Iterate through the footnote record array copying the text of each footnote
        # before deleting the original and then inserting the unchanged footnote text
        # at its named insertion mark from the first pass.

        fn_records = self.get_fn_records()
        for fn_record_index, fn_record in enumerate(fn_records):
            fn_cur_start = self.checker_dialog.mark_from_rowcol(fn_record.start)
            fn_cur_end = self.checker_dialog.mark_from_rowcol(fn_record.end)
            fn_lines = maintext().get(fn_cur_start, fn_cur_end)
            mark_name = fn_cur_end
            fn_is_deleted = False
            # If the FN is followed on same line by an insertion mark, DON'T delete the
            # terminating newline. Insertion marks are placed before the newline so if
            # the latter is deleted then any footnotes moved to those insertion marks
            # will be located on the wrong page; i.e. they will end up at the start of
            # the following page.
            while mark_name := maintext().mark_next(mark_name):  # type: ignore[assignment]
                if mark_name.startswith(mark_prefix):
                    # On same line?
                    if maintext().compare(fn_cur_end, "==", mark_name):
                        maintext().delete(
                            f"{fn_cur_start} -1l linestart", f"{fn_cur_end}"
                        )
                        maintext().insert(
                            f"{mark_prefix}{fn_record_index}",
                            "\n\n" + fn_lines,
                        )
                        fn_is_deleted = True
                    break
            if not fn_is_deleted:
                # FN is not followed by an insertion mark. Delete it and its preceding
                # blank line so the space is closed up; e.g. in the case of a mid-paragrph
                # FN where the paragraph continues on the next page. There is a bug in GG1
                # which leaves a blank line mid-paragraph when a FN is deleted from that
                # location.
                maintext().delete(
                    f"{fn_cur_start} -1l linestart", f"{fn_cur_end} +1l linestart"
                )
                maintext().insert(f"{mark_prefix}{fn_record_index}", "\n\n" + fn_lines)
        # Third pass

        # Iterate through the footnote records in reverse order removing blank lines
        # left behind when FNs are deleted between the end of a paragraph and the
        # insertion mark after the last FN in a list of FNs that followed it.

        # Restore RIGHT gravity to the Checker mark at the end of each footnote.
        self.change_gravity_left_to_right()
        # Rebuild FN and AN record arrays.
        self.run_check()
        # Get FN records in reverse order. It is necessary that the 'for' loop below
        # works backwards from the last footnote to the first footnote so that the
        # location of each footnote that is processed is not affected by the deletion
        # of blank lines below it in the file.
        fn_records = self.get_fn_records()
        fn_records_reversed = fn_records.copy()
        fn_records_reversed.reverse()
        for fn_record in fn_records_reversed:
            fn_cur_start = fn_record.start.index()
            fn_cur_end = fn_record.end.index()
            fn_lines = maintext().get(fn_cur_start, fn_cur_end)
            # Replace each block of TWO blank lines above a relocated footnote by ONE
            # blank line until a single blank line remains.
            while True:
                text_to_match = maintext().get(
                    f"{fn_cur_start} -2l linestart", f"{fn_cur_start} linestart"
                )
                if re.match(r"^\s+?$", text_to_match):
                    maintext().delete(
                        f"{fn_cur_start} -1l linestart", f"{fn_cur_start} linestart"
                    )
                    fn_cur_start = f"{fn_cur_start} -1l linestart"
                else:
                    # No longer two or more blank lines above the footnote.
                    break

        # End of final pass over footnotes.

        # Footnotes have been moved. Rebuild anchor and footnote record arrays
        # to reflect the changes.
        self.run_check()
        # Set flag to disable buttons after dialog refreshed so that user
        # cannot execute a second FN move that might corrupt the file.
        self.fns_have_been_moved = True
        # Maintain the order of function calls below.
        display_footnote_entries()
        self.display_buttons()

    def move_footnotes_to_lz(self) -> None:
        """Implements the 'Move FNs to LZs' button.

        There will either be a single LZ 'FOOTNOTES:' header at the end of the
        file or multiple LZ 'FOOTNOTES:' headers, each one inserted before every
        4-line chapter break and an added one at end of file. This means that all
        footnotes have a LZ below them.
        """
        maintext().undo_block_begin()
        # Check for any duplicate footnote labels and warn user that they should
        # reindex footnotes before attempting to move them to paragraphs or LZ(s).
        if not self.ok_to_move_fns():
            logger.error(
                "Duplicate labels - reindex footnotes before moving them to LZ(s)"
            )
            return

        # Footnotes are always moved downward to a landing zone on a higher-numbered
        # line. Even if a footnote sits immediately below a landing zone, it will be
        # moved to the next one down in the file. There is always a 'next one down'
        # landing zone if 'end LZ' or 'chapter LZ' specified but may not be if 'set
        # LZ at cursor' used. We will add a LZ at file end if necessary as a 'catch
        # all' to cope with missing LZs.
        #
        # Start the moves from the last footnote in the file and work upward to the
        # first footnote. Each footnote moved is inserted immediately below the LZ
        # header so pushing down higher-numbered footnotes already moved.

        fn_records = self.get_fn_records()
        fn_records_reversed = fn_records.copy()
        fn_records_reversed.reverse()
        an_records = self.get_an_records()
        footnotes_header = "FOOTNOTES:"
        for fn_record in fn_records_reversed:
            fn_cur_start = self.checker_dialog.mark_from_rowcol(fn_record.start)
            fn_cur_end = self.checker_dialog.mark_from_rowcol(fn_record.end)
            fn_lines = maintext().get(fn_cur_start, fn_cur_end)
            # Get anchor record for this footnote.
            an_cur = an_records[fn_record.an_index]  # type: ignore[index]
            an_cur_end = maintext().index(
                self.checker_dialog.mark_from_rowcol(an_cur.end)
            )
            # Is there an LZ below the *anchor* of this footnote?
            #
            # NB This corrects a bug found by @sjfoo when chapter breaks occur mid-page
            #    rather than starting a new page. A footnote at the bottom of the page
            #    whose anchor is in the previous chapter is incorrectly moved to the LZ
            #    for the following chapter. Doing the search for the LZ from immediately
            #    after the anchor of each footnote avoids this pitfall.
            #
            # If no LZ, create one and move footnote below it. Otherwise move footnote
            # below the LZ that was found
            search_range = IndexRange(an_cur_end, maintext().end())
            if lz_match := maintext().find_match(footnotes_header, search_range):
                # There is a LZ below the anchor of the footnote.
                lz_start = lz_match.rowcol.index()
                below_lz = f"{lz_start} lineend"
                # Insert the copy of the footnote line(s) below the LZ.
                maintext().insert(below_lz, "\n\n" + fn_lines)
                # Delete the original footnote line(s) along with the
                # blank line above it.
                maintext().delete(
                    f"{fn_cur_start} -1l linestart", f"{fn_cur_end} +1l linestart"
                )
            else:
                # This branch should be entered 0 or 1 times only. Here
                # with a footnote anchor with no landing zone below.
                # There are two situations where this can happen:
                #  1. One or more LZs were added manually with 'set LZ
                #     at cursor' but were perhaps wrongly placed.
                #  2. No LZ was specified before clicking the move
                #     to landing zones button.
                #
                # Insert a LZ after the last line of the file
                # and move the footnote below it. Other footnotes
                # with a lower index may be inserted immediately
                # above it but that will be done in the 'then'
                # branch above because there is now a LZ below
                # those footnotes.
                self.autoset_end_lz()
                below_lz = maintext().end().index()
                # Insert the copy of the footnote line(s) below the new LZ.
                maintext().insert(below_lz, "\n" + fn_lines)
                # Delete the original footnote line(s) along with the blank
                # line above it.
                maintext().delete(
                    f"{fn_cur_start} -1l linestart", f"{fn_cur_end} +1l linestart"
                )
                # The last line of the file will be (the last line of) a
                # footnote. Add a blank line after it.
                self.add_blank_line_at_eof()
        # Remove unused LZ headers ('FOOTNOTES:'). Only needed when moving to
        # chapter end LZs but will be invoked for end LZ too. It does no harm
        # in this latter case and saves setting/testing flags to make it apply
        # only when moving FNs to chapter end LZs.
        self.remove_unused_lz_headers()
        # Ensure correct number of lines after 'FOOTNOTES:' header. If 4 blank
        # lines before, then add extra blank line after to make 2 blank lines.
        matches = maintext().find_matches(footnotes_header, maintext().start_to_end())
        for match in reversed(matches):
            match_idx = match.rowcol.index()
            if (
                maintext().get(f"{match_idx}-4l", match_idx) == "\n" * 4
                and maintext().get(f"{match_idx}+1l", f"{match_idx}+3l") != "\n" * 2
            ):
                maintext().insert(f"{match_idx}+1l", "\n")
        # Footnotes have been moved. Rebuild anchor and footnote record arrays
        # to reflect the changes.
        self.run_check()
        # Set flag to disable buttons when dialog refreshed so that user cannot
        # execute a second footnote move that might corrupt the file.
        self.fns_have_been_moved = True
        # Maintain order of function calls below.
        display_footnote_entries()
        self.display_buttons()

    def remove_unused_lz_headers(self) -> None:
        """Remove unused LZs and up to two preceding blank lines."""
        search_range = maintext().start_to_end()
        match_regex = r"FOOTNOTES:"
        # Loop, finding all LZ headers; i.e. the string "FOOTNOTES:".
        while True:
            beg_match = maintext().find_match(match_regex, search_range, regexp=True)
            if not beg_match:
                break
            indx = beg_match.rowcol.index()
            # A LZ header is unused if it's not followed by a blank line then a line beginning
            # with '[Footnote '.
            if (
                not maintext().get(f"{indx} +2l linestart", f"{indx} +2l lineend")[0:10]
                == "[Footnote "
            ):
                # A LZ header is always preceeded by one or two blank lines. Remove them and
                # the LZ header.
                bl_cnt = 0
                bl_cnt = (
                    1
                    if maintext().get(f"{indx} -1l linestart", f"{indx} -1l lineend")
                    == ""
                    else bl_cnt
                )
                bl_cnt = (
                    2
                    if maintext().get(f"{indx} -2l linestart", f"{indx} -2l lineend")
                    == ""
                    else bl_cnt
                )
                maintext().delete(
                    f"{indx} -{bl_cnt}l linestart", f"{indx} +1l linestart"
                )
            # Restart LZ search from next line.
            search_range = IndexRange(
                maintext().rowcol(f"{indx} +1l linestart"), maintext().end()
            )

    def ok_to_move_fns(self) -> bool:
        """Checks that file has been reindexed or has no duplicate FN labels."""
        labels_dict = {}
        fn_records = self.get_fn_records()
        for fn_record in fn_records:
            fn_cur_start = self.checker_dialog.mark_from_rowcol(fn_record.start)
            fn_cur_end = self.checker_dialog.mark_from_rowcol(fn_record.end)
            fn_label_and_text_part = maintext().get(
                f"{fn_cur_start} +10c", f"{fn_cur_end} -1c"
            )
            fn_label = re.sub(r"(?s)(^.+?):(.+$)", r"\1", fn_label_and_text_part)
            if fn_label in labels_dict:
                return False
            labels_dict[fn_label] = fn_label
        return True

    def tidy_footnotes(self) -> None:
        """Tidy footnotes after they have been moved.

        Copies and deletes the original footnote then reinserts edited
        version at the same place.
        """
        maintext().undo_block_begin()
        fn_records = self.get_fn_records()
        for fn_record in fn_records:
            fn_cur_start = self.checker_dialog.mark_from_rowcol(fn_record.start)
            fn_cur_end = self.checker_dialog.mark_from_rowcol(fn_record.end)
            fn_label_and_text_part = maintext().get(
                f"{fn_cur_start} +10c", f"{fn_cur_end} -1c"
            )
            fn_label_and_text_part = re.sub(r"(^.+?):", r"[\1]", fn_label_and_text_part)
            maintext().delete(fn_cur_start, fn_cur_end)
            maintext().insert(fn_cur_start, fn_label_and_text_part)
        # As there are no longer any '[Footnote ...' style records in the file
        # the effect of invoking run_check() here will be to clear the dialog
        # as there are no footnotes to report.
        self.run_check()
        # Maintain order of function calls below.
        display_footnote_entries()
        self.display_buttons()

    def get_anchor_hilite_columns_from_marks(self, an_record: AnchorRecord) -> tuple:
        """Get hilite start/end columns of an anchor from its start/end mark positions.

        The first thing the reindex() function does is set marks at the hilite start/end
        column positions of each anchor. When there is more than one anchor on a line
        the AN record hilite start/end column positions of a second or subsequent anchor
        on the same line cannot be relied on because the label of a previous anchor on
        that line might have expanded. Example:

        'B' -> 'AA' if the FN and its AN represented by label 'B' is the 53rd AN/FN
        and the anchor of the 54th FN is on the same line.

        On termination of the reindex() function these marks are unset.

        Returns:
            a tuple containing integer column hilite start/end positions of anchor.
        """
        an_cur_start = self.checker_dialog.mark_from_rowcol(an_record.start)
        an_cur_end = self.checker_dialog.mark_from_rowcol(an_record.end)
        hilite_start = re.sub(r"^.+?\.(.+$)", r"\1", an_cur_start)
        hilite_end = re.sub(r"^.+?\.(.+$)", r"\1", an_cur_end)
        return int(hilite_start), int(hilite_end)

    def alpha(self, label: int) -> str:
        """Converts given footnote number to alphabetic form:
        A,B,...Z,AA,AB,...AZ,BA,BB,...ZZ,AAA,AAB,...ZZZ
        Wraps back to AAA,AAB,etc., if >= 18728 (26^3+26^2+26)
        footnotes.

        Algorithm devised by @windymilla (Nigel Blower).

        Arg:
            label: an integer footnote or anchor label/index.

        Returns:
            # The corresponding alphabetic formhe label/index.
        """
        label -= 1
        _single = label % 26
        _double = int((label - 26) / 26) % 26
        _triple = int((label - 26 - 676) / 676) % 26
        #
        single_ch = chr(65 + _single)
        double_ch = (chr(65 + _double)) if label >= 26 else ""
        triple_ch = (chr(65 + _triple)) if label >= (676 + 26) else ""
        return triple_ch + double_ch + single_ch

    def set_anchor(self) -> None:
        """Insert anchor using label of selected footnote."""
        maintext().undo_block_begin()
        insert_index = maintext().get_insert_index().index()
        # Get label to use  for anchor from the selected FN.
        fn_index = self.get_selected_fn_index()
        if fn_index < 0:
            logger.error("No footnote selected")  # No selection
            return
        fn_records = self.get_fn_records()
        fn_record = fn_records[fn_index]
        fn_line_text = fn_record.text
        fn_label_start = 10
        fn_label_end = fn_line_text.find(":")
        fn_label = fn_line_text[fn_label_start:fn_label_end]
        maintext().insert(f"{insert_index}", f"[{fn_label}]")
        # Update AN and FN record arrays then refresh the dialog.
        self.run_check()
        display_footnote_entries(auto_select_line=False)
        # Make new anchor the selected entry in the dialog.
        fn_records = self.get_fn_records()
        # Get the footnote we previously selected.
        fn_record = fn_records[fn_index]
        # From that we can get the start index of the new anchor we created.
        an_records = self.get_an_records()
        an_record = an_records[fn_record.an_index]  # type: ignore[index]
        # Get index into dialog entries for this record.
        dialog_entry_index = self.map_an_record_to_dialog_index(an_record)
        if dialog_entry_index < 0:
            logger.error("Unexpected return from 'set_anchor()'")
            return
        # Make it the selected entry in the dialog.
        self.checker_dialog.select_entry_by_index(dialog_entry_index)

    def set_lz_at_cursor(self) -> None:
        """Insert FOOTNOTES: header at cursor."""
        maintext().undo_block_begin()
        insert_index = maintext().get_insert_index().index()
        insert_rowcol = maintext().get_insert_index()
        # Place LZ 'FOOTNOTES:' header at cursor; normally the header
        # is spaced two lines down from chapter end or end of book but
        # if user is manually inserting LZs it is assumed they will
        # add spacing as appropriate.
        maintext().insert(f"{insert_index}", "FOOTNOTES:")
        # The file has changed so rebuild AN/FN records and refresh dialog.
        self.run_check()
        # Order below is important. A flag is set in display_footnote_entries()
        # that will determine which, if any, buttons are disabled when they are
        # displayed.
        display_footnote_entries()
        self.display_buttons()
        # Reposition main text window to display the inserted header in the midle of the window.
        maintext().set_insert_index(insert_rowcol)

    def reindex(self) -> None:
        """Reindex all footnotes to fn_index_style.

        Three radio buttons set fn_index_style. It is persistent
        across runs so style chosen last time will be the current
        labelling style until a different radio button clicked.

        fn_index_style values are 'number', 'letter' or 'roman'.
        """
        maintext().undo_block_begin()
        # Check index style used last time Footnotes Fixup was run or default if first run.
        index_style = self.fn_index_style.get()
        an_records = self.get_an_records()
        fn_records = self.get_fn_records()
        for index in range(1, len(an_records) + 1):
            if index_style == "number":
                label = index
            elif index_style == "roman":
                label = roman.toRoman(index)
                label = label + "."  # type: ignore[operator]
            elif index_style == "letter":
                label = self.alpha(index)  # type: ignore[assignment]
            else:
                # Default
                label = index
            an_record = an_records[index - 1]
            an_line_text = maintext().get(
                f"{an_record.start.index()} linestart",
                f"{an_record.start.index()} lineend",
            )
            hl_start, hl_end = self.get_anchor_hilite_columns_from_marks(an_record)
            replacement_text = (
                f"{an_line_text[0:hl_start]}[{label}]{an_line_text[hl_end:]}"
            )
            an_start = f"{an_record.start.index()} linestart"
            maintext().delete(f"{an_start}", f"{an_start} lineend")
            maintext().insert(f"{an_start}", replacement_text)
            #
            fn_record = fn_records[index - 1]
            fn_line_text = fn_record.text
            hl_start = fn_record.hilite_start
            hl_end = fn_record.hilite_end
            fn_line_text = (
                f"{fn_line_text[0:hl_start + 9]}{label}{fn_line_text[hl_end:]}"
            )
            fn_start = f"{fn_record.start.index()} linestart"
            # Replace (first line of) footnote using new label value.
            maintext().delete(f"{fn_start}", f"{fn_start} lineend")
            maintext().insert(f"{fn_start}", fn_line_text)
        # AN/FN file entries have changed. Update AN/FN records.
        self.run_check()
        # Maintain the order of function calls below.
        display_footnote_entries()
        self.display_buttons()

    def next_footnote(self, direction: str) -> None:
        """Jump from the selected FN to next/previous FN.

        The next/previous FN becomes the selected FN.

        Args:
            direction: "back" to previous FN or "forward" to next FN.
        """
        cur_fn_index = self.get_selected_fn_index()
        if cur_fn_index < 0:
            logger.error("No footnote selected")  # No selection
            return
        fn_records = self.get_fn_records()
        if cur_fn_index == 0 and direction == "back":
            # Can't move behind first footnote.
            fn_record = fn_records[cur_fn_index]
        elif cur_fn_index == len(fn_records) - 1 and direction == "forward":
            # Can't move beyond last footnote.
            fn_record = fn_records[cur_fn_index]
        elif direction == "forward":
            fn_record = fn_records[cur_fn_index + 1]
        else:
            # direction must be "back".
            fn_record = fn_records[cur_fn_index - 1]
        # Get index into dialog entries for the footnote we've moved to.
        dialog_entry_index = self.map_fn_record_to_dialog_index(fn_record)
        if dialog_entry_index < 0:
            logger.error("Unexpected return from 'next_footnote()'")
            return
        # Make it the selected entry in the dialog.
        self.checker_dialog.select_entry_by_index(dialog_entry_index)

    def map_fn_record_to_dialog_index(self, fn_record: FootnoteRecord) -> int:
        """Get the footnote's index in the dialog entries.

        Args:
            fn_record: the footnote record whose index in the dialog entries we want.

        Returns:
            The index into dialog entries of the footnote, negative if not found.
        """
        fn_start = fn_record.start
        for entry_index, entry_record in enumerate(self.checker_dialog.entries):
            text_range = entry_record.text_range
            assert text_range is not None
            if fn_start == text_range.start:
                return entry_index
        return -1

    def map_an_record_to_dialog_index(self, an_record: AnchorRecord) -> int:
        """Get the anchor's index in the dialog entries.

        Args:
            an_record: the anchor record whose index in the dialog entries we want.

        Returns:
            The index into dialog entries of the anchor, negative if not found.
        """
        an_start = an_record.start
        for entry_index, entry_record in enumerate(self.checker_dialog.entries):
            text_range = entry_record.text_range
            assert text_range is not None
            if an_start == text_range.start:
                return entry_index
        return -1

    def get_selected_fn_index(self) -> int:
        """Get the index of the selected footnote.

        Returns:
            Index into self.fn_records array, negative if none selected.
        """
        cur_idx = self.checker_dialog.current_entry_index()
        if cur_idx is None:
            return -1
        text_range = self.checker_dialog.entries[cur_idx].text_range
        assert text_range is not None
        fn_start = text_range.start
        fn_records = self.get_fn_records()
        for fn_index, fn_record in enumerate(fn_records):
            if fn_record.start == fn_start:
                return fn_index
        return -1

    def display_buttons(self) -> None:
        """Helper function to create dialog window for run_check."""

        fixit_frame = ttk.Frame(self.checker_dialog.header_frame)
        fixit_frame.grid(column=0, row=1, sticky="NSEW")
        # Weight only needs setting once for each column, not every row of
        # every column.
        fixit_frame.grid_columnconfigure(0, weight=1)
        fixit_frame.grid_columnconfigure(1, weight=1)
        fixit_frame.grid_columnconfigure(2, weight=1)
        fixit_frame.grid_columnconfigure(3, weight=1)
        # --
        join_button = ttk.Button(
            fixit_frame,
            text="Join Selected FN to Previous",
            command=self.join_to_previous,
        )
        join_button.grid(column=0, row=0, pady=2, sticky="NSEW")
        # --
        prev_fn_button = ttk.Button(
            fixit_frame,
            text="<-- Prev. FN",
            command=lambda: self.next_footnote("back"),
        )
        prev_fn_button.grid(column=1, row=0, pady=2, sticky="NSEW")
        # --
        next_fn_button = ttk.Button(
            fixit_frame,
            text="Next FN -->",
            command=lambda: self.next_footnote("forward"),
        )
        next_fn_button.grid(column=2, row=0, pady=2, sticky="NSEW")
        # --
        anchor_button = ttk.Button(
            fixit_frame,
            text="Set Anchor for Selected FN",
            command=self.set_anchor,
        )
        anchor_button.grid(column=3, row=0, pady=2, sticky="NSEW")

        # Reindexing Footnotes

        all_to_num = ttk.Radiobutton(
            fixit_frame,
            text="All to Number",
            variable=self.fn_index_style,
            value=FootnoteIndexStyle.NUMBER,
            takefocus=False,
        )
        all_to_num.grid(column=0, row=1, pady=2, sticky="NSEW")
        # --
        all_to_let = ttk.Radiobutton(
            fixit_frame,
            text="All to Letter",
            variable=self.fn_index_style,
            value=FootnoteIndexStyle.LETTER,
            takefocus=False,
        )
        all_to_let.grid(column=1, row=1, pady=2, sticky="NSEW")
        # --
        all_to_rom = ttk.Radiobutton(
            fixit_frame,
            text="All to Roman",
            variable=self.fn_index_style,
            value=FootnoteIndexStyle.ROMAN,
            takefocus=False,
        )
        all_to_rom.grid(column=2, row=1, pady=2, sticky="NSEW")
        # --
        reindex_button = ttk.Button(
            fixit_frame,
            text="Reindex",
            command=self.reindex,
        )
        reindex_button.grid(column=3, row=1, pady=2, sticky="NSEW")

        # Setting LZs, moving FNs, tidying FNs.

        lz_set_at_cursor_button = ttk.Button(
            fixit_frame,
            text="Set LZ @ Cursor",
            command=self.set_lz_at_cursor,
        )
        lz_set_at_cursor_button.grid(column=0, row=2, pady=2, sticky="NSEW")
        # --
        lz_set_at_end_button = ttk.Button(
            fixit_frame,
            text="Autoset End LZ",
            command=self.autoset_end_lz,
        )
        lz_set_at_end_button.grid(column=1, row=2, pady=2, sticky="NSEW")
        # --
        lz_set_at_chapter_button = ttk.Button(
            fixit_frame,
            text="Autoset Chap. LZ",
            command=self.autoset_chapter_lz,
        )
        lz_set_at_chapter_button.grid(column=2, row=2, pady=2, sticky="NSEW")
        # --
        move_to_lz_button = ttk.Button(
            fixit_frame,
            text="Move FNs to Landing Zone(s)",
            command=self.move_footnotes_to_lz,
        )
        move_to_lz_button.grid(column=3, row=2, pady=2, sticky="NSEW")
        # --
        move_to_para_button = ttk.Button(
            fixit_frame,
            text="Move FNs to Paragraphs",
            command=self.move_footnotes_to_paragraphs,
        )
        move_to_para_button.grid(column=0, row=3, pady=2, sticky="NSEW")
        # --
        fn_tidy_button = ttk.Button(
            fixit_frame,
            text="Tidy Footnotes",
            command=self.tidy_footnotes,
        )
        fn_tidy_button.grid(column=3, row=3, pady=2, sticky="NSEW")

        # Some buttons are disabled if their operation could corrupt the file.

        # The following flag is set once and never unset.
        if self.fns_have_been_moved:
            lz_set_at_cursor_button.config(state="disable")
            lz_set_at_chapter_button.config(state="disable")
            lz_set_at_end_button.config(state="disable")
            move_to_lz_button.config(state="disable")
            move_to_para_button.config(state="disable")
            reindex_button.config(state="disable")
            join_button.config(state="disable")
            anchor_button.config(state="disable")
        # The following flag is set if FN check errors and unset if
        # there are no errors.
        if self.fn_check_errors:
            lz_set_at_cursor_button.config(state="disable")
            lz_set_at_chapter_button.config(state="disable")
            lz_set_at_end_button.config(state="disable")
            move_to_lz_button.config(state="disable")
            move_to_para_button.config(state="disable")
            reindex_button.config(state="disable")
            fn_tidy_button.config(state="disable")
        # Enable all buttons if neither of the 'disable' flags are True.
        if not (self.fn_check_errors or self.fns_have_been_moved):
            join_button.config(state="normal")
            anchor_button.config(state="normal")
            reindex_button.config(state="normal")
            lz_set_at_cursor_button.config(state="normal")
            lz_set_at_chapter_button.config(state="normal")
            lz_set_at_end_button.config(state="normal")
            move_to_lz_button.config(state="normal")
            move_to_para_button.config(state="normal")
            fn_tidy_button.config(state="normal")

    def run_check(self) -> None:
        """Run the initial footnote check."""
        self.reset()
        search_range = maintext().start_to_end()
        match_regex = r"\[ *footnote"
        # Loop, finding all footnotes (i.e. beginning with "[Footnote") allowing
        # some flexibility of spacing & case
        while beg_match := maintext().find_match(
            match_regex, search_range, regexp=True, nocase=True
        ):
            start = beg_match.rowcol
            # Find colon position, or use end of the word "Footnote"
            colon_match = maintext().find_match(
                ":", IndexRange(start, maintext().rowcol(f"{start.row}.end"))
            )
            if colon_match is None:
                colon_pos = maintext().rowcol(
                    f"{start.index()}+{beg_match.count + 1}c wordend"
                )
            else:
                colon_pos = colon_match.rowcol
            end_point = colon_pos
            # Find closing square bracket - allow open/close bracket within footnote
            end_match = None
            nested = False
            while True:
                end_match = maintext().find_match(
                    "[][]", IndexRange(end_point, maintext().end()), regexp=True
                )
                if end_match is None:
                    break
                end_point = maintext().rowcol(f"{end_match.rowcol.index()}+1c")
                if maintext().get_match_text(end_match) == "]":
                    if not nested:
                        break  # Found the one we want
                    nested = False  # Closing nesting
                else:
                    if nested:
                        end_match = None  # Not attempting to handle double nesting
                        break
                    nested = True  # Opening nesting

            # If closing [ not found, use end of line
            if end_match is None:
                end_point = maintext().rowcol(f"{start.row}.end")

            # Get label of footnote, e.g. "[Footnote 4:..." has label "4"
            fn_line = maintext().get(start.index(), f"{start.row}.end")
            fn_label = fn_line[
                beg_match.count + 1 : colon_pos.col - beg_match.rowcol.col
            ].strip()

            # Find previous occurrence of matching anchor, e.g. "[4]"
            # but not where used in context of block markup, e.g. "/#[4]"
            start_point = start
            while True:
                anchor_match = maintext().find_match(
                    f"[{fn_label}]",
                    IndexRange(start_point, maintext().start()),
                    backwards=True,
                )
                if anchor_match is None:
                    break
                anchor_context = maintext().get(
                    f"{anchor_match.rowcol.index()}-2c", anchor_match.rowcol.index()
                )
                if not re.fullmatch("/[#$*FILPXCR]", anchor_context):
                    break
                start_point = anchor_match.rowcol

            fn_index = len(self.fn_records)
            an_index = None if anchor_match is None else len(self.an_records)
            fnr = FootnoteRecord(
                fn_line, start, end_point, 1, colon_pos.col - start.col, an_index
            )
            self.fn_records.append(fnr)

            if anchor_match is not None:
                an_line = maintext().get(
                    f"{anchor_match.rowcol.row}.0", f"{anchor_match.rowcol.row}.end"
                )
                anr = AnchorRecord(
                    an_line,
                    anchor_match.rowcol,
                    maintext().rowcol(
                        f"{anchor_match.rowcol.index()}+{anchor_match.count}c",
                    ),
                    anchor_match.rowcol.col,
                    anchor_match.rowcol.col + anchor_match.count,
                    fn_index,
                )
                self.an_records.append(anr)

            search_range = IndexRange(end_point, maintext().end())


def sort_key_type(
    entry: CheckerEntry,
) -> tuple[int, int, int]:
    """Sort key function to sort Footnote entries by type (footnote/anchor), then rowcol.

    Types are distinguished lazily - anchors have a short highlit text, "[1]"
    """
    assert entry.text_range is not None
    assert entry.hilite_start is not None
    assert entry.hilite_end is not None
    return (
        entry.hilite_end - entry.hilite_start,
        entry.text_range.start.row,
        entry.text_range.start.col,
    )


def footnote_check() -> None:
    """Check footnotes in the currently loaded file."""
    global _the_footnote_checker

    if not tool_save():
        return

    checker_dialog = FootnoteCheckerDialog.show_dialog(
        "Footnote Check Results",
        rerun_command=footnote_check,
        sort_key_alpha=sort_key_type,
        show_suspects_only=True,
        tooltip="\n".join(
            [
                "Left click: Select & find footnote",
                "Right click: Remove item from list",
                "Shift-Right click: Remove all matching items",
            ]
        ),
    )

    if _the_footnote_checker is None:
        _the_footnote_checker = FootnoteChecker(checker_dialog)
    elif not _the_footnote_checker.checker_dialog.winfo_exists():
        _the_footnote_checker.checker_dialog = checker_dialog
    _the_footnote_checker.fns_have_been_moved = False

    _the_footnote_checker.run_check()
    # Order below is important. The display_footnote_entries() function will
    # set a flag if any dialog records have an error prefix. This flag is
    # checked in display_buttons() and will determine which, if any, buttons
    # are disabled when they are displayed.
    display_footnote_entries()
    _the_footnote_checker.display_buttons()


def check_fn_string(fn_record: FootnoteRecord) -> str:
    """Identify common footnote typos in a footnote record.

    NB Only looks at the start of (the first line of) the footnote.

    Args:
        footnote_record: record of a footnote in the file.

    Returns:
        str: 'OK' if FN conformant else an error description.
    """
    # GG1 fixes the next two typos but GG2 just flags them as for any other error.
    if fn_record.text[0:2] == "[ ":
        return "SPACE ERROR"
    if fn_record.text[0:2] == "[f":
        return "CASE ERROR"
    fn_start = fn_record.start
    if (
        fn_record.text[0:11] == "[Footnote: "
        and maintext().get(f"{fn_start.index()}-1c", fn_start.index()) != "*"
    ):
        return "MISSING LABEL"
    return "OK"


def display_footnote_entries(auto_select_line: bool = True) -> None:
    """(Re-)display the footnotes in the checker dialog."""
    assert _the_footnote_checker is not None
    checker_dialog = _the_footnote_checker.checker_dialog
    checker_dialog.reset()
    fn_records = _the_footnote_checker.get_fn_records()
    an_records = _the_footnote_checker.get_an_records()
    # Boolean to determine if buttons are to be set disabled or normal.
    _the_footnote_checker.fn_check_errors = False
    errors_flagged = False
    for fn_index, fn_record in enumerate(fn_records):
        # Flag common footnote typos. All non-conformant FNs
        # must be fixed before reindexing, etc., can be done.
        error_prefix = check_fn_string(fn_record)
        # Value of error_prefix is either 'OK' or an error description;
        # e.g. 'MISSING COLON', etc.
        if error_prefix == "OK":
            # Clear error_prefix string before looking for FN/AN context errors.
            error_prefix = ""
            # The footnote string is conformant now consider its context; does
            # it have an anchor; is it a continuation footnote; is it out of
            # sequence with its anchor, etc.
            if fn_record.an_index is None:
                fn_start = fn_record.start
                if maintext().get(f"{fn_start.index()}-1c", fn_start.index()) == "*":
                    error_prefix = "CONTINUATION: "
                else:
                    error_prefix = "NO ANCHOR: "
            else:
                an_record = an_records[fn_record.an_index]
                # Check that no other footnote has the same anchor as this one
                for fni2, fn2 in enumerate(fn_records):
                    if fn2.an_index is None:
                        continue
                    an2 = an_records[fn2.an_index]
                    if fn_index != fni2 and an_record.start == an2.start:
                        error_prefix = "SAME ANCHOR: "
                        break
                # Check anchor of previous footnote and this one are in order (footnotes are always in order)
                if (
                    fn_index > 0
                    and fn_record.an_index is not None
                    and fn_records[fn_index - 1].an_index is not None
                ):
                    an_prev = an_records[fn_records[fn_index - 1].an_index]  # type: ignore[index]
                    if an_prev.start.row > an_record.start.row or (
                        an_prev.start.row == an_record.start.row
                        and an_prev.start.col > an_record.start.col
                    ):
                        error_prefix += "SEQUENCE: "
                # Check anchor of next footnote and this one are in order (footnotes are always in order)
                if (
                    "SEQUENCE: " not in error_prefix
                    and fn_index < len(fn_records) - 1
                    and fn_record.an_index is not None
                    and fn_records[fn_index + 1].an_index is not None
                ):
                    an_next = an_records[fn_records[fn_index + 1].an_index]  # type: ignore[index]
                    if an_next.start.row < an_record.start.row or (
                        an_next.start.row == an_record.start.row
                        and an_next.start.col < an_record.start.col
                    ):
                        error_prefix += "SEQUENCE: "
        # If error_prefix string is not null then a context issue has been identified
        # above. The dialog entry will be prefixed with the highlighted error_prefix
        # such as "SAME ANCHOR", "NO ANCHOR" or "CONTINUATION". Note the last is not
        # an error as such but does need action by the PPer (join it to the previous
        # footnote) before reindexing and moving to paragraphs or LZ(s).
        if error_prefix != "":
            errors_flagged = True
        checker_dialog.add_entry(
            fn_record.text,
            IndexRange(fn_record.start, fn_record.end),
            fn_record.hilite_start,
            fn_record.hilite_end,
            error_prefix=error_prefix,
        )
    if errors_flagged:
        # Corrective action is needed by user before footnotes can be reindexed
        # and moved to their required landing zone. This flag is checked by the
        # display_buttons() function and determines which, if any, buttons are
        # disabled when displayed.
        _the_footnote_checker.fn_check_errors = True
    else:
        _the_footnote_checker.fn_check_errors = False
    for an_record in _the_footnote_checker.get_an_records():
        checker_dialog.add_entry(
            an_record.text,
            IndexRange(an_record.start, an_record.end),
            an_record.hilite_start,
            an_record.hilite_end,
        )
    checker_dialog.display_entries(auto_select_line)
    _the_footnote_checker.display_buttons()
