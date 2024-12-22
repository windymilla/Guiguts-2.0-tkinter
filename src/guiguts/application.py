#!/usr/bin/env python
"""Guiguts - an application to support creation of ebooks for PG"""


import argparse
import logging
import importlib.resources
from importlib.metadata import version
import os.path
from tkinter import messagebox
from typing import Optional
import unicodedata
import webbrowser

from guiguts.data import themes
from guiguts.file import File, the_file, NUM_RECENT_FILES
from guiguts.footnotes import footnote_check, FootnoteIndexStyle
from guiguts.html_convert import HTMLGeneratorDialog, HTMLMarkupTypes
from guiguts.illo_sn_fixup import illosn_check
from guiguts.maintext import maintext
from guiguts.mainwindow import (
    MainWindow,
    Menu,
    menubar,
    StatusBar,
    statusbar,
    ErrorHandler,
    process_accel,
)
from guiguts.misc_dialogs import (
    PreferencesDialog,
    ComposeSequenceDialog,
    ComposeHelpDialog,
    UnicodeBlockDialog,
    UnicodeSearchDialog,
)
from guiguts.misc_tools import (
    basic_fixup_check,
    page_separator_fixup,
    PageSepAutoType,
    unmatched_dp_markup,
    unmatched_brackets,
    unmatched_curly_quotes,
    unmatched_block_markup,
    FractionConvertType,
    fraction_convert,
    unicode_normalize,
    proofer_comment_check,
    asterisk_check,
    TextMarkupConvertorDialog,
    stealth_scannos,
    DEFAULT_SCANNOS_DIR,
    DEFAULT_REGEX_SCANNOS,
    DEFAULT_STEALTH_SCANNOS,
    convert_to_curly_quotes,
    check_curly_quotes,
)
from guiguts.page_details import PageDetailsDialog
from guiguts.preferences import preferences, PrefKey
from guiguts.root import root
from guiguts.search import show_search_dialog, find_next
from guiguts.spell import spell_check
from guiguts.tools.bookloupe import bookloupe_check
from guiguts.tools.jeebies import jeebies_check, JeebiesParanoiaLevel
from guiguts.tools.levenshtein import levenshtein_check, LevenshteinEditDistance
from guiguts.tools.pptxt import pptxt
from guiguts.utilities import is_mac, folder_dir_str
from guiguts.widgets import themed_style, theme_name_internal_from_user
from guiguts.word_frequency import word_frequency, WFDisplayType, WFSortType

logger = logging.getLogger(__package__)

MESSAGE_FORMAT = "%(asctime)s: %(levelname)s - %(message)s"
DEBUG_FORMAT = "%(asctime)s: %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
THEMES_DIR = importlib.resources.files(themes)


