"""Functions to convert from text to HTML."""

from enum import StrEnum, auto
import importlib.resources
import logging
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import regex as re

from guiguts.data import html
from guiguts.file import the_file
from guiguts.maintext import maintext
from guiguts.page_details import PAGE_LABEL_PREFIX, PageDetail
from guiguts.preferences import (
    preferences,
    PrefKey,
    PersistentString,
    PersistentBoolean,
)
from guiguts.utilities import IndexRange, DiacriticRemover, IndexRowCol
from guiguts.widgets import ToplevelDialog, Busy

logger = logging.getLogger(__package__)

css_indents: set[int] = set()
book_title: Optional[str] = None
DEFAULT_HTML_DIR = importlib.resources.files(html)
HTML_HEADER_NAME = "html_header.txt"
PAGE_ID_PREFIX = "Page_"
H1_SKIP_REGEX = r"(\[Illustr|/[*$fxcr]|[*$fxcr]/)"

inline_conversion_dict = {
    PrefKey.HTML_ITALIC_MARKUP: ("i", "italic"),
    PrefKey.HTML_BOLD_MARKUP: ("b", "bold"),
    PrefKey.HTML_GESPERRT_MARKUP: ("g", "gesperrt"),
    PrefKey.HTML_FONT_MARKUP: ("f", "antiqua"),
    PrefKey.HTML_UNDERLINE_MARKUP: ("u", "u"),
}


class HTMLMarkupTypes(StrEnum):
    """Enum class to store values for markup conversion."""

    KEEP = auto()
    EM = auto()
    EM_CLASS = auto()
    SPAN_CLASS = auto()


class HTMLGeneratorDialog(ToplevelDialog):
    """Dialog for converting text file to HTML."""

    manual_page = "HTML_Menu#HTML_Generator"
    book_title = None

    def __init__(self) -> None:
        """Initialize HTML Generator dialog."""
        super().__init__("HTML Auto-generator", resize_x=False, resize_y=False)

        # Title
        title_frame = ttk.Frame(self.top_frame, padding=2)
        title_frame.grid(row=0, column=0, sticky="NSEW")
        title_frame.columnconfigure(1, weight=1)
        ttk.Label(title_frame, text="Title:").grid(row=0, column=0, padx=(0, 5))
        HTMLGeneratorDialog.book_title = tk.StringVar(self, get_title())
        self.title_entry = ttk.Entry(
            title_frame,
            textvariable=HTMLGeneratorDialog.book_title,
        )
        self.title_entry.grid(row=0, column=1, sticky="NSEW")
        self.title_entry.focus_set()

        # Whether to display page numbers
        self.display_page_numbers = ttk.Checkbutton(
            self.top_frame,
            text="Show Page Numbers in HTML",
            variable=PersistentBoolean(PrefKey.HTML_SHOW_PAGE_NUMBERS),
            takefocus=False,
        )
        self.display_page_numbers.grid(row=1, column=0, sticky="NSEW", pady=5)

        # Markup conversion
        markup_frame = ttk.LabelFrame(self.top_frame, text="Inline Markup", padding=2)
        markup_frame.grid(row=2, column=0, sticky="NSEW")
        for col, text in enumerate(
            ("Keep", "<em>", "<em class>", "<span class>"), start=1
        ):
            ttk.Label(markup_frame, text=text, anchor=tk.CENTER).grid(
                row=0, column=col, padx=5
            )
        for row, (key, (letter, _)) in enumerate(
            inline_conversion_dict.items(), start=1
        ):
            ttk.Label(markup_frame, text=f"<{letter}>:").grid(
                row=row, column=0, sticky="NSE", padx=(0, 5)
            )
            type_var = PersistentString(key)
            for col, value in enumerate(
                (
                    HTMLMarkupTypes.KEEP,
                    HTMLMarkupTypes.EM,
                    HTMLMarkupTypes.EM_CLASS,
                    HTMLMarkupTypes.SPAN_CLASS,
                ),
                start=1,
            ):
                ttk.Radiobutton(
                    markup_frame,
                    text="",
                    variable=type_var,
                    value=value,
                    takefocus=False,
                ).grid(row=row, column=col, padx=(7, 0))

        ttk.Button(
            self.top_frame,
            text="Auto-generate HTML",
            command=html_autogenerate,
            takefocus=False,
        ).grid(row=3, column=0, pady=2)


def html_autogenerate() -> None:
    """Autogenerate HTML from text file."""
    Busy.busy()
    backup_fn = re.sub(r"\.[^\.]*$", "-htmlbak.txt", the_file().filename)
    the_file().save_copy(backup_fn)
    try:
        do_html_autogenerate()
    except SyntaxError as exc:
        logger.error(exc)
        # Check if user would like to reload backup file
        reload = messagebox.askokcancel(
            title="Re-load backup file?",
            message="An error occurred during HTML autogeneration,\nand the file was not completely converted.",
            detail='Click "OK" to re-load pre-conversion backup file,\nor "Cancel" to explore partially converted file (see manual).',
            icon=messagebox.QUESTION,
        )
        if reload:
            # Save name because load_file will overwrite it
            old_filename = the_file().filename
            the_file().load_file(backup_fn)
            # Restore original filename
            the_file().filename = old_filename
            # Put it at the top of the recent files list
            the_file().store_recent_file(old_filename)
        # Go to error line number if available
        line_num, is_line_num = re.subn(r"Line (\d+):.*", r"\1", str(exc))
        if is_line_num:
            maintext().set_insert_index(IndexRowCol(int(line_num), 0))

    Busy.unbusy()


def do_html_autogenerate() -> None:
    """Do the work of HTML autogenerate."""
    css_indents.clear()
    maintext().undo_block_begin()
    remove_trailing_spaces()
    adjust_pagemark_postions()
    html_convert_entities()
    html_convert_title()
    html_convert_body()
    html_convert_inline()
    html_convert_smallcaps()
    html_convert_footnotes()
    html_convert_page_anchors()
    html_convert_footnote_landing_zones()
    html_convert_sidenotes()
    html_add_chapter_divs()
    html_wrap_long_lines()
    maintext().set_insert_index(maintext().start())


