"""Handle file operations"""

import hashlib
import json
import logging
import os.path
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable, Final, TypedDict, Literal, Optional

import regex as re

from guiguts.mainwindow import mainimage
from guiguts.maintext import (
    maintext,
    PAGE_FLAG_TAG,
    PAGEMARK_PIN,
    BOOKMARK_TAG,
    img_from_page_mark,
    page_mark_from_img,
)
from guiguts.page_details import (
    PageDetail,
    PageDetails,
    PAGE_LABEL_PREFIX,
    NUMBER_INCREMENT,
    STYLE_ARABIC,
    STYLE_DITTO,
)
from guiguts.preferences import preferences, PrefKey
from guiguts.project_dict import ProjectDict, GOOD_WORDS_FILENAME, BAD_WORDS_FILENAME
from guiguts.root import root

from guiguts.utilities import (
    load_dict_from_json,
    IndexRowCol,
    IndexRange,
    sound_bell,
    folder_dir_str,
)
from guiguts.widgets import grab_focus, ToplevelDialog

logger = logging.getLogger(__package__)

NUM_RECENT_FILES = 9
BINFILE_SUFFIX = ".json"

BINFILE_KEY_MD5CHECKSUM: Final = "md5checksum"
BINFILE_KEY_PAGEDETAILS: Final = "pagedetails"
BINFILE_KEY_INSERTPOS: Final = "insertpos"
BINFILE_KEY_INSERTPOSPEER: Final = "insertpospeer"
BINFILE_KEY_IMAGEDIR: Final = "imagedir"
BINFILE_KEY_PROJECTID: Final = "projectid"
BINFILE_KEY_LANGUAGES: Final = "languages"
BINFILE_KEY_BOOKMARKS: Final = "bookmarks"

PAGE_FLAGS_NONE = 0
PAGE_FLAGS_SOME = 1
PAGE_FLAGS_ALL = 2
PAGE_FLAG_PREFIX = "Img"
PAGE_FLAG_START = "["
PAGE_FLAG_START_E = re.escape(PAGE_FLAG_START)
PAGE_FLAG_END = "]"
PAGE_FLAG_END_E = re.escape(PAGE_FLAG_END)
PAGE_FLAG_SEP = "|"
PAGE_FLAG_SEP_E = re.escape(PAGE_FLAG_SEP)
PAGE_FLAG_ARABIC = "A"
PAGE_FLAG_ROMAN = "R"
# 3 capture groups for img, style (Roman/Arabic), number (or +1/None)
# Label is calculated from other info after file load.
PAGE_FLAG_REGEX = rf"{PAGE_FLAG_START_E}{PAGE_FLAG_PREFIX}(.+?){PAGE_FLAG_SEP_E}(.+?){PAGE_FLAG_SEP_E}(.+?){PAGE_FLAG_END_E}"

PAGE_SEPARATOR_REGEX = r"File:.+?([^/\\ ]+)\.(png|jpg)"

BOOKMARK_BASE = "Bookmark"


class BinDict(TypedDict):
    """Dictionary for saving to bin file."""

    md5checksum: str
    pagedetails: PageDetails
    insertpos: str
    insertpospeer: str
    imagedir: str
    projectid: str
    languages: str
    bookmarks: dict[str, str]