class Guiguts:
    """Top level Guiguts application."""

    def __init__(self) -> None:
        """Initialize Guiguts class.

        Creates windows and sets default preferences."""

        self.parse_args()

        self.logging_init()
        logger.info("Guiguts started")

        self.initialize_preferences()

        self.file = File(self.filename_changed, self.languages_changed)
        the_file(self.file)

        self.mainwindow = MainWindow()
        self.update_title()

        theme_path = THEMES_DIR.joinpath("awthemes-10.4.0")
        root().tk.call("lappend", "auto_path", theme_path)
        root().tk.call("package", "require", "awdark")
        root().tk.call("package", "require", "awlight")

        self.menu_file: Optional[Menu] = (
            None  # File menu is saved to allow deletion & re-creation
        )
        self.init_menus()

        self.init_statusbar(statusbar())

        self.file.languages = preferences.get(PrefKey.DEFAULT_LANGUAGES)

        maintext().focus_set()
        maintext().add_modified_callback(self.update_title)
        maintext().add_config_callback(self.update_title)

        # Known tkinter issue - must call this before any dialogs can get created,
        # or focus will not return to maintext on Windows
        root().update_idletasks()

        # After menus, etc., have been created, set the zoomed/fullscreen state
        root().set_zoom_fullscreen()

        self.logging_add_gui()
        logger.info("GUI initialized")

        preferences.run_callbacks()

        self.load_file_if_given()

        def check_save_and_destroy() -> None:
            if self.file.check_save():
                root().destroy()

        root().protocol("WM_DELETE_WINDOW", check_save_and_destroy)

    def parse_args(self) -> None:
        """Parse command line args"""
        parser = argparse.ArgumentParser(
            prog="guiguts", description="Guiguts is an ebook creation tool"
        )
        parser.add_argument(
            "filename", nargs="?", help="Optional name of file to be loaded"
        )
        parser.add_argument(
            "-r",
            "--recent",
            type=int,
            choices=range(1, NUM_RECENT_FILES + 1),
            help="Number of 'Recent File' to be loaded: 1 is most recent",
        )
        parser.add_argument(
            "-d",
            "--debug",
            action="store_true",
            help="Run in debug mode",
        )
        parser.add_argument(
            "--nohome",
            action="store_true",
            help="Do not load the Preferences file",
        )
        self.args = parser.parse_args()

    def load_file_if_given(self) -> None:
        """If filename, or recent number, given on command line
        load the relevant file."""

        if self.args.filename:
            self.file.load_file(self.args.filename)
        elif self.args.recent:
            index = self.args.recent - 1
            try:
                self.file.load_file(preferences.get(PrefKey.RECENT_FILES)[index])
            except IndexError:
                pass  # Not enough recent files to load the requested one

    def auto_image_callback(self, value: bool) -> None:
        """Callback when auto_image preference is changed."""
        statusbar().set("see img", "Auto Img" if value else "See Img")
        if value:
            self.image_dir_check()
            self.auto_image_check()

    def auto_image_check(self) -> None:
        """Function called repeatedly to check whether an image needs loading."""
        if preferences.get(PrefKey.AUTO_IMAGE):
            self.mainwindow.load_image(self.file.get_current_image_path())
            root().after(200, self.auto_image_check)

    def show_image(self) -> None:
        """Show the image corresponding to current location."""
        self.image_dir_check()
        self.mainwindow.load_image(self.file.get_current_image_path())

    def hide_image(self) -> None:
        """Hide the image."""
        preferences.set(PrefKey.AUTO_IMAGE, False)
        self.mainwindow.hide_image()

    def image_dir_check(self) -> None:
        """Check if image dir is set up correctly."""
        if self.file.filename and not (
            self.file.image_dir and os.path.exists(self.file.image_dir)
        ):
            self.file.choose_image_dir()

    def run(self) -> None:
        """Run the app."""
        root().mainloop()

    def filename_changed(self) -> None:
        """Handle side effects needed when filename changes."""
        self.init_file_menu()  # Recreate file menu to reflect recent files
        self.update_title()
        if preferences.get(PrefKey.AUTO_IMAGE):
            self.image_dir_check()
        maintext().after_idle(maintext().focus_set)

    def languages_changed(self) -> None:
        """Handle side effects needed when languages change."""
        statusbar().set("languages label", "Lang: " + (self.file.languages or ""))

    def update_title(self) -> None:
        """Update the window title to reflect current status."""
        modtitle = " - edited" if maintext().is_modified() else ""
        filetitle = self.file.filename
        # Restrict filepath to rightmost W characters where W is maintext's visible width
        len_title = len(filetitle) + len(modtitle) + len("...")
        if len_title > 0:
            max_width = int(maintext().winfo_width() / maintext().font.measure("0"))
            if len_title > max_width:
                filetitle = "..." + filetitle[len_title - max_width :]
            if filetitle:
                filetitle = " - " + filetitle
        root().title(f"Guiguts {version('guiguts')}" + modtitle + filetitle)
        if is_mac():
            root().wm_attributes("-modified", maintext().is_modified())

    def quit_program(self) -> None:
        """Exit the program."""
        if self.file.check_save():
            root().quit()

    def help_about(self) -> None:
        """Display a 'Help About' dialog."""
        help_message = """Guiguts - an application to support creation of ebooks for PG

Copyright Contributors to the Guiguts-py project.

This program is free software; you can redistribute it
and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation;
either version 2 of the License, or (at your option) any
later version.

This program is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public
License along with this program; if not, write to the
Free Software Foundation, Inc., 51 Franklin Street,
Fifth Floor, Boston, MA 02110-1301 USA."""

        messagebox.showinfo(title="About Guiguts", message=help_message)

    def show_page_details_dialog(self) -> None:
        """Show the page details display/edit dialog."""
        PageDetailsDialog.show_dialog(page_details=self.file.page_details)

    def open_document(self, args: list[str]) -> None:
        """Handle drag/drop on Macs.

        Accepts a list of filenames, but only loads the first.
        """
        self.file.load_file(args[0])

    def open_file(self, filename: str = "") -> None:
        """Open new file, close old image if open.

        Args:
            filename: Optional filename - prompt user if none given.
        """
        if self.file.open_file(filename):
            self.mainwindow.clear_image()

    def close_command(self) -> None:
        """Close top-level dialog with focus, or close the file if it's
        the main window."""
        focus_widget = maintext().focus_get()
        if focus_widget is None:
            return
        focus_tl = focus_widget.winfo_toplevel()
        if focus_tl == maintext().winfo_toplevel():
            self.close_file()
        else:
            focus_tl.destroy()

    def close_file(self) -> None:
        """Close currently loaded file and associated image."""
        self.file.close_file()
        self.mainwindow.clear_image()

    def load_current_image(self) -> None:
        """Load image corresponding to current cursor position"""
        self.mainwindow.load_image(self.file.get_current_image_path())

    def show_help_manual(self) -> None:
        """Display the manual."""
        webbrowser.open("https://www.pgdp.net/wiki/PPTools/Guiguts/Guiguts_2_Manual")

    def show_help_regex(self) -> None:
        """Display the regex cheat sheet."""
        webbrowser.open(
            "https://www.pgdp.net/wiki/PPTools/Guiguts/Guiguts_2_Manual/Help_Menu#Regular_Expression_Quick_Reference"
        )

    def highlight_quotbrac_callback(self, value: bool) -> None:
        """Callback when highlight_quotbrac preference is changed."""
        if value:
            maintext().highlight_quotbrac()
        else:
            maintext().remove_highlights_quotbrac()

    def initialize_preferences(self) -> None:
        """Set default preferences and load settings from the GGPrefs file."""
        preferences.set_default(PrefKey.AUTO_IMAGE, False)
        preferences.set_callback(PrefKey.AUTO_IMAGE, self.auto_image_callback)
        preferences.set_default(PrefKey.BELL_AUDIBLE, True)
        preferences.set_default(PrefKey.BELL_VISUAL, True)
        preferences.set_default(PrefKey.IMAGE_WINDOW_DOCKED, True)
        preferences.set_default(PrefKey.RECENT_FILES, [])
        preferences.set_default(PrefKey.LINE_NUMBERS, True)
        preferences.set_default(PrefKey.ORDINAL_NAMES, True)
        preferences.set_callback(
            PrefKey.LINE_NUMBERS, lambda value: maintext().show_line_numbers(value)
        )
        preferences.set_default(PrefKey.SEARCH_HISTORY, [])
        preferences.set_default(PrefKey.REPLACE_HISTORY, [])
        preferences.set_default(PrefKey.SEARCHDIALOG_REVERSE, False)
        preferences.set_default(PrefKey.SEARCHDIALOG_MATCH_CASE, False)
        preferences.set_default(PrefKey.SEARCHDIALOG_WHOLE_WORD, False)
        preferences.set_default(PrefKey.SEARCHDIALOG_WRAP, True)
        preferences.set_default(PrefKey.SEARCHDIALOG_REGEX, False)
        preferences.set_default(PrefKey.SEARCHDIALOG_MULTI_REPLACE, False)
        preferences.set_default(PrefKey.DIALOG_GEOMETRY, {})
        preferences.set_default(PrefKey.ROOT_GEOMETRY, "800x400")
        preferences.set_default(PrefKey.ROOT_GEOMETRY_STATE, "normal")
        preferences.set_default(PrefKey.DEFAULT_LANGUAGES, "en")
        preferences.set_default(PrefKey.WFDIALOG_SUSPECTS_ONLY, False)
        preferences.set_default(PrefKey.WFDIALOG_IGNORE_CASE, False)
        preferences.set_default(PrefKey.WFDIALOG_DISPLAY_TYPE, WFDisplayType.ALL_WORDS)
        preferences.set_default(PrefKey.WFDIALOG_SORT_TYPE, WFSortType.ALPHABETIC)
        preferences.set_default(PrefKey.CHECKERDIALOG_SORT_TYPE_DICT, {})
        preferences.set_default(PrefKey.CHECKERDIALOG_SUSPECTS_ONLY_DICT, {})
        preferences.set_default(PrefKey.WFDIALOG_ITALIC_THRESHOLD, ["4"])
        preferences.set_default(PrefKey.WFDIALOG_REGEX, [])
        preferences.set_default(
            PrefKey.JEEBIES_PARANOIA_LEVEL, JeebiesParanoiaLevel.NORMAL
        )
        preferences.set_default(
            PrefKey.LEVENSHTEIN_EDIT_DISTANCE, LevenshteinEditDistance.ONE
        )
        preferences.set_default(PrefKey.FOOTNOTE_INDEX_STYLE, FootnoteIndexStyle.NUMBER)
        preferences.set_default(PrefKey.WRAP_LEFT_MARGIN, 0)
        preferences.set_default(PrefKey.WRAP_RIGHT_MARGIN, 72)
        preferences.set_default(PrefKey.WRAP_BLOCKQUOTE_INDENT, 2)
        preferences.set_default(PrefKey.WRAP_BLOCKQUOTE_RIGHT_MARGIN, 72)
        preferences.set_default(PrefKey.WRAP_BLOCK_INDENT, 2)
        preferences.set_default(PrefKey.WRAP_POETRY_INDENT, 4)
        preferences.set_default(PrefKey.WRAP_INDEX_MAIN_MARGIN, 2)
        preferences.set_default(PrefKey.WRAP_INDEX_WRAP_MARGIN, 8)
        preferences.set_default(PrefKey.WRAP_INDEX_RIGHT_MARGIN, 72)
        preferences.set_default(PrefKey.TEXT_MARKUP_ITALIC, "_")
        preferences.set_default(PrefKey.TEXT_MARKUP_BOLD, "=")
        preferences.set_default(PrefKey.TEXT_MARKUP_SMALLCAPS, "+")
        preferences.set_default(PrefKey.TEXT_MARKUP_GESPERRT, "~")
        preferences.set_default(PrefKey.TEXT_MARKUP_FONT, "=")
        preferences.set_default(PrefKey.PAGESEP_AUTO_TYPE, PageSepAutoType.AUTO_FIX)
        preferences.set_default(PrefKey.THEME_NAME, "Default")
        preferences.set_callback(
            PrefKey.THEME_NAME,
            lambda value: themed_style().theme_use(
                theme_name_internal_from_user(value)
            ),
        )
        preferences.set_default(PrefKey.TEAROFF_MENUS, False)
        preferences.set_default(PrefKey.COMPOSE_HISTORY, [])
        # Since fonts aren't available until Tk has initialized, set default font family
        # to be empty string here, and set the true default later in MainText.
        preferences.set_default(PrefKey.TEXT_FONT_FAMILY, "")
        preferences.set_callback(
            PrefKey.TEXT_FONT_FAMILY,
            lambda *value: maintext().set_font(),
        )
        preferences.set_default(PrefKey.TEXT_FONT_SIZE, 12)
        preferences.set_callback(
            PrefKey.TEXT_FONT_SIZE,
            lambda *value: maintext().set_font(),
        )
        preferences.set_default(PrefKey.SPELL_THRESHOLD, 3)
        preferences.set_default(PrefKey.UNMATCHED_NESTABLE, False)
        preferences.set_default(
            PrefKey.UNICODE_BLOCK, UnicodeBlockDialog.commonly_used_characters_name
        )
        preferences.set_default(PrefKey.UNICODE_SEARCH_HISTORY, [])
        preferences.set_default(PrefKey.SPLIT_TEXT_WINDOW, False)
        preferences.set_default(PrefKey.SPLIT_TEXT_SASH_COORD, 0)
        preferences.set_default(PrefKey.IMAGE_INVERT, False)
        preferences.set_default(PrefKey.IMAGE_FLOAT_GEOMETRY, "400x600+100+100")
        preferences.set_default(PrefKey.IMAGE_DOCK_SASH_COORD, 300)
        preferences.set_default(PrefKey.IMAGE_SCALE_FACTOR, 0.5)
        preferences.set_default(
            PrefKey.SCANNOS_FILENAME,
            str(DEFAULT_SCANNOS_DIR.joinpath(DEFAULT_REGEX_SCANNOS)),
        )
        preferences.set_default(
            PrefKey.SCANNOS_HISTORY,
            [
                str(DEFAULT_SCANNOS_DIR.joinpath(DEFAULT_REGEX_SCANNOS)),
                str(DEFAULT_SCANNOS_DIR.joinpath(DEFAULT_STEALTH_SCANNOS)),
            ],
        )
        preferences.set_default(PrefKey.HIGHLIGHT_QUOTBRAC, False)
        preferences.set_callback(
            PrefKey.HIGHLIGHT_QUOTBRAC, self.highlight_quotbrac_callback
        )
        preferences.set_default(PrefKey.COLUMN_NUMBERS, False)
        preferences.set_callback(
            PrefKey.COLUMN_NUMBERS, lambda value: maintext().show_column_numbers(value)
        )
        preferences.set_default(PrefKey.HTML_ITALIC_MARKUP, HTMLMarkupTypes.KEEP)
        preferences.set_default(PrefKey.HTML_BOLD_MARKUP, HTMLMarkupTypes.KEEP)
        preferences.set_default(PrefKey.HTML_GESPERRT_MARKUP, HTMLMarkupTypes.EM_CLASS)
        preferences.set_default(PrefKey.HTML_FONT_MARKUP, HTMLMarkupTypes.SPAN_CLASS)
        preferences.set_default(
            PrefKey.HTML_UNDERLINE_MARKUP, HTMLMarkupTypes.SPAN_CLASS
        )
        preferences.set_default(PrefKey.HTML_SHOW_PAGE_NUMBERS, True)

        # Check all preferences have a default
        for pref_key in PrefKey:
            assert preferences.get_default(pref_key) is not None

        # If `--nohome` argument given, Prefs are not loaded & saved in Prefs file
        preferences.set_permanent(not self.args.nohome)
        preferences.load()

    # Lay out menus
    def init_menus(self) -> None:
        """Create all the menus."""
        self.init_file_menu()
        self.init_edit_menu()
        self.init_search_menu()
        self.init_tools_menu()
        self.init_text_menu()
        self.init_html_menu()
        self.init_view_menu()
        self.init_help_menu()
        self.init_os_menu()

        if is_mac():
            root().createcommand(
                "tk::mac::ShowPreferences", PreferencesDialog.show_dialog
            )
            root().createcommand("tk::mac::OpenDocument", self.open_document)
            root().createcommand("tk::mac::Quit", self.quit_program)

    def init_file_menu(self) -> None:
        """(Re-)create the File menu."""
        try:
            self.menu_file.delete(0, "end")  # type:ignore[union-attr]
        except AttributeError:
            self.menu_file = Menu(menubar(), "~File")
        assert self.menu_file is not None
        self.menu_file.add_button("~Open...", self.open_file, "Cmd/Ctrl+O")
        self.init_file_recent_menu(self.menu_file)
        self.menu_file.add_button("~Save", self.file.save_file, "Cmd/Ctrl+S")
        self.menu_file.add_button(
            "Save ~As...", self.file.save_as_file, "Cmd/Ctrl+Shift+S"
        )
        self.menu_file.add_button("Sa~ve a Copy As...", self.file.save_copy_as_file)
        self.menu_file.add_button(
            "~Close", self.close_command, "Cmd+W" if is_mac() else ""
        )
        self.init_file_project_menu(self.menu_file)
        if not is_mac():
            self.menu_file.add_separator()
            self.menu_file.add_button("E~xit", self.quit_program, "")

    def init_file_recent_menu(self, parent: Menu) -> None:
        """Create the Recent Documents menu."""
        recent_menu = Menu(parent, "Recent Doc~uments")
        for count, file in enumerate(preferences.get(PrefKey.RECENT_FILES), start=1):
            recent_menu.add_button(
                f"~{count}: {file}",
                lambda fn=file: self.open_file(fn),  # type:ignore[misc]
            )

    def init_file_project_menu(self, parent: Menu) -> None:
        """Create the File->Project menu."""
        proj_menu = Menu(parent, "~Project")
        proj_menu.add_button(
            "Add ~Good/Bad Words to Project Dictionary",
            self.file.add_good_and_bad_words,
        )
        proj_menu.add_button(
            f"Set ~Scan Image {folder_dir_str()}...",
            self.file.choose_image_dir,
        )
        proj_menu.add_button("Set ~Language(s)...", self.file.set_languages)
        proj_menu.add_separator()
        proj_menu.add_button("~Configure Page Labels...", self.show_page_details_dialog)
        proj_menu.add_separator()
        proj_menu.add_button("~Add Page Marker Flags", self.file.add_page_flags)
        proj_menu.add_button("~Remove Page Marker Flags", self.file.remove_page_flags)

    def init_edit_menu(self) -> None:
        """Create the Edit menu."""
        menu_edit = Menu(menubar(), "~Edit")
        menu_edit.add_button("~Undo", "<<Undo>>", "Cmd/Ctrl+Z")
        menu_edit.add_button(
            "~Redo", "<<Redo>>", "Cmd+Shift+Z" if is_mac() else "Ctrl+Y"
        )
        menu_edit.add_separator()
        menu_edit.add_cut_copy_paste()
        menu_edit.add_button(
            "R~estore Selection",
            maintext().restore_selection_ranges,
        )
        menu_edit.add_separator()
        menu_edit.add_button(
            "Co~lumn Cut", maintext().columnize_cut, "Cmd/Ctrl+Shift+X"
        )
        menu_edit.add_button(
            "C~olumn Copy", maintext().columnize_copy, "Cmd/Ctrl+Shift+C"
        )
        menu_edit.add_button(
            "Colu~mn Paste", maintext().columnize_paste, "Cmd/Ctrl+Shift+V"
        )
        menu_edit.add_button(
            "To~ggle Column/Regular Selection",
            maintext().toggle_selection_type,
        )
        menu_edit.add_separator()
        case_menu = Menu(menu_edit, "C~hange Case")
        case_menu.add_button(
            "~lowercase selection",
            lambda: maintext().transform_selection(str.lower),
            "",
        )
        case_menu.add_button(
            "~Sentence case selection",
            lambda: maintext().transform_selection(
                maintext().sentence_case_transformer
            ),
            "",
        )
        case_menu.add_button(
            "~Title Case Selection",
            lambda: maintext().transform_selection(maintext().title_case_transformer),
            "",
        )
        case_menu.add_button(
            "~UPPERCASE SELECTION",
            lambda: maintext().transform_selection(str.upper),
            "",
        )
        if not is_mac():
            menu_edit.add_separator()
            menu_edit.add_button("Pre~ferences...", PreferencesDialog.show_dialog)

    def init_search_menu(self) -> None:
        """Create the Search menu."""
        menu_search = Menu(menubar(), "~Search")
        menu_search.add_button(
            "~Search & Replace...",
            show_search_dialog,
            "Cmd/Ctrl+F",
        )
        menu_search.add_button(
            "Find Ne~xt",
            find_next,
            "Cmd+G" if is_mac() else "F3",
        )
        menu_search.add_button(
            "Find Pre~vious",
            lambda: find_next(backwards=True),
            "Cmd+Shift+G" if is_mac() else "Shift+F3",
        )
        menu_search.add_separator()
        self.init_search_goto_menu(menu_search)
        menu_search.add_separator()
        menu_search.add_button(
            "Find Proofer ~Comments",
            proofer_comment_check,
        )
        menu_search.add_button(
            "Find ~Asterisks w/o Slash",
            asterisk_check,
        )
        menu_search.add_separator()
        menu_search.add_button(
            "Highlight Single ~Quotes in Selection",
            maintext().highlight_single_quotes,
        )
        menu_search.add_button(
            "Highlight ~Double Quotes in Selection",
            maintext().highlight_double_quotes,
        )
        menu_search.add_button(
            "~Remove Highlights",
            maintext().remove_highlights,
        )
        menu_search.add_separator()
        menu_search.add_checkbox(
            "Highlight S~urrounding Quotes & Brackets",
            root().highlight_quotbrac,
        )
        menu_search.add_checkbox(
            "Highlight Al~ignment Column",
            maintext().aligncol_active,
            lambda: maintext().highlight_aligncol_callback(True),
            lambda: maintext().highlight_aligncol_callback(False),
        )
        menu_search.add_separator()
        self.init_bookmark_menu(menu_search)

    def init_search_goto_menu(self, parent: Menu) -> None:
        """Create the Search->Goto menu."""
        goto_menu = Menu(parent, "~Goto")
        goto_menu.add_button(
            "~Line Number...",
            self.file.goto_line,
        )
        goto_menu.add_button(
            "Pa~ge (png)...",
            self.file.goto_image,
        )
        goto_menu.add_button(
            "Page La~bel...",
            self.file.goto_page,
        )
        goto_menu.add_button(
            "~Previous Page",
            self.file.prev_page,
        )
        goto_menu.add_button(
            "~Next Page",
            self.file.next_page,
        )

    def init_bookmark_menu(self, parent: Menu) -> None:
        """Create the Bookmarks menu."""
        bookmark_menu = Menu(parent, "~Bookmarks")
        # Because keyboard layouts are different, need to bind to several keys for some bookmarks
        shortcuts = [
            ("exclam",),
            ("at", "quotedbl"),
            ("numbersign", "sterling", "section", "periodcentered"),
            ("dollar", "currency"),
            ("percent",),
        ]
        # Shift+Cmd+number no good for Mac due to clash with screen capture shortcuts
        # Ctrl+number could also clash on Mac with virtual desktop switching, but
        # the best option we have that works at the moment.
        modifier = "Shift"
        for bm, keys in enumerate(shortcuts, start=1):
            bookmark_menu.add_button(
                f"Set Bookmark {bm}",
                lambda num=bm: self.file.set_bookmark(num),  # type:ignore[misc]
                f"{modifier}+Ctrl+Key-{bm}",
            )
            # Add extra shortcuts to cope with keyboard layout differences
            for key in keys:
                (_, key_event) = process_accel(f"{modifier}+Ctrl+{key}")
                maintext().key_bind(
                    key_event,
                    lambda _event, num=bm: self.file.set_bookmark(  # type:ignore[misc]
                        num
                    ),
                    bind_all=True,
                )

        for bm in range(1, 6):
            bookmark_menu.add_button(
                f"Go To Bookmark {bm}",
                lambda num=bm: self.file.goto_bookmark(num),  # type:ignore[misc]
                f"Ctrl+Key-{bm}",
            )

    def init_tools_menu(self) -> None:
        """Create the Tools menu."""
        menu_tools = Menu(menubar(), "~Tools")
        menu_tools.add_button("Basic Fi~xup...", basic_fixup_check)
        menu_tools.add_button("~Word Frequency...", word_frequency)
        menu_tools.add_button("~Bookloupe...", bookloupe_check)
        menu_tools.add_button(
            "~Spelling...",
            lambda: spell_check(
                self.file.project_dict,
                self.file.add_good_word_to_project_dictionary,
                self.file.add_good_word_to_global_dictionary,
            ),
        )
        menu_tools.add_button("PP~txt...", lambda: pptxt(self.file.project_dict))
        menu_tools.add_button("~Jeebies...", jeebies_check)
        menu_tools.add_button("Stealth S~cannos...", stealth_scannos)
        menu_tools.add_button(
            "Word ~Distance Check...", lambda: levenshtein_check(self.file.project_dict)
        )
        menu_tools.add_separator()
        menu_tools.add_button("~Page Separator Fixup...", page_separator_fixup)
        menu_tools.add_button("~Footnote Fixup...", footnote_check)
        menu_tools.add_button("Side~note Fixup...", lambda: illosn_check("Sidenote"))
        menu_tools.add_button(
            "~Illustration Fixup...", lambda: illosn_check("Illustration")
        )
        menu_tools.add_separator()
        menu_tools.add_button("~Rewrap All", self.file.rewrap_all)
        menu_tools.add_button("R~ewrap Selection", self.file.rewrap_selection)
        menu_tools.add_button("C~lean Up Rewrap Markers", self.file.rewrap_cleanup)
        menu_tools.add_separator()
        menu_tools.add_button("Convert to Curly ~Quotes", convert_to_curly_quotes)
        menu_tools.add_button("Check Curly Q~uotes", check_curly_quotes)
        unmatched_menu = Menu(menu_tools, "Un~matched")
        unmatched_menu.add_button("Bloc~k Markup...", unmatched_block_markup)
        unmatched_menu.add_button("~DP Markup...", unmatched_dp_markup)
        unmatched_menu.add_button("~Brackets...", unmatched_brackets)
        unmatched_menu.add_button("Curly ~Quotes...", unmatched_curly_quotes)

        fraction_menu = Menu(menu_tools, "~Convert Fractions")
        fraction_menu.add_button(
            "~Unicode Fractions Only",
            lambda: fraction_convert(FractionConvertType.UNICODE),
        )
        fraction_menu.add_button(
            "Unicode Fractions ~Or Super/Subscript",
            lambda: fraction_convert(FractionConvertType.MIXED),
        )
        fraction_menu.add_button(
            "All to ~Super/Subscript",
            lambda: fraction_convert(FractionConvertType.SUPSUB),
        )
        unicode_menu = Menu(menu_tools, "~Unicode")
        unicode_menu.add_button(
            "Unicode ~Search/Entry", UnicodeSearchDialog.show_dialog
        )
        unicode_menu.add_button("Unicode ~Blocks", UnicodeBlockDialog.show_dialog)
        unicode_menu.add_button(
            "~Normalize Selected Characters",
            unicode_normalize,
        )
        unicode_menu.add_button(
            "~Compose Sequence...",
            ComposeSequenceDialog.show_dialog,
            "Cmd/Ctrl+I",
        )

    def init_text_menu(self) -> None:
        """Create the Text menu."""
        menu_tools = Menu(menubar(), "Te~xt")
        menu_tools.add_button(
            "Convert ~Markup...", TextMarkupConvertorDialog.show_dialog
        )

    def init_html_menu(self) -> None:
        """Create the HTML menu."""
        menu_tools = Menu(menubar(), "HT~ML")
        menu_tools.add_button("HTML ~Generator...", HTMLGeneratorDialog.show_dialog)

    def init_view_menu(self) -> None:
        """Create the View menu."""
        menu_view = Menu(menubar(), "~View")
        menu_view.add_checkbox(
            "Split ~Text Window",
            root().split_text_window,
            lambda: maintext().show_peer(),
            lambda: maintext().hide_peer(),
        )
        menu_view.add_checkbox(
            "~Dock Image",
            root().image_window_docked_state,
            self.mainwindow.dock_image,
            self.mainwindow.float_image,
        )
        menu_view.add_checkbox(
            "~Auto Image",
            root().auto_image_state,
        )
        menu_view.add_button("~See Image", self.show_image)
        menu_view.add_button("~Hide Image", self.hide_image)
        menu_view.add_separator()
        menu_view.add_button("~Message Log", self.mainwindow.messagelog.show)
        menu_view.add_separator()
        if not is_mac():  # Full Screen behaves oddly on Macs
            menu_view.add_checkbox(
                "~Full Screen",
                root().full_screen_var,
                lambda: root().wm_attributes("-fullscreen", True),
                lambda: root().wm_attributes("-fullscreen", False),
            )

    def init_help_menu(self) -> None:
        """Create the Help menu."""
        menu_help = Menu(menubar(), "~Help")
        menu_help.add_button(
            "Guiguts ~Manual", self.show_help_manual, "F1", force_main_only=True
        )
        menu_help.add_button("About ~Guiguts", self.help_about)
        menu_help.add_button("~Regex Quick Reference", self.show_help_regex)
        menu_help.add_button(
            "List of ~Compose Sequences", ComposeHelpDialog.show_dialog
        )

    def init_os_menu(self) -> None:
        """Create the OS-specific menu.

        Currently only does anything on Macs
        """
        if is_mac():
            # Window menu
            Menu(menubar(), "Window", name="window")

    def init_statusbar(self, the_statusbar: StatusBar) -> None:
        """Add labels to initialize the statusbar.

        Functions are bound to Button Release, rather than Button Press, since
        that is how buttons normally operate.
        """

        def rowcol_str() -> str:
            """Format current insert index for statusbar."""
            row, col = maintext().get_insert_index().rowcol()
            return f"L:{row} C:{col}"

        the_statusbar.add(
            "rowcol",
            tooltip="Click: Go to line\nShift click: Toggle line numbers\nShift right-click: Toggle column numbers",
            update=rowcol_str,
        )
        the_statusbar.add_binding("rowcol", "ButtonRelease-1", self.file.goto_line)
        the_statusbar.add_binding(
            "rowcol",
            "Shift-ButtonRelease-1",
            lambda: preferences.toggle(PrefKey.LINE_NUMBERS),
        )
        the_statusbar.add_binding(
            "rowcol",
            "Shift-ButtonRelease-3",
            lambda: preferences.toggle(PrefKey.COLUMN_NUMBERS),
        )

        the_statusbar.add(
            "img",
            tooltip="Click: Go to image",
            update=lambda: "Img: " + maintext().get_current_image_name(),
        )
        the_statusbar.add_binding("img", "ButtonRelease-1", self.file.goto_image)

        the_statusbar.add(
            "prev img", tooltip="Click: Go to previous image", text="<", width=1
        )
        the_statusbar.add_binding("prev img", "ButtonRelease-1", self.file.prev_page)

        the_statusbar.add(
            "see img",
            tooltip="Click: See image\nShift click: Toggle auto-image",
            text="See Img",
            width=9,
        )
        the_statusbar.add_binding(
            "see img",
            "ButtonRelease-1",
            self.show_image,
        )
        the_statusbar.add_binding(
            "see img",
            "Shift-ButtonRelease-1",
            lambda: preferences.toggle(PrefKey.AUTO_IMAGE),
        )

        the_statusbar.add(
            "next img", tooltip="Click: Go to next image", text=">", width=1
        )
        the_statusbar.add_binding("next img", "ButtonRelease-1", self.file.next_page)

        the_statusbar.add(
            "page label",
            tooltip="Click: Go to page\nShift click: Configure page labels",
            text="Lbl: ",
            update=lambda: "Lbl: " + self.file.get_current_page_label(),
        )
        the_statusbar.add_binding("page label", "ButtonRelease-1", self.file.goto_page)
        the_statusbar.add_binding(
            "page label", "Shift-ButtonRelease-1", self.show_page_details_dialog
        )

        def selection_str() -> str:
            """Format current selection range for statusbar.

            Returns:
                "Start-End" for a regualar selection, and "R:rows C:cols" for column selection.
            """
            maintext().selection_cursor()
            ranges = maintext().selected_ranges()
            maintext().save_selection_ranges()
            if not ranges:
                return "No selection"
            if len(ranges) == 1:
                return f"{ranges[0].start.index()}-{ranges[-1].end.index()}"
            row_diff = abs(ranges[-1].end.row - ranges[0].start.row) + 1
            col_diff = abs(ranges[-1].end.col - ranges[0].start.col)
            return f"R:{row_diff} C:{col_diff}"

        the_statusbar.add(
            "selection",
            tooltip="Click: Restore previous selection\nShift Click: Toggle column/regular selection",
            update=selection_str,
            width=16,
        )
        the_statusbar.add_binding(
            "selection", "ButtonRelease-1", maintext().restore_selection_ranges
        )
        the_statusbar.add_binding(
            "selection", "Shift-ButtonRelease-1", maintext().toggle_selection_type
        )

        the_statusbar.add(
            "languages label",
            tooltip="Click: Set language(s), e.g. 'en' or 'de fr'",
            text="Lang: ",
        )
        the_statusbar.add_binding(
            "languages label", "ButtonRelease-1", self.file.set_languages
        )

        def ordinal_name_display(force: Optional[bool] = None) -> None:
            """Set, unset, or toggle, name display in ordinal button.

            Args:
                Force: True to set, False to unset, omit to toggle.
            """
            if force is None:
                force = not preferences.get(PrefKey.ORDINAL_NAMES)
            preferences.set(PrefKey.ORDINAL_NAMES, force)

        def ordinal_str() -> str:
            """Format ordinal of single selected char, or char at current insert
            index for statusbar."""
            # Get character - display nothing if more than one char selected
            sel_ranges = maintext().selected_ranges()
            if len(sel_ranges) == 0:
                char = maintext().get(maintext().get_insert_index().index())
            elif len(sel_ranges) == 1:
                char = maintext().selected_text()
                if len(char) != 1:
                    return ""
            else:
                return ""
            # unicodedata.name fails to return name for "control" characters
            # but the only one we care about is line feed
            if preferences.get(PrefKey.ORDINAL_NAMES):
                try:
                    name = f": {unicodedata.name(char)}"
                except ValueError:
                    name = ": LINE FEED" if char == "\n" else ""
            else:
                name = ""
            return f"U+{ord(char):04x}{name}"

        the_statusbar.add(
            "ordinal",
            tooltip="Character after the cursor\nClick to toggle name display",
            update=ordinal_str,
        )
        the_statusbar.add_binding("ordinal", "ButtonRelease-1", ordinal_name_display)

    def logging_init(self) -> None:
        """Set up basic logger until GUI is ready."""
        if self.args.debug:
            log_level = logging.DEBUG
            console_log_level = logging.DEBUG
            formatter = logging.Formatter(DEBUG_FORMAT, "%H:%M:%S")
        else:
            log_level = logging.INFO
            console_log_level = logging.WARNING
            formatter = logging.Formatter(MESSAGE_FORMAT, "%H:%M:%S")
        logger.setLevel(log_level)
        # Output to console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    def logging_add_gui(self) -> None:
        """Add handlers to display log messages via the GUI.

        Assumes mainwindow has created the message_log handler.
        """

        # Message log is approximate GUI equivalent to console output
        if self.args.debug:
            message_log_level = logging.DEBUG
            formatter = logging.Formatter(DEBUG_FORMAT, "%H:%M:%S")
        else:
            message_log_level = logging.INFO
            formatter = logging.Formatter(MESSAGE_FORMAT, "%H:%M:%S")
        self.mainwindow.messagelog.setLevel(message_log_level)
        self.mainwindow.messagelog.setFormatter(formatter)
        logger.addHandler(self.mainwindow.messagelog)

        # Alert is just for errors, e.g. unable to load file
        alert_handler = ErrorHandler()
        alert_handler.setLevel(logging.ERROR)
        alert_handler.setFormatter(formatter)
        logger.addHandler(alert_handler)


def main() -> None:
    """Main application function."""
    Guiguts().run()


if __name__ == "__main__":
    main()