def remove_trailing_spaces() -> None:
    """Remove trailing spaces from every line."""
    maintext().replace_all(" +$", "", regexp=True)


def html_convert_entities() -> None:
    """Replace occurrences of ampersand, less & greater than, non-breaking space
    with HTML entities (except for things like `<i>`)."""
    maintext().replace_all("&", "&amp;")
    maintext().replace_all("\xa0", "&nbsp;")
    maintext().replace_all("--", "—")

    # Find all < and > characters
    search_range = maintext().start_to_end()
    while match := maintext().find_match("(<|>)", search_range, regexp=True):
        match_index = match.rowcol.index()
        match_text = maintext().get(match_index, f"{match_index} lineend")
        # Check if it's a DP tag, like `<i>` or `</sc>`
        check_match = re.match("</?(i|b|f|g|u|sc|tb)>", match_text)
        if check_match is None:
            maintext().replace(
                match_index,
                f"{match_index}+1c",
                "&lt;" if match_text[0] == "<" else "&gt;",
            )
            advance = 4
        else:
            advance = len(check_match[0])  # Skip whole of DP tag
        search_range = IndexRange(
            maintext().rowcol(f"{match_index}+{advance}c"), maintext().end()
        )


def html_convert_title() -> None:
    """Find first bit of text that may be the book title, and mark it up with h1."""
    in_title = False
    for step in range(1, 500):
        if maintext().compare(f"{step}.0", ">=", tk.END):
            break
        selection = maintext().get(f"{step}.0", f"{step}.end")
        if in_title:
            # Check if title complete (blank line or end of frontmatter/center/right/etc markup)
            if not selection or re.match("[*$fxcr]/", selection, flags=re.IGNORECASE):
                maintext().insert(f"{step - 1}.end", "</h1>")
                return
            # Not complete, keep going
            continue
        # Not in title yet, so skip blank lines, illos or block markup
        if not selection or re.match(H1_SKIP_REGEX, selection, flags=re.IGNORECASE):
            continue
        # Found start of title
        in_title = True
        maintext().insert(f"{step}.0", "<h1>")


def html_convert_inline() -> None:
    """Replace occurrences of <i>...</i> and other inline markup
    with em/span depending on settings."""
    for key, (letter, classname) in inline_conversion_dict.items():
        match preferences.get(key):
            case HTMLMarkupTypes.EM:
                maintext().replace_all(f"<{letter}>", "<em>")
                maintext().replace_all(f"</{letter}>", "</em>")
            case HTMLMarkupTypes.EM_CLASS:
                maintext().replace_all(f"<{letter}>", f'<em class="{classname}">')
                maintext().replace_all(f"</{letter}>", "</em>")
            case HTMLMarkupTypes.SPAN_CLASS:
                maintext().replace_all(f"<{letter}>", f'<span class="{classname}">')
                maintext().replace_all(f"</{letter}>", "</span>")


def html_convert_smallcaps() -> None:
    """Replace occurrences of <sc>...</sc> with HTML span.

    Classname is `smcap` unless string contains no lowercase when it is `allsmcap`
    """
    # Easier to work backward so replacements don't affect search for next occurrence
    search_index = "end"
    while smcap_end := maintext().search("</sc>", search_index, backwards=True):
        smcap_start = maintext().search("<sc>", smcap_end, backwards=True)
        if not smcap_start:
            return
        test_text = maintext().get(f"{smcap_start}+4c", smcap_end)
        classname = (
            "smcap" if re.search(r"\p{Lowercase_Letter}", test_text) else "allsmcap"
        )
        maintext().replace(smcap_end, f"{smcap_end}+5c", "</span>")
        maintext().replace(
            smcap_start, f"{smcap_start}+4c", f'<span class="{classname}">'
        )