class File:
    """Handle data and actions relating to the main text file.

    Attributes:
        _filename: Current filename.
        _filename_callback: Function to be called whenever filename is set.
        _languages: Languages used in file.
        _languages_callback: Function to be called whenever languages are set.
        _image_dir: Directory containing scan images.
        _project_id: Project ID.
    """

    def __init__(
        self,
        filename_callback: Callable[[], None],
        languages_callback: Callable[[], None],
    ):
        """
        Args:
            filename_callback: Function to be called whenever filename is set.
        """
        self._filename = ""
        self._filename_callback = filename_callback
        self._image_dir = ""
        self._project_id = ""
        self._languages = "en"
        self._languages_callback = languages_callback
        self.page_details = PageDetails()
        self.project_dict = ProjectDict()

    @property
    def filename(self) -> str:
        """Name of currently loaded file.

        When assigned to, executes callback function to update interface"""
        return self._filename

    @filename.setter
    def filename(self, value: str) -> None:
        self._filename = value
        self._filename_callback()

    @property
    def image_dir(self) -> Any:
        """Directory containing scan images.

        If unset, defaults to pngs subdir of project dir"""
        if (not self._image_dir) and self.filename:
            proj_dir = os.path.dirname(self.filename)
            maybe_dir = os.path.join(proj_dir, "pngs")
            if (not os.path.isdir(maybe_dir)) and self.project_id:
                maybe_dir = os.path.join(proj_dir, self.project_id + "_images")
            if os.path.isdir(maybe_dir):
                self._image_dir = maybe_dir
        return self._image_dir

    @image_dir.setter
    def image_dir(self, value: str) -> None:
        self._image_dir = value

    @property
    def project_id(self) -> Any:
        """ID of project, e.g. "projectID0123456789abc".

        If unset, extract from project comments filename.
        """
        if (not self._project_id) and self.filename:
            proj_dir = os.path.dirname(self.filename)
            pathlist = Path(proj_dir).glob("projectID*_comments.html")
            for path in pathlist:
                path_str = str(path)
                if match := re.search(r"(projectID[0-9a-f]*)_comments\.html", path_str):
                    self._project_id = match[1]
                    break
        return self._project_id

    @project_id.setter
    def project_id(self, value: str) -> None:
        self._project_id = value

    @property
    def languages(self) -> Any:
        """Languages of currently loaded file.

        Multiple languages are separated by "+"
        When assigned to, executes callback function to update interface"""
        return self._languages

    @languages.setter
    def languages(self, value: str) -> None:
        value = value.strip()
        if not value:
            value = self._languages if self._languages else "en"
        if re.fullmatch(r"[a-z_]+(\+[a-z_]+)*", value) is None:
            logger.error("Invalid language code(s): valid examples are 'en' or 'de fr'")
            return
        self._languages = value
        self._languages_callback()
        preferences.set(PrefKey.DEFAULT_LANGUAGES, self._languages)
        # Inform maintext, so text manipulation algorithms there can check languages
        maintext().set_languages(self._languages)

    def reset(self) -> None:
        """Reset file internals to defaults, e.g. filename, page markers, etc.

        Also close any open dialogs, since they will refer to the previous file."""
        self.filename = ""
        self.image_dir = ""
        self.project_id = ""
        self.remove_page_marks()
        self.page_details = PageDetails()
        ToplevelDialog.close_all()

    def open_file(self, filename: str = "") -> str:
        """Open and load a text file.

        Args:
            Optional filename - if not given, ask user to select file
        Returns:
            Name of file opened - empty string if cancelled.
        """
        if self.check_save():
            if not filename:
                filename = filedialog.askopenfilename(
                    filetypes=(
                        ("Text files", "*.txt *.html *.htm"),
                        ("All files", "*.*"),
                    )
                )
            if filename:
                self.load_file(filename)
            grab_focus(root(), maintext())
        return filename

    def close_file(self) -> None:
        """Close current file, leaving an empty file."""
        if self.check_save():
            self.reset()
            maintext().do_close()

    def load_file(self, filename: str) -> None:
        """Load file & bin file.

        Args:
            filename: Name of file to be loaded. Bin filename has ".bin" appended.
        """
        self.reset()
        try:
            maintext().do_open(filename)
        except FileNotFoundError:
            logger.error(f"Unable to open {filename}")
            self.remove_recent_file(filename)
            self.filename = ""
            return
        maintext().set_insert_index(IndexRowCol(1, 0))
        self.languages = preferences.get(PrefKey.DEFAULT_LANGUAGES)
        bin_matches_file = self.load_bin(filename)
        self.mark_page_boundaries()
        flags_found = (
            self.update_page_marks_from_flags() or self.update_page_marks_from_ppgen()
        )
        if not bin_matches_file:
            # Inform user that bin doesn't match
            # If flags are present, user has used page marker flags to store the
            # page boundary positions while using other editor, so only issue a
            # warning. Otherwise, other failure, so it's an error.
            msg_start = (
                "Main file and bin (.json) file do not match.\n"
                "  File may have been edited in a different editor.\n"
            )
            if flags_found:
                logger.warning(
                    msg_start + "  However, page marker flags were detected,\n"
                    "  so page boundary positions were updated if necessary."
                )
            else:
                logger.error(
                    msg_start + "  You may continue, but page boundary positions\n"
                    "  may not be accurate."
                )

        self.page_details.recalculate()
        self.store_recent_file(filename)
        self.project_dict.load(filename)
        # Load complete, so set filename (including side effects)
        self.filename = filename

    def save_file(self) -> str:
        """Save the current file.

        Returns:
            Current filename or None if save is cancelled
        """
        if self.filename:
            maintext().do_save(self.filename)
            self.save_bin(self.filename)
            return self.filename
        return self.save_as_file()

    def save_as_file(self) -> str:
        """Save current text as new file.

        Returns:
            Chosen filename or None if save is cancelled
        """
        # If no current extension, or ".txt" extension, set extension to ".html"
        # if it's an HTML file. Similarly for text files, otherwise leave alone.
        cur_ext = os.path.splitext(self.filename)[1]
        html_file = maintext().get("1.0", "1.end") == "<!DOCTYPE html>"
        if html_file and cur_ext in ("", ".txt", ".html"):
            extension = ".html"
        elif not html_file and cur_ext in ("", ".txt"):
            extension = ".txt"
        else:
            extension = cur_ext
        match extension:
            case ".html":
                file_type = "HTML files"
            case ".txt":
                file_type = "Text files"
            case _:
                file_type = f"{extension} files"
        suggested_fn = os.path.splitext(self.filename)[0] + extension

        initialfile = (
            os.path.basename(suggested_fn) if self.filename else f"untitled{extension}"
        )
        if fn := filedialog.asksaveasfilename(
            initialfile=initialfile,
            initialdir=os.path.dirname(suggested_fn),
            filetypes=[(file_type, f"*{extension}"), ("All files", "*")],
            title="Save As",
        ):
            self.store_recent_file(fn)
            self.filename = fn
            self.save_file()
        grab_focus(root(), maintext())
        return fn

    def save_copy_as_file(self) -> None:
        """Save copy of current text as new file, without affecting
        current filename or "modified" flag."""
        if fn := filedialog.asksaveasfilename(
            initialfile=os.path.basename(self.filename),
            initialdir=os.path.dirname(self.filename),
            filetypes=[("All files", "*")],
            title="Save a Copy As",
        ):
            self.save_copy(fn)
        grab_focus(root(), maintext())

    def save_copy(self, filename: str) -> None:
        """Save copy of current text as new file, without affecting
        current filename or "modified" flag.

        Args:
            filename: Name to save file as.
        """
        self.store_recent_file(filename)
        self.store_recent_file(self.filename)
        maintext().do_save(filename, clear_modified_flag=False)
        self.save_bin(filename)

    def check_save(self) -> bool:
        """If file has been edited, check if user wants to save,
        or discard, or cancel the intended operation.

        Returns:
            True if OK to continue with intended operation.
        """
        # Good(?) place to ensure image position gets saved
        mainimage().handle_configure(tk.Event())

        if not maintext().is_modified():
            return True

        save = messagebox.askyesnocancel(
            title="Save document?",
            message="Save changes to document first?",
            detail="Your changes will be lost if you don't save them.",
            icon=messagebox.WARNING,
        )
        # Trap Cancel from messagebox
        if save is None:
            return False
        # Trap Cancel from save-as dialog
        if save and not self.save_file():
            return False
        return True

    def load_bin(self, basename: str) -> bool:
        """Load bin file associated with current file.

        If bin file not found, returns silently.

        Args:
            basename - name of current text file - bin file has ".bin" appended.

        Returns:
            True if no bin, or bin matches main file; False otherwise
        """
        bin_dict = load_dict_from_json(bin_name(basename))
        if bin_dict is None:
            return True
        return self.interpret_bin(bin_dict)  # type: ignore[arg-type]

    def save_bin(self, basename: str) -> None:
        """Save bin file associated with current file.

        Args:
            basename: name of current text file - bin file has ".bin" appended.
        """
        binfile_name = bin_name(basename)
        bin_dict = self.create_bin()
        with open(binfile_name, "w", encoding="utf-8") as fp:
            json.dump(bin_dict, fp, indent=2, ensure_ascii=False)

    def interpret_bin(self, bin_dict: BinDict) -> bool:
        """Interpret bin file dictionary and set necessary variables, etc.

        Args:
            bin_dict: Dictionary loaded from bin file.

        Returns:
            True if main file and text file match, False otherwise.
        """
        self.set_initial_position(bin_dict.get(BINFILE_KEY_INSERTPOS), maintext())
        self.set_initial_position(
            bin_dict.get(BINFILE_KEY_INSERTPOSPEER), maintext().peer
        )
        # Since object loaded from bin file is a dictionary of dictionaries,
        # need to create PageDetails from the loaded raw data.
        if page_details := bin_dict.get(BINFILE_KEY_PAGEDETAILS):
            for img, detail in page_details.items():
                self.page_details[img] = PageDetail(
                    detail["index"], detail["style"], detail["number"]
                )
        self.set_page_marks(self.page_details)
        self.image_dir = bin_dict.get(BINFILE_KEY_IMAGEDIR)
        self.project_id = bin_dict.get(BINFILE_KEY_PROJECTID)
        self.languages = bin_dict.get(BINFILE_KEY_LANGUAGES)
        bookmarks: Optional[dict[str, str]]
        if bookmarks := bin_dict.get(BINFILE_KEY_BOOKMARKS):
            for key, value in bookmarks.items():
                self.set_bookmark_index(key, value)

        md5checksum = bin_dict.get(BINFILE_KEY_MD5CHECKSUM)
        return not md5checksum or md5checksum == self.get_md5_checksum()

    def create_bin(self) -> BinDict:
        """From relevant variables, etc., create dictionary suitable for saving
        to bin file.

        Returns:
            Dictionary of settings to be saved in bin file
        """
        self.update_page_marks(self.page_details)
        bin_dict: BinDict = {
            BINFILE_KEY_MD5CHECKSUM: self.get_md5_checksum(),
            BINFILE_KEY_INSERTPOS: maintext().index(tk.INSERT),
            BINFILE_KEY_INSERTPOSPEER: maintext().peer.index(tk.INSERT),
            BINFILE_KEY_PAGEDETAILS: self.page_details,
            BINFILE_KEY_IMAGEDIR: self.image_dir,
            BINFILE_KEY_PROJECTID: self.project_id,
            BINFILE_KEY_LANGUAGES: self.languages,
            BINFILE_KEY_BOOKMARKS: self.get_bookmarks(),
        }
        return bin_dict

    def get_md5_checksum(self) -> str:
        """Get checksum from maintext file contents.

        Returns:
            MD5 checksum
        """
        return hashlib.md5(maintext().get_text().encode()).hexdigest()

    def store_recent_file(self, filename: str) -> None:
        """Store given filename in list of recent files.

        Args:
            filename: Name of new file to add to list.
        """
        self.remove_recent_file(filename)
        recents = preferences.get(PrefKey.RECENT_FILES)
        recents.insert(0, filename)
        del recents[NUM_RECENT_FILES:]
        preferences.set(PrefKey.RECENT_FILES, recents)
        self._filename_callback()

    def remove_recent_file(self, filename: str) -> None:
        """Remove given filename from list of recent files.

        Args:
            filename: Name of new file to add to list.
        """
        recents = preferences.get(PrefKey.RECENT_FILES)
        if filename in recents:
            recents.remove(filename)
            preferences.set(PrefKey.RECENT_FILES, recents)
        self._filename_callback()

    def set_page_marks(self, page_details: PageDetails) -> None:
        """Set page marks from keys/values in dictionary.

        Args:
            page_details: Dictionary of page details, including indexes.
        """
        self.remove_page_marks()
        if page_details:
            for img, detail in page_details.items():
                mark = page_mark_from_img(img)
                maintext().set_mark_position(mark, IndexRowCol(detail["index"]))

    def set_initial_position(self, index: str | None, widget: tk.Text) -> None:
        """Set initial cursor position after file is loaded.

        Args:
            index: Location for insert cursor. If none, go to start.
            widget: The widget to set insert cursor for.
        """
        if not index:
            index = "1.0"
        maintext().set_insert_index(IndexRowCol(index), focus_widget=widget)

    def update_page_marks(self, page_details: PageDetails) -> None:
        """Update page mark locations in page details structure.

        Args:
            page_details: Dictionary of page details, including indexes.
        """
        mark = "1.0"
        while mark := maintext().page_mark_next(mark):
            img = img_from_page_mark(mark)
            assert img in page_details
            page_details[img]["index"] = maintext().index(mark)

    def mark_page_boundaries(self) -> None:
        """Loop through whole file, ensuring all page separator lines
        are in standard format, and setting page marks at the
        start of each page separator line.
        """
        if not self.contains_page_marks():
            self.page_details = PageDetails()
        page_num_style = STYLE_ARABIC
        page_num = "1"

        pattern = re.compile(PAGE_SEPARATOR_REGEX)
        search_start = "1.0"
        while page_index := maintext().search(
            PAGE_SEPARATOR_REGEX, search_start, regexp=True, stopindex="end"
        ):
            line_start = maintext().index(page_index + " linestart")
            line_end = page_index + " lineend"
            line = maintext().get(line_start, line_end)
            # Always matches since same regex as earlier search
            if match := pattern.search(line):
                (page, ext) = match.group(1, 2)
                standard_line = f"-----File: {page}.{ext}"
                standard_line += "-" * (75 - len(standard_line))
                # Don't standarize line if it has a page marker flag on it, or you'll delete the flag!
                if line != standard_line and not re.search(PAGE_FLAG_REGEX, line):
                    maintext().delete(line_start, line_end)
                    maintext().insert(line_start, standard_line)
                page_mark = page_mark_from_img(page)
                maintext().set_mark_position(page_mark, IndexRowCol(line_start))
                try:
                    self.page_details[page]["index"] = line_start
                except KeyError:
                    self.page_details[page] = PageDetail(
                        line_start, page_num_style, page_num
                    )
                    page_num_style = STYLE_DITTO
                    page_num = NUMBER_INCREMENT

            search_start = line_end

    def remove_page_marks(self) -> None:
        """Remove any existing page marks."""
        marklist = []
        mark = "1.0"
        while mark := maintext().page_mark_next(mark):
            marklist.append(mark)
        for mark in marklist:
            maintext().mark_unset(mark)

    def contains_page_marks(self) -> bool:
        """Check whether file contains page marks.

        Returns:
            True if file contains page marks.
        """
        return maintext().page_mark_next("1.0") != ""

    def get_current_image_path(self) -> str:
        """Return the path of the image file for the page where the insert
        cursor is located.

        Returns:
            Name of the image file for the current page, or the empty string
            if unable to get image file name.
        """
        basename = maintext().get_current_image_name()
        if self.image_dir and basename:
            basename += ".png"
            path = os.path.join(self.image_dir, basename)
            if os.path.exists(path):
                return path
        return ""

    def choose_image_dir(self) -> None:
        """Allow user to select directory containing png image files"""
        self.image_dir = filedialog.askdirectory(
            mustexist=True, title=f"Select {folder_dir_str(True)} containing scans"
        )

    def get_current_page_label(self) -> str:
        """Find page label corresponding to where the insert cursor is.

        Returns:
            Page label of current page. Empty string if none found.
        """
        img = maintext().get_current_image_name()
        if img == "":
            return ""
        return self.page_details[img]["label"]

    def set_languages(self) -> None:
        """Allow the user to set languages.

        Multiple languages may be separated by any non-word character
        which will be converted to "+"
        """
        languages = simpledialog.askstring(
            "Set Language(s)", "Language(s)", parent=maintext()
        )
        if languages:
            lang_list = re.split(r"\W", languages)
            lang_list2 = [lang for lang in lang_list if lang]
            self.languages = "+".join(lang_list2)
            maintext().set_modified(True)  # Because bin file needs saving

    def goto_line(self) -> None:
        """Go to the line number the user enters"""
        line_num = simpledialog.askinteger(
            "Go To Line", "Line number", parent=maintext()
        )
        if line_num is not None:
            maintext().set_insert_index(IndexRowCol(line_num, 0))

    def goto_image(self) -> None:
        """Go to the image the user enters"""
        image_num = simpledialog.askstring(
            "Go To Page (png)", "Image number", parent=maintext()
        )
        if image_num is not None:
            image_num = re.sub(r"\.png$", "", image_num)
            self.do_goto_image(image_num)

    def do_goto_image(self, image_num: Optional[str]) -> None:
        """Go to page corresponding to the given image number.

        Args:
            image_num: Number of image to go to, e.g. "001" (can omit leading zeroes)
        """
        if image_num is None:
            return
        for n_zero in range(5):
            prefix = "0" * n_zero
            try:
                index = maintext().rowcol(page_mark_from_img(prefix + image_num))
            except tk.TclError:
                # Bad image number
                continue
            maintext().set_insert_index(index)
            return
        sound_bell()

    def goto_page(self) -> None:
        """Go to the page the user enters."""
        page_num = simpledialog.askstring(
            "Go To Page Label", "Page label", parent=maintext()
        )
        if page_num is not None:
            # If user put "Pg" before label remove it, so correctly formatted prefix can be added
            page_num = re.sub(r"^ *(Pg)? *", "", page_num)
            image_num = self.page_details.png_from_label(PAGE_LABEL_PREFIX + page_num)
            self.do_goto_image(image_num)

    def prev_page(self) -> None:
        """Go to the start of the previous page"""
        self._next_prev_page(-1)

    def next_page(self) -> None:
        """Go to the start of the next page"""
        self._next_prev_page(1)

    def _next_prev_page(self, direction: Literal[1, -1]) -> None:
        """Go to the page before/after the current one

        Always moves backward/forward in file, even if cursor and page mark(s)
        are coincident or multiple coincident page marks. Will not remain in
        the same location unless no further page marks are found.

        Args:
            direction: +1 to go to next page; -1 for previous page
        """
        insert = maintext().get_insert_index().index()
        cur_page = maintext().get_current_image_name()
        mark = page_mark_from_img(cur_page) if cur_page else insert
        while mark := maintext().page_mark_next_previous(mark, direction):
            if maintext().compare(mark, "!=", insert):
                maintext().set_insert_index(maintext().rowcol(mark))
                return
        sound_bell()

    # Note that the following code must match the equivalent code in Guiguts 1
    # or file transfer between the two will be broken.
    def add_page_flags(self) -> None:
        """Add page flag at each page boundary.

        Done in reverse order so two adjacent boundaries preserve their order.
        """
        mark: str = tk.END
        while mark := maintext().page_mark_previous(mark):
            img = img_from_page_mark(mark)
            style = self.page_details[img]["style"]
            number = self.page_details[img]["number"]
            info = PAGE_FLAG_SEP.join([PAGE_FLAG_PREFIX + img, style, number])
            maintext().insert(
                mark,
                PAGE_FLAG_START + info + PAGE_FLAG_END,
                PAGE_FLAG_TAG,
            )

    def remove_page_flags(self) -> None:
        """Remove page flags."""
        search_range = maintext().start_to_end()
        while match := maintext().find_match(
            PAGE_FLAG_REGEX,
            search_range,
            nocase=False,
            regexp=True,
            backwards=False,
        ):
            maintext().delete(
                match.rowcol.index(), match.rowcol.index() + f"+{match.count}c"
            )
            search_range = IndexRange(match.rowcol, maintext().end())

    def update_page_marks_from_flags(self) -> bool:
        """If page marker flags in file, replace all existing page marks.

        Also tag flags to highlight them.

        Returns:
            True if page marker flags were present.
        """
        mark = "1.0"
        flag_matches = maintext().find_matches(
            PAGE_FLAG_REGEX,
            maintext().start_to_end(),
            nocase=False,
            regexp=True,
        )
        if not flag_matches:
            return False

        # Remove existing page marks, if any
        self.remove_page_marks()
        self.page_details = PageDetails()
        for match in flag_matches:
            flag_text = maintext().get_match_text(match)
            extract = re.fullmatch(PAGE_FLAG_REGEX, flag_text)
            # Will always match because same regex as above
            assert extract is not None
            img = extract[1]
            style = extract[2]
            number = extract[3]
            mark = page_mark_from_img(img)
            maintext().set_mark_position(mark, match.rowcol)
            self.page_details[img] = PageDetail(match.rowcol.index(), style, number)
            maintext().tag_add(
                PAGE_FLAG_TAG,
                match.rowcol.index(),
                match.rowcol.index() + f"+{match.count}c",
            )
        maintext().set_modified(True)
        return True

    def update_page_marks_from_ppgen(self) -> bool:
        """If `.bn` ppgen commands in file, replace all existing page marks.

        Command is of the form: `.bn a001.png // optional comment`
        We only want `a001`

        Returns:
            True if `.bn` commands were present.
        """
        regex = r"^\.bn +(.+?)\.png"
        flag_matches = maintext().find_matches(
            regex,
            maintext().start_to_end(),
            nocase=False,
            regexp=True,
        )
        if not flag_matches:
            return False

        # Remove existing page marks, if any
        self.remove_page_marks()
        self.page_details = PageDetails()
        # First page is Arabic, page 1
        style = STYLE_ARABIC
        number = "1"
        for match in flag_matches:
            bn_text = maintext().get_match_text(match)
            extract = re.fullmatch(regex, bn_text)
            # Will always match because same regex as above
            assert extract is not None
            img = extract[1]
            # Set mark and store page info
            maintext().set_mark_position(page_mark_from_img(img), match.rowcol)
            self.page_details[img] = PageDetail(match.rowcol.index(), style, number)
            # Subsequent pages are same style as first, page numbers increment
            style = STYLE_DITTO
            number = NUMBER_INCREMENT
        maintext().set_modified(True)
        return True

    def add_good_and_bad_words(self) -> None:
        """Load the words from the good and bad words files into the project dictionary."""

        gw_load = self.project_dict.add_good_bad_words(
            self.filename, load_good_words=True
        )
        bw_load = self.project_dict.add_good_bad_words(
            self.filename, load_good_words=False
        )
        if gw_load or bw_load:
            self.project_dict.save(self.filename)
        else:
            logger.error(
                f"Neither {GOOD_WORDS_FILENAME} nor {BAD_WORDS_FILENAME} was found"
            )

    def add_good_word_to_project_dictionary(self, word: str) -> None:
        """Add a good word to the project dictionary. Save if word was added.

        Args:
            word: The word to be added.
        """
        if self.project_dict.add_good_word(word):
            self.project_dict.save(self.filename)

    def add_good_word_to_global_dictionary(self, word: str) -> None:
        """Add a good word to the user global dictionary for the current language.

        Args:
            word: The word to be added.
        """
        main_lang = maintext().get_language_list()[0]
        path = Path(preferences.prefsdir, f"dict_{main_lang}_user.txt")
        with path.open("a", encoding="utf-8") as fp:
            fp.write(f"{word}\n")

    def rewrap_selection(self) -> None:
        """Wrap selected text."""
        ranges = maintext().selected_ranges()
        if not ranges:
            sound_bell()
            return
        # Adjust selection to begin & end at start of lines
        ranges[0].start.col = min(ranges[0].start.col, 0)
        if ranges[0].end.col > 0:
            ranges[0].end.col = 0
            ranges[0].end.row += 1
        self.rewrap_section(ranges[0])

    def rewrap_all(self) -> None:
        """Wrap whole text."""
        self.rewrap_section(IndexRange("1.0", maintext().index(tk.END)))

    def rewrap_section(self, section_range: IndexRange) -> None:
        """Wrap a section of the text, preserving page mark locations.

        Args:
            section_range: Range of text to be wrapped.
        """
        maintext().selection_ranges_store_with_marks()
        maintext().undo_block_begin()

        # Dummy insert & delete, so that if user undoes the wrap, the insert
        # cursor returns to its previous point, not the first pin page mark position.
        insert_index = maintext().get_insert_index()
        maintext().insert(insert_index.index(), " ")
        maintext().delete(insert_index.index())

        maintext().strip_end_of_line_spaces()
        mark_list = self.pin_page_marks()
        maintext().rewrap_section(
            section_range, lambda: self.unpin_page_marks(mark_list)
        )
        maintext().selection_ranges_restore_from_marks()

    def rewrap_cleanup(self) -> None:
        """Clean up all rewrap markers."""
        match_regex = r"^/[#$*FILPXCR]|^[#$*FILPXCR]/"
        search_range = maintext().start_to_end()
        maintext().undo_block_begin()
        while beg_match := maintext().find_match(
            match_regex, search_range, regexp=True, nocase=True
        ):
            # If a start rewrap is followed immediately by a blank line
            # then another start rewrap, delete the blank line as well.
            beg_idx = beg_match.rowcol.index()
            is_start = maintext().get(beg_idx) == "/"
            test_txt = maintext().get(f"{beg_idx} +1l", f"{beg_idx} +2l lineend")
            nlines = 2 if is_start and re.match(r"\n/[#$*FILPXCR]", test_txt) else 1

            maintext().delete(beg_idx, f"{beg_idx} +{nlines}l")
            search_range = IndexRange(beg_match.rowcol, maintext().end())

    def pin_page_marks(self) -> list[str]:
        """Pin the page marks to locations in the text, by inserting a special
        character at each page mark.

        This allows operations such as rewrapping to happen without losing
        page mark positions. Marks are unpinned by `unpin_page_marks`.

        Returns:
            List of page mark names in reverse order to pass to `unpin_page_marks`.
        """
        # Ensure no pins left around from previous wraps - should never happen,
        # but if it has, we want to clear them.
        found: str = tk.END
        while found := maintext().search(PAGEMARK_PIN, found, backwards=True):
            maintext().delete(found, f"{found} +1c")

        mark_list = []
        mark: str = tk.END
        while mark := maintext().page_mark_previous(mark):
            mark_list.append(mark)
            maintext().insert(mark, PAGEMARK_PIN)
        return mark_list

    def unpin_page_marks(self, mark_list: list[str]) -> None:
        """Unpin the page marks by setting them to the locations of the special
        characters inserted by `pin_page_marks`.

        Args:
            mark_list: List of page mark names in reverse order from `pin_page_marks`.
        """
        # First set each page mark to the pinned location - this avoids setting two
        # page marks adjacent to one another (due to the pin character) because their
        # order isn't guaranteed if you do that, so they can end up swapped.
        found = "1.0"
        while found := maintext().search(PAGEMARK_PIN, found, "end"):
            maintext().mark_set(mark_list.pop(), found)
            found = f"{found} +1c"
        # Now it's safe to remove the pins, in reverse order to simplify loop
        found = tk.END
        while found := maintext().search(PAGEMARK_PIN, found, backwards=True):
            maintext().delete(found, f"{found} +1c")

    def set_bookmark_index(self, bm_name: str, idx: str) -> None:
        """Set the position of a bookmark via name & index string.

        Args:
            bm_name: Name of bookmark to set (assumed valid).
            idx: Index of bookmark position in file.
        """
        maintext().set_mark_position(bm_name, maintext().rowcol(idx))

    def set_bookmark(self, bm_num: int) -> None:
        """Set the position of the given bookmark at the current insert location.

        Args:
            bm_num: Number of bookmark to add (must be 1 to 5).
        """
        assert 1 <= bm_num <= 5
        maintext().set_mark_position(
            f"{BOOKMARK_BASE}{bm_num}", maintext().get_insert_index()
        )
        self.highlight_bookmark(bm_num)

    def goto_bookmark(self, bm_num: int) -> None:
        """Set the insert position to the location of the given bookmark.

        Args:
            bm_num: Number of bookmark to find (must be 1 to 5).
        """
        assert 1 <= bm_num <= 5
        try:
            maintext().set_insert_index(
                maintext().rowcol(f"{BOOKMARK_BASE}{bm_num}"), focus=False
            )
        except tk.TclError:
            sound_bell()  # Bookmark hasn't been set
            return
        self.highlight_bookmark(bm_num)

    def highlight_bookmark(self, bm_num: int) -> None:
        """Temporarily highlight bookmark position.

        Args:
            bm_num: Number of bookmark to find (must be 1 to 5).
        """
        assert 1 <= bm_num <= 5
        maintext().tag_add(BOOKMARK_TAG, maintext().index(f"{BOOKMARK_BASE}{bm_num}"))
        maintext().after(1000, self.remove_bookmark_tags)

    def get_bookmarks(self) -> dict[str, str]:
        """Get dictionary of bookmark names:locations.

        Assumes if a mark begins with `BOOKMARK_BASE` it's a bookmark.
        """
        bookmarks: dict[str, str] = {}
        for name in maintext().mark_names():
            if name.startswith(BOOKMARK_BASE):
                assert re.fullmatch(f"{BOOKMARK_BASE}[1-5]", name)
                bookmarks[name] = maintext().index(name)
        return bookmarks

    def remove_bookmark_tags(self) -> None:
        """Remove all bookmark highlightling."""
        maintext().tag_remove(BOOKMARK_TAG, "1.0", tk.END)


def bin_name(basename: str) -> str:
    """Get the name of the bin file associated with a text file.

    Args:
        basename: Name of text file.

    Returns:
        Name of associated bin file.
    """
    return basename + BINFILE_SUFFIX


# For convenient access, store the single File instance here,
# with a function to set/query it.
_the_file = None  # pylint: disable=invalid-name


def the_file(file: Optional[File] = None) -> File:
    """Store and return the single File widget"""
    global _the_file
    if file is not None:
        assert _the_file is None
        _the_file = file
    assert _the_file is not None
    return _the_file