def html_convert_body() -> None:
    """Convert body of text to HTML one line at a time."""
    next_step = 1
    contents_start = "1.0"
    in_chap_heading = False
    in_h1_heading = False
    chap_id = ""
    chap_heading = ""
    auto_toc = ""
    in_para = False
    in_front_para = False
    in_stanza = False
    pre_flag = False
    front_flag = False
    poetry_flag = False
    list_flag = False
    index_flag = False
    dollar_nowrap_flag = False
    asterisk_nowrap_flag = False
    center_nowrap_flag = False
    right_nowrap_flag = False
    right_block_line_num = 0
    poetry_indent = 0
    blockquote_level = 0
    right_line_lengths: list[int] = []
    ibs_dict = {
        "i": False,
        "b": False,
        "sc": False,
    }  # Flags to track open inline markup across lines

    def check_illegal_nesting() -> None:
        """Raise exception if already inside block markup."""
        if (
            pre_flag  # pylint: disable=too-many-boolean-expressions
            or front_flag
            or poetry_flag
            or list_flag
            or dollar_nowrap_flag
            or asterisk_nowrap_flag
            or center_nowrap_flag
            or right_nowrap_flag
            or index_flag
        ):
            raise SyntaxError(f"Line {step}: Illegally nested block markup")

    def reset_ibs_dict() -> None:
        """Set the italic/bold/smcap flags to False when starting a new block."""
        for key in ibs_dict:
            ibs_dict[key] = False

    markup_start = 1
    chap_start = 1
    while next_step <= maintext().end().row:
        step = next_step
        next_step += 1
        line_start = f"{step}.0"
        line_end = f"{step}.end"
        selection = maintext().get(line_start, line_end)
        n_spaces = len(selection) - len(selection.lstrip())

        # Remove trailing spaces first
        sel_strip = selection.rstrip()
        if n_trail := len(selection) - len(sel_strip):
            maintext().delete(f"{line_end}-{n_trail}c", line_end)
        selection = sel_strip
        selection_lower = selection.lower()

        # Note start of table of contents (English-only atm)
        if step < 100 and contents_start == "1.0" and "contents" in selection_lower:
            contents_start = f"{line_start}+2l"

        selection = html_convert_sub_super(selection, line_start, line_end)
        if html_convert_tb(selection, line_start, line_end):
            continue

        # Skip h1 heading - dealt with earlier
        if "<h1>" in selection_lower:
            in_h1_heading = True
        if in_h1_heading:
            if "</h1>" in selection_lower:
                in_h1_heading = False
            continue

        # "/x" is replaced with "<pre>", then leave lines unchanged until "/x"-->"</pre>"
        if selection_lower == "/x":  # open
            maintext().replace(line_start, line_end, "<pre>")
            pre_flag = True
            markup_start = step
            continue
        if pre_flag:
            if selection_lower == "x/":  # close
                maintext().replace(line_start, line_end, "</pre>")
                pre_flag = False
            continue

        # Remove leading spaces now "pre" is dealt with.
        # n_spaces stores the number of spaces if needed later.
        maintext().delete(line_start, f"{line_start}+{n_spaces}c")

        # "/f" --> center all paragraphs until we get "f/"
        if selection_lower == "/f":  # open
            maintext().delete(line_start, line_end)
            front_flag = True
            markup_start = step
            in_front_para = False
            continue
        if front_flag:
            if selection_lower == "f/":  # close
                if in_front_para:
                    maintext().insert(f"{line_start} -1l lineend", "</p>")
                maintext().delete(line_start, line_end)
                front_flag = False
            elif selection:
                if not in_front_para:  # start of paragraph
                    maintext().insert(line_start, '<p class="center">')
                    in_front_para = True
            elif in_front_para:  # blank line - end of paragraph
                maintext().insert(f"{line_start} -1l lineend", "</p>")
                in_front_para = False
            continue

        # "/p" --> poetry until we get "p/"
        if selection_lower == "/p":  # open
            maintext().replace(
                line_start,
                line_end,
                '<div class="poetry-container"><div class="poetry">',
            )
            poetry_flag = True
            markup_start = step
            in_stanza = False
            poetry_end = maintext().search(
                "^p/$", line_end, tk.END, regexp=True, nocase=True
            )
            if not poetry_end:
                raise SyntaxError(f"Line {step}: Unclosed poetry")
            poetry_indent = poetry_indentation(
                maintext().get(f"{line_start}+1l", poetry_end)
            )
            reset_ibs_dict()
            continue
        if poetry_flag:
            if selection_lower == "p/":  # close
                if in_stanza:
                    maintext().insert(f"{line_start} -1l lineend", "</div>")
                maintext().replace(line_start, line_end, "</div></div>")
                poetry_flag = False
            elif selection:
                # Handle line numbers (at least 2 spaces plus digits at end of line)
                # Remove line number prior to handling per-line bold/ital/smcap
                # then add marked up line number afterwards.
                replacement = ""
                if linenum_match := re.search(r" {2,}(\d+) *$", selection):
                    linenum_len = len(linenum_match[0])
                    replacement = f'<span class="linenum">{linenum_match[1]}</span>'
                    maintext().delete(f"{line_end}-{linenum_len}c", line_end)
                do_per_line_markup(selection, line_start, line_end, ibs_dict)
                # Now safe to add indent markup and line number markup
                indent = n_spaces - poetry_indent
                maintext().insert(
                    line_start,
                    f'<div class="verse indent{indent}">',
                )
                maintext().insert(line_end, replacement + "</div>")
                add_indent_to_css(indent)
                if not in_stanza:  # start of stanza
                    maintext().insert(line_start, '<div class="stanza">')
                    in_stanza = True
            elif in_stanza:  # blank line - end of stanza
                maintext().insert(f"{line_start} -1l lineend", "</div>")
                in_stanza = False
            continue

        # "/l" --> list until we get "l/"
        if selection_lower == "/l":  # open
            maintext().replace(
                line_start,
                line_end,
                "<ul>",
            )
            list_flag = True
            markup_start = step
            reset_ibs_dict()
            continue
        if list_flag:
            if selection_lower == "l/":  # close
                maintext().replace(line_start, line_end, "</ul>")
                list_flag = False
            elif selection:
                do_per_line_markup(selection, line_start, line_end, ibs_dict)
                # Now safe to add list markup
                maintext().insert(line_start, "<li>")
                maintext().insert(line_end, "</li>")
            continue

        # "/#" --> enter new level of blockquote until we get "#/"
        if selection.startswith("/#"):  # open
            blockquote_level += 1
            maintext().replace(
                line_start,
                line_end,
                '<div class="blockquot">',
            )
            markup_start = step
            continue
        if selection == "#/":  # close
            if blockquote_level > 0:
                blockquote_level -= 1
                maintext().replace(
                    line_start,
                    line_end,
                    "</div>",
                )
                if in_para:
                    maintext().insert(f"{line_start}-1l lineend", "</p>")
                    in_para = False
            else:
                raise SyntaxError(f"Line {step}: Unmatched close blockquote (#/)")
            continue

        # "/$" --> nowrap until we get "$/"
        if selection == "/$":  # open
            check_illegal_nesting()
            dollar_nowrap_flag = True
            markup_start = step
            maintext().replace(
                line_start,
                line_end,
                "<p>",
            )
            reset_ibs_dict()
            continue
        if dollar_nowrap_flag and selection == "$/":  # close
            dollar_nowrap_flag = False
            maintext().replace(
                line_start,
                line_end,
                "</p>",
            )
            continue
        # "/*" --> nowrap until we get "*/"
        if selection == "/*":  # open
            check_illegal_nesting()
            asterisk_nowrap_flag = True
            markup_start = step
            maintext().replace(
                line_start,
                line_end,
                "<p>",
            )
            reset_ibs_dict()
            continue
        if asterisk_nowrap_flag and selection == "*/":  # close
            asterisk_nowrap_flag = False
            maintext().replace(
                line_start,
                line_end,
                "</p>",
            )
            continue
        # lines within "/$" or "/*" markup
        if dollar_nowrap_flag or asterisk_nowrap_flag:
            do_per_line_markup(selection, line_start, line_end, ibs_dict)
            # Add 0.5em margin per space character
            if n_spaces > 0:
                maintext().insert(
                    line_start,
                    f'<span style="margin-left: {n_spaces * 0.5}em;">',
                )
                maintext().insert(line_end, "</span>")
            maintext().insert(line_end, "<br>")
            continue

        # "/i" --> index until we get "i/"
        if selection_lower.startswith("/i"):  # open
            check_illegal_nesting()
            index_flag = True
            markup_start = step
            maintext().replace(
                line_start,
                line_end,
                '<ul class="index">',
            )
            reset_ibs_dict()
            index_blank_lines = 2  # Force first entry to be start of section
            continue
        if index_flag:
            if selection_lower == "i/":  # close
                index_flag = False
                maintext().replace(
                    line_start,
                    line_end,
                    "</ul>",
                )
                continue
            if selection == "":
                index_blank_lines += 1
                continue
            if index_blank_lines >= 2:  # Section start
                classname = "ifrst"
            elif index_blank_lines == 1:  # Top-level entry
                classname = "indx"
            else:
                classname = f"isub{int((n_spaces + 1) / 2)}"
            # Convert numbers to page links where the formatting guidelines have been followed:
            # 1. Number must be preceded by a comma (optionally close quote) then one or more spaces
            # 2. Number must be no more than 3 digits (to avoid converting dates, like 1910)
            # 3. Page range may be specified by hyphen/ndash and second number following the first
            # 4. Number or range may be marked up italic or bold, e.g. <b>123</b> or <i>123-124</i>
            linked_selection = re.sub(
                r'(,["\'”’]?) +((<[ib]>)?\b(\d{1,3})([-–]\d{1,3})?\b(</[ib]>)?)',
                rf'\1 <a href="#{PAGE_ID_PREFIX}\4">\2</a>',
                selection,
            )
            maintext().replace(
                line_start, line_end, f'<li class="{classname}">{linked_selection}</li>'
            )
            index_blank_lines = 0
            continue

        # "/c" --> center until we get "c/"
        if selection_lower == "/c":  # open
            check_illegal_nesting()
            center_nowrap_flag = True
            markup_start = step
            maintext().replace(
                line_start,
                line_end,
                '<p class="center">',
            )
            reset_ibs_dict()
            continue
        if center_nowrap_flag:
            if selection_lower == "c/":  # close
                center_nowrap_flag = False
                maintext().replace(
                    line_start,
                    line_end,
                    "</p>",
                )
            else:  # lines within "/c" markup
                do_per_line_markup(selection, line_start, line_end, ibs_dict)
                maintext().insert(line_end, "<br>")
            continue

        # "/r" --> right-align block until we get "r/"
        if selection_lower == "/r":  # open
            check_illegal_nesting()
            right_nowrap_flag = True
            markup_start = step
            maintext().replace(
                line_start,
                line_end,
                '<p class="right">',
            )
            reset_ibs_dict()
            # Begin list of line lengths in order to add right-margin indents later
            right_line_lengths = []
            right_block_line_num = maintext().rowcol(line_start).row
            continue
        if right_nowrap_flag:
            if selection_lower == "r/":  # close
                right_nowrap_flag = False
                maintext().replace(
                    line_start,
                    line_end,
                    "</p>",
                )
                # Add the right-margin indents calculated from line lengths
                # Attempt to preserve shape of right margin (0.5em per character)
                max_len = max(right_line_lengths)
                for line_len in right_line_lengths:
                    right_block_line_num += 1
                    right_pad = max_len - line_len
                    if line_len > 0 and right_pad > 0:
                        maintext().insert(
                            f"{right_block_line_num}.0",
                            f'<span style="margin-left: {right_pad * 0.5}em;">',
                        )
                        maintext().insert(f"{right_block_line_num}.end", "</span>")
                    maintext().insert(f"{right_block_line_num}.end", "<br>")
            else:  # lines within "/r" markup
                # Store line length for later right-margin calculations
                # Convert entities back to single character to get length correct
                # Also sup/sub HTML markup don't count towards length
                len_str = re.sub("&[a-z]+?;", "X", selection)
                len_str = re.sub("</?su[pb]>", "", len_str)
                right_line_lengths.append(len(len_str))
                do_per_line_markup(selection, line_start, line_end, ibs_dict)
            continue

        # In chapter heading - store lines in heading until we get 2 blank lines
        if in_chap_heading:
            if selection:
                chap_line = re.sub(" *<h2.+?>", "", selection)
                chap_heading += (" " if chap_heading else "") + chap_line
                chap_head_blanks = 0
            elif chap_head_blanks == 0:  # First blank line
                # May be two part heading, e.g. "Chapter 1|<blank line>|The Start"
                maintext().insert(line_start, "<br>")
                chap_head_blanks += 1
            else:  # Second blank line = end of heading
                # First blank line will have had "<br>" inserted in elif above.
                # Remove that and add </h2> at end of chapter heading.
                maintext().replace(
                    f"{line_start}-2l lineend", f"{line_start}-1l lineend", "</h2>\n"
                )
                auto_toc += f'<a href="#{chap_id}">{chap_heading}</a><br>\n'
                in_chap_heading = False
            continue

        if selection:
            if not in_para:  # start of paragraph
                maintext().insert(line_start, "<p>")
                in_para = True
        elif in_para:  # blank line - end of paragraph
            maintext().insert(f"{line_start} -1l lineend", "</p>")
            in_para = False
        else:
            # blank line - not in paragraph, so might be chapter heading
            chap_check = maintext().get(f"{line_start}-3l", f"{line_start}+1l lineend")
            if re.match(r"\n\n\n\n[^\n]", chap_check):
                # Look ahead to see what's coming next
                # Don't want to treat h1, centered, poetry, etc as an h2 heading
                # even if it has 4 blank lines before it.
                chap_check = chap_check.lstrip("\n")
                if re.match(
                    r"<h1>|\[Illustration|\[Sidenote|\[Footnote|/[$*#cfilprx]",
                    chap_check,
                    flags=re.IGNORECASE,
                ):
                    continue
                chap_id = make_anchor(
                    maintext().get(f"{line_start}+1l", f"{line_start}+1l lineend")
                )
                maintext().insert(
                    f"{line_start}+1l linestart", f'<h2 class="nobreak" id="{chap_id}">'
                )
                in_chap_heading = True
                chap_start = step
                chap_head_blanks = 0
                chap_heading = ""
    # End of line-at-a-time loop

    # May hit end of file without a final blank line
    if in_para:
        maintext().insert(tk.END, "</p>")

    if pre_flag:
        raise SyntaxError(
            f"Line {markup_start}: Pre-formatted (/x) markup not closed by end of file"
        )
    if front_flag:
        raise SyntaxError(
            f"Line {markup_start}: Frontmatter (/f) markup not closed by end of file"
        )
    if poetry_flag:
        raise SyntaxError(
            f"Line {markup_start}: Poetry (/p) markup not closed by end of file"
        )
    if list_flag:
        raise SyntaxError(
            f"Line {markup_start}: List (/l) markup not closed by end of file"
        )
    if dollar_nowrap_flag:
        raise SyntaxError(
            f"Line {markup_start}: No rewrap (/$) markup not closed by end of file"
        )
    if asterisk_nowrap_flag:
        raise SyntaxError(
            f"Line {markup_start}: No rewrap (/*) markup not closed by end of file"
        )
    if index_flag:
        raise SyntaxError(
            f"Line {markup_start}: Index (/i) markup not closed by end of file"
        )
    if blockquote_level > 0:
        raise SyntaxError(
            f"Line {markup_start}: Blockquote (/#) not closed by end of file"
        )
    if in_chap_heading:
        raise SyntaxError(
            f"Line {chap_start}: Chapter heading does not have two blank lines before end of file"
        )

    # Add autogenerated ToC
    if auto_toc:
        maintext().insert(
            contents_start,
            "\n<!-- Autogenerated TOC. Modify or delete as required. -->\n"
            f"<p>\n{auto_toc}</p>\n"
            "<!-- End Autogenerated TOC. -->\n\n",
        )

    insert_header_footer()
    flush_css_indents()


def do_per_line_markup(
    selection: str,
    line_start: str,
    line_end: str,
    ibs_flags: dict[str, bool],
) -> None:
    """Add <i>, <b>, <sc> markup to this line if needed.

    Some constructions, e.g. poetry, use divs/spans for each line, which
    means that italic, bold or smcap cannot span line breaks, even if
    it was marked up that way at DP. Therefore need to stop/restart these
    inline markups on every line.

    Args:
        selection: Text of the line currently being worked on.
        line_start: Index of start of line.
        line_end: Index of end of line.
        ibs_flags: Global dictionary of flags keeping track of which markups are active.
    """
    if not selection:
        return
    for ch in ibs_flags.keys():
        # Add open at start if already in markup when we got to this line
        if ibs_flags[ch]:
            maintext().insert(line_start, f"<{ch}>")
        # Check status at end of line
        open_idx = selection.rfind(f"<{ch}>")
        close_idx = selection.rfind(f"</{ch}>")
        if open_idx > close_idx:
            ibs_flags[ch] = True  # Markup was left open
        if close_idx > open_idx:
            ibs_flags[ch] = False  # Markup was left closed
        # Add close at end of line if markup was left open
        if ibs_flags[ch]:
            maintext().insert(line_end, f"</{ch}>")


def end_of_css() -> str:
    """Find </style>, marking end of css.

    Returns:
        Index of beginning of line containing </style>
    """
    style_end = maintext().search("</style>", "1.0", regexp=False)
    if not style_end:
        raise SyntaxError("No '</style>' line found in default HTML header")
    return maintext().index(f"{style_end} linestart")


def add_indent_to_css(indent: int) -> None:
    """Add CSS for indent to CSS."""
    css_indents.add(indent)


def flush_css_indents() -> None:
    """Output saved indents to CSS section of file."""
    if not css_indents:
        return

    css_strings: list[str] = ["\n/* Poetry indents */\n"]
    for indent in sorted(css_indents):
        # Default verse with no extra spaces has 3em padding, and -3em text-indent to
        # give hanging indent in case of a continuation line.
        # Every two spaces causes +1em indent, but continuation lines need to align at 3em,
        # so, add the half the space indent to -3em to set the em text-indent for each line
        # For example, if 4 space indent, use 4 * 0.5 - 3 = -1em text-indent, i.e.
        #    .poetry .indent4 {text-indent: -1em;}
        css_strings.append(
            f".poetry .indent{indent} {{text-indent: {indent * 0.5 - 3}em;}}\n"
        )
    css_strings.append("\n")
    maintext().insert(end_of_css(), "".join(css_strings))


def poetry_indentation(poem: str) -> int:
    """Return how much whole poem is already indented by."""
    min_indent = 1000
    for line in poem.splitlines():
        if strip_line := line.lstrip():
            min_indent = min(min_indent, len(line) - len(strip_line))
            if min_indent == 0:
                break
    return min_indent


def html_convert_sub_super(selection: str, line_start: str, line_end: str) -> str:
    """Convert all sub/superscripts in given line & return
    the modified line for later processing.

    Supports `_{subscript}`, `^{superscript}`, and `^x` (single char superscript).

    Args:
        selection: The text of one line.
        line_start: Index of start of line.
        line_end: Index of end of line.

    Returns:
        The modified text of the line.
    """
    selection, nsubs = re.subn(r"_\{(.+?)\}", r"<sub>\1</sub>", selection)
    selection, nsups1 = re.subn(r"\^{(.+?)\}", r"<sup>\1</sup>", selection)
    selection, nsups2 = re.subn(r"\^(.)", r"<sup>\1</sup>", selection)
    if nsubs or nsups1 or nsups2:
        maintext().replace(line_start, line_end, selection)
    return selection


def html_convert_tb(selection: str, line_start: str, line_end: str) -> int:
    """Convert asterisk or <tb> thoughtbreak markup to <hr>.

    Args:
        selection: The text of one line.
        line_start: Index of start of line.
        line_end: Index of end of line.

    Returns:
        True if line was a thoughtbreak.
    """
    selection, nsubs = re.subn(r"^(       \*){5}$", '<hr class="tb">', selection)
    if nsubs:
        maintext().replace(line_start, line_end, selection)
    else:
        selection, nsubs = re.subn("<tb>", '<hr class="tb">', selection)
        if nsubs:
            maintext().replace(line_start, line_end, selection)
    return nsubs


def insert_header_footer() -> None:
    """Insert the default and/or user HTML header, and the footer.

    User's header can either be a complete header, or just some CSS
    to be inserted at the end of the default CSS.
    """
    # Temporarily set the gravity on page markers at the start
    # of the file to RIGHT to ensure the header gets added above them.
    # Need to create list of marks first before changing any gravities,
    # because if pages 1 & 2 are both at the start, then changing the
    # gravity of page 1 to RIGHT means that page 2 is no longer the
    # "next" mark after page 1 (because page 1 is to the right of page 2).
    # Then page 2 would be skipped and remain with LEFT gravity.
    page_mark_list = []
    page_mark = "1.0"
    while page_mark := maintext().page_mark_next(page_mark):
        if maintext().compare(page_mark, ">", "1.0"):
            break
        page_mark_list.append(page_mark)
    for page_mark in page_mark_list:
        maintext().mark_gravity(page_mark, tk.RIGHT)

    # Get user's header file if there is one
    user_path = Path(preferences.prefsdir, HTML_HEADER_NAME)
    if user_path.is_file():
        user_header = replace_header_keywords(user_path.read_text(encoding="utf-8"))
    else:
        user_header = ""
    # If user has provided complete header, insert at start instead of default
    if user_header.startswith("<!DOCTYPE"):
        maintext().insert("1.0", f"{user_header}\n")
    else:
        # Insert default header at start
        default_path = DEFAULT_HTML_DIR.joinpath(HTML_HEADER_NAME)
        default_header = replace_header_keywords(
            default_path.read_text(encoding="utf-8")
        )
        maintext().insert("1.0", f"{default_header}\n")
        # Insert user header if there is one, just before closing "</style>"
        if user_header:
            maintext().insert(end_of_css(), f"{user_header}\n")
    # Insert footer
    maintext().insert(tk.END, "\n</body>\n</html>\n")
    # Restore mark gravity if changed above
    for page_mark in page_mark_list:
        maintext().mark_gravity(page_mark, tk.LEFT)


def replace_header_keywords(header: str) -> str:
    """Replace TITLE and BOOKLANG in the given header to
    their values.

    Args:
        header: Header potentially containing keywords.

    Returns:
        Header with keywords replaced.
    """
    assert HTMLGeneratorDialog.book_title is not None
    header = header.replace("TITLE", HTMLGeneratorDialog.book_title.get())
    header = header.replace("BOOKLANG", maintext().get_language_list()[0])
    return header


def make_anchor(string: str) -> str:
    """Make a valid HTML id from string which may contain HTML markup,
    accented characters, punctuation, etc.

    Args:
        string - String to be converted to HTML id.

    Returns:
        Valid HTML id.
    """
    # Remove any accents
    string = DiacriticRemover.remove_diacritics(string)
    # Clean up dashes and HTML entities
    string = re.sub(r"\p{Dash_Punctuation}+", "-", string)
    string = re.sub("&[a-z]+;", "_", string)
    # Remove super/subscripted text as well as other HTML markup
    string = re.sub("<su[bp]>.+?</subp>", "", string)
    string = re.sub(r"</?[\p{Letter}\p{Number}]+?>", "", string)
    # Remove anything else undesirable
    string = re.sub(r"[^-_\p{Letter}\p{Number}\p{Separator}]", "", string)
    # Replace spaces, etc. with _
    string = re.sub(r"\p{Separator}+", "_", string)
    # Replace multiple underscores with single
    string = re.sub(r"__+", "_", string)
    return string


def get_title() -> str:
    """Determine the book title from the text, if possible.

    Returns:
        Best guess at book title.
    """
    complete_title = ""
    in_title = False
    # Only search first 500 lines of book
    for step in range(1, 500):
        if maintext().compare(f"{step}.0", ">=", tk.END):
            break
        selection = maintext().get(f"{step}.0", f"{step}.end")
        # Check if title complete (blank line or end of frontmatter markup)
        if in_title and (
            not selection or re.match("f/", selection, flags=re.IGNORECASE)
        ):
            break
        # Skip blank lines, illos or block markup
        if not selection or re.match(H1_SKIP_REGEX, selection, flags=re.IGNORECASE):
            continue
        # Strip inline markup, compress multiple spaces & trim trailing spaces
        selection = re.sub(r"<.+?>", "", selection)
        selection = re.sub(r"  +", " ", selection).strip()
        complete_title = f"{complete_title}{selection} "
        in_title = True

    # If no lowercase letters, title is ALL CAPS, so make all but first char lowercase
    # (may not be correct, but best we can do).
    if not re.search(r"\p{Lowercase_Letter}", complete_title):
        complete_title = complete_title.capitalize()
    # Don't want trailing space, comma or period
    return complete_title.rstrip(" ,.")


def adjust_pagemark_postions() -> None:
    """Move page marks to start of next line if at end of non-empty line.

    In particular, we don't want want page mark at the end of a paragraph,
    and hence getting trapped before closing `</p>` markup.
    """

    def mark_list_at_index(mark: str, index: str) -> list[str]:
        """Return a list of the page marks after given mark that are at the given index."""
        mark_list: list[str] = []
        while mark := maintext().page_mark_next(mark):
            if maintext().compare(mark, ">", index):
                break
            mark_list.append(mark)
        return mark_list

    mark = "1.0"
    while mark := maintext().page_mark_next(mark):
        eol = maintext().compare(mark, "==", f"{mark} lineend")
        nonempty = maintext().get(f"{mark} linestart", f"{mark} lineend")
        if eol and nonempty:
            move_mark_list = mark_list_at_index(mark, mark)
            move_mark_list.insert(0, mark)
            next_linestart = maintext().index(f"{mark}+1l linestart")
            # Need to change gravity of this mark and all marks at
            # start of next line, to preserve mark order
            next_mark_list = mark_list_at_index(mark, next_linestart)
            for next_line_mark in next_mark_list:
                maintext().mark_gravity(next_line_mark, tk.RIGHT)
            for move_mark in reversed(move_mark_list):
                maintext().mark_set(move_mark, next_linestart)
            # Restore gravities
            for next_line_mark in next_mark_list:
                maintext().mark_gravity(next_line_mark, tk.LEFT)
            mark = move_mark_list[-1]


def html_convert_page_anchors() -> None:
    """Add page anchors, comments, etc., at page marks depending on dialog settings."""
    page_details = the_file().page_details
    the_file().update_page_marks(page_details)

    # We want page labels sorted by png even if their order in the file is wrong
    # Work in reverse so inserted text doesn't affect later indexes
    page_detail_buffer: list[PageDetail] = []
    last_mark_index = maintext().index("end")

    def lbl_to_pgnum(label: str) -> str:
        """Convert page label to page number by removing prefix."""
        return label.removeprefix(PAGE_LABEL_PREFIX)

    def safe_index(pnum_index: str) -> str:
        """Given an index where a pagenum span is to be inserted,
        move it if necessary, so it won't occur mid-word.

        Args:
            pnum_index: Index where pagenum may be inserted.

        Returns:
            Safe index, which is after pnum_index by as far is necessary
            to avoid mid-word page spans.
        """
        pnum_rowcol = IndexRowCol(pnum_index)
        if pnum_rowcol.col > 0:  # Not at beginning of line
            # Check from previous char to end of line - may be a space before the index point
            check_str = maintext().get(f"{pnum_index} -1c", f"{pnum_index} lineend")
            if len(check_str) > 1:  # Not at end of line
                # Truncate from first space or HTML markup opening
                move = len(re.sub(r"[\s<].*", "", check_str)) - 1
                if move > 0:
                    pnum_index = maintext().index(f"{pnum_index}+{move}c")
        return pnum_index

    def outside_paragraph(index: str) -> bool:
        """Return True if given index is not within `<p>` markup."""
        prev_markup = maintext().find_match(
            r"(<p[> ]|</p>)", IndexRange(index, "1.0"), regexp=True, backwards=True
        )
        return prev_markup is None or maintext().get_match_text(prev_markup) == "</p>"

    def flush_page_detail_buffer() -> None:
        """Output contents of buffer in pgnum span."""
        pgnum = lbl_to_pgnum(page_detail_buffer[0]["label"])
        pstring = f"[{PAGE_LABEL_PREFIX}{pgnum}]" if show_page_numbers else ""
        if len(page_detail_buffer) == 1:
            pagenum_span = (
                f'<span class="pagenum" id="{PAGE_ID_PREFIX}{pgnum}">{pstring}</span>'
            )
        else:
            anchors = f'"></a><a id="{PAGE_ID_PREFIX}'.join(
                [lbl_to_pgnum(pd["label"]) for pd in reversed(page_detail_buffer)]
            )
            anchors = f'<a id="{PAGE_ID_PREFIX}{anchors}"></a>'
            pagenum_span = f'<span class="pagenum">{anchors}{pstring}</span>'
        insert_index = safe_index(page_detail_buffer[0]["index"])
        if outside_paragraph(insert_index):
            pagenum_span = f"<p>{pagenum_span}</p>"
        maintext().insert(insert_index, pagenum_span)

    show_page_numbers = preferences.get(PrefKey.HTML_SHOW_PAGE_NUMBERS)
    for _, page_detail in sorted(page_details.items(), reverse=True):
        label = lbl_to_pgnum(page_detail["label"])
        if not label:
            continue
        # Check if this page mark is effectively at different place to last,
        # i.e. if there are non-space characters between them.
        # If it's a new location for page marks - flush the buffer, writing
        # one pagenum span, with an anchor for each page break if multiple coincident breaks.
        text_between = maintext().get(page_detail["index"], last_mark_index)
        last_mark_index = page_detail["index"]
        if page_detail_buffer and re.search(r"\S", text_between):
            flush_page_detail_buffer()
            page_detail_buffer = []
        page_detail_buffer.append(page_detail)
    if page_detail_buffer:
        flush_page_detail_buffer()


def html_convert_sidenotes() -> None:
    """Convert sidenote markup to HTML divs.

    If multi-paragraph, retain the `<p>` markup, otherwise discard.
    """
    sidenote_end = "1.0"
    while sidenote_start := maintext().search("<p>[Sidenote: ", sidenote_end, tk.END):
        sidenote_end = maintext().search("]</p>", sidenote_start, tk.END)
        if not sidenote_end:
            raise SyntaxError(
                f"Unclosed sidenote:\n{maintext().get(f'{sidenote_start}+3c', f'{sidenote_start} lineend')}"
            )
        sidenote_text = maintext().get(sidenote_start, sidenote_end)
        p_open = p_close = ""
        if "\n\n" in sidenote_text:
            p_open = "<p>"
            p_close = "</p>"
        maintext().replace(
            sidenote_start, f"{sidenote_start}+14c", f'<div class="sidenote">{p_open}'
        )
        maintext().replace(sidenote_end, f"{sidenote_end}+5c", f"{p_close}</div>")


def html_convert_footnotes() -> None:
    """Convert `[Footnote` markup to HTML.

    For each footnote, search backward to find another identically numbered one.
    Then between those two, search for suitable anchors. Link from all anchors
    to the footnote, and from the footnote to the first anchor.
    """
    fn_end = "1.0"
    fn_number = 0
    # Find start of each footnote
    while fn_start := maintext().search(
        r"<p>\[Footnote .+?:", fn_end, tk.END, regexp=True
    ):
        # Find end of footnote
        fn_end = maintext().search(r"]</p>$", fn_start, tk.END, regexp=True)
        if not fn_end:
            raise SyntaxError(
                f"No paragraph break at footnote end:\n{maintext().get(f'{fn_start}+3c', f'{fn_start} lineend')}"
            )
        # Extract label for footnote
        fn_label_end = maintext().search(":", fn_start, tk.END)
        fn_label = maintext().get(f"{fn_start}+13c", fn_label_end)
        fn_number += 1
        fn_id = f"Footnote_{fn_label}_{fn_number}"
        an_id = f"FNanchor_{fn_label}_{fn_number}"
        # Add markup for footnote
        maintext().replace(fn_end, f"{fn_end} lineend", "</p></div>")
        maintext().replace(
            fn_start,
            f"{fn_label_end}+1c",
            f'<div class="footnote"><p><a id="{fn_id}" href="#{an_id}" class="label">[{fn_label}]</a>',
        )

        # Search backwards for another footnote with the same label (or start of file)
        an_search_start = maintext().search(
            f'class="label">[{fn_label}]</a>', fn_start, "1.0", backwards=True
        )
        if an_search_start:
            an_search_start += (
                "+1l"  # Don't want to mistake label of this footnote for an anchor
            )
        else:
            an_search_start = "1.0"
        # For all matching anchors in that range, add HTML markup to anchor, with id on the first only
        id_markup = f'id="{an_id}" '
        while an_start := maintext().search(f"[{fn_label}]", an_search_start, fn_start):
            an_end = f"{an_start}+{len(fn_label) + 2}c"
            maintext().insert(an_end, "</a>")
            open_markup = f'<a {id_markup}href="#{fn_id}" class="fnanchor">'
            maintext().insert(an_start, open_markup)
            id_markup = ""
            an_search_start = maintext().index(f"{an_start}+{len(open_markup) + 4}c")


def html_convert_footnote_landing_zones() -> None:
    """Add HTML markup around Landing Zones beginning with "FOOTNOTES"."""
    lz_end = "1.0"
    while lz_start := maintext().search("<p>FOOTNOTES:</p>", lz_end, tk.END):
        # Find next LZ (or end of file) to help find the end of this LZ
        lz_next = maintext().search("<p>FOOTNOTES:</p>", f"{lz_start}+17c", tk.END)
        if not lz_next:
            lz_next = tk.END
        # Find last footnote in this LZ by searching backwards from next LZ
        lz_end = maintext().search(
            '<div class="footnote">', lz_next, lz_start, backwards=True
        )
        if lz_end:
            lz_end = maintext().search(
                "</div>", lz_end, lz_next
            )  # End of last footnote
        else:
            # No FN in LZ
            lz_end = f"{lz_start}"
        lz_end += " lineend"
        maintext().insert(lz_end, "</div>")
        # <p>FOOTNOTES:</p> ==> <div class="footnotes"><h3>FOOTNOTES:</h3>
        maintext().replace(f"{lz_start}+15c", f"{lz_start}+16c", "h3")
        maintext().replace(lz_start, f"{lz_start}+3c", '<div class="footnotes"><h3>')


def html_add_chapter_divs() -> None:
    """Add chapter divs where there are h2 headings, attempting to
    enclose pagenum spans where appropriate."""
    h2_end = "1.0"
    while h2_start := maintext().search("<h2", h2_end, tk.END):
        h2_end = f'{maintext().search("</h2>", h2_start, tk.END)} lineend'
        prev_page_break = maintext().search(
            '<span class="pagenum"', h2_start, "1.0", backwards=True
        )
        if prev_page_break:
            check_text = maintext().get(f"{prev_page_break} linestart", h2_start)
            check_text = re.sub(r'<span class="pagenum".+?</span>', "", check_text)
            check_text = re.sub(r"</?p>", "", check_text)
            check_text = re.sub(r"\[Illustration.*?\]", "", check_text)
            check_text = check_text.replace("\n", "")
            if not check_text:
                h2_start = f"{prev_page_break} linestart"
        maintext().insert(
            h2_start, '<hr class="chap x-ebookmaker-drop"><div class="chapter">'
        )
        maintext().insert(h2_end, "</div>")


def html_wrap_long_lines() -> None:
    """Wrap lines that are longer than 200 characters, typically where an index line
    has many linked page numbers."""
    wrap_limit = 200
    index = "1.0"
    # Find lines longer than 200 characters
    while index := maintext().search(
        rf"^[^\n]{{{wrap_limit},}}", f"{index} lineend", tk.END, regexp=True
    ):
        # Find previous close tag with a space after (space is possibly preceded by other text, but not an open tag)
        count = tk.IntVar()
        if insert_point := maintext().search(
            "</[a-zA-Z0-9]+?>[^ <]* ",
            f"{index}+{wrap_limit}c",
            index,
            regexp=True,
            backwards=True,
            count=count,
        ):
            # Insert a newline and three extra spaces before the space that was found
            maintext().insert(f"{insert_point}+{count.get() - 1}c", "\n   ")
