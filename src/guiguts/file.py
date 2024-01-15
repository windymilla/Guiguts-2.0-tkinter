"""Handle file operations"""

import hashlib
import json
import logging
import os.path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable, Final, TypedDict, Literal, Optional

from guiguts.mainwindow import maintext, sound_bell
import guiguts.page_details as page_details
from guiguts.page_details import PageDetail, PageDetails, PAGE_LABEL_PREFIX
from guiguts.preferences import preferences
from guiguts.utilities import is_windows, load_dict_from_json

logger = logging.getLogger(__package__)

FOLDER_DIR = "folder" if is_windows() else "directory"
NUM_RECENT_FILES = 9
PAGEMARK_PREFIX = "Pg"
BINFILE_SUFFIX = ".json"

BINFILE_KEY_MD5CHECKSUM: Final = "md5checksum"
BINFILE_KEY_PAGEDETAILS: Final = "pagedetails"
BINFILE_KEY_INSERTPOS: Final = "insertpos"
BINFILE_KEY_IMAGEDIR: Final = "imagedir"


class BinDict(TypedDict):
    md5checksum: str
    pagedetails: PageDetails
    insertpos: str
    imagedir: str


class File:
    """Handle data and actions relating to the main text file.

    Attributes:
        _filename: Current filename.
        _filename_callback: Function to be called whenever filename is set.
        _image_dir: Directory containing scan images.
    """

    def __init__(self, filename_callback: Callable[[], None]):
        """
        Args:
            filename_callback: Function to be called whenever filename is set.
        """
        self._filename = ""
        self._filename_callback = filename_callback
        self._image_dir = ""
        self.page_details = PageDetails()

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
            self._image_dir = os.path.join(os.path.dirname(self.filename), "pngs")
        return self._image_dir

    @image_dir.setter
    def image_dir(self, value: str) -> None:
        self._image_dir = value

    def reset(self) -> None:
        """Reset file internals to defaults, e.g. filename, page markers, etc"""
        self.filename = ""
        self.image_dir = ""
        self.remove_page_marks()
        self.page_details = PageDetails()

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
        maintext().set_insert_index("1.0", see=True)
        self.load_bin(filename)
        if not self.contains_page_marks():
            self.mark_page_boundaries()
        self.page_details.recalculate()
        self.store_recent_file(filename)
        # Load complete, so set filename (including side effects)
        self.filename = filename

    def save_file(self, *args: Any) -> str:
        """Save the current file.

        Returns:
            Current filename or None if save is cancelled
        """
        if self.filename:
            maintext().do_save(self.filename)
            maintext().set_modified(False)
            self.save_bin(self.filename)
            return self.filename
        else:
            return self.save_as_file()

    def save_as_file(self, *args: Any) -> str:
        """Save current text as new file.

        Returns:
            Chosen filename or None if save is cancelled
        """
        if fn := filedialog.asksaveasfilename(
            initialfile=os.path.basename(self.filename),
            initialdir=os.path.dirname(self.filename),
            filetypes=[("All files", "*")],
        ):
            self.store_recent_file(fn)
            self.filename = fn
            maintext().do_save(fn)
            maintext().set_modified(False)
            self.save_bin(fn)
        return fn

    def check_save(self) -> bool:
        """If file has been edited, check if user wants to save,
        or discard, or cancel the intended operation.

        Returns:
            True if OK to continue with intended operation.
        """
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

    def load_bin(self, basename: str) -> None:
        """Load bin file associated with current file.

        If bin file not found, returns silently.

        Args:
            basename - name of current text file - bin file has ".bin" appended.
        """
        bin_dict = load_dict_from_json(bin_name(basename))
        if bin_dict is not None:
            self.interpret_bin(bin_dict)  # type: ignore[arg-type]

    def save_bin(self, basename: str) -> None:
        """Save bin file associated with current file.

        Args:
            basename: name of current text file - bin file has ".bin" appended.
        """
        binfile_name = bin_name(basename)
        bin_dict = self.create_bin()
        with open(binfile_name, "w") as fp:
            json.dump(bin_dict, fp, indent=2)

    def interpret_bin(self, bin_dict: BinDict) -> None:
        """Interpret bin file dictionary and set necessary variables, etc.

        Args:
            bin_dict: Dictionary loaded from bin file
        """
        md5checksum = bin_dict.get(BINFILE_KEY_MD5CHECKSUM)
        if md5checksum and md5checksum != self.get_md5_checksum():
            logger.error(
                "Main file and bin (.json) file do not match.\n"
                "  File may have been edited in a different editor.\n"
                "  You may continue, but page boundary positions\n"
                "  may not be accurate."
            )
        self.set_initial_position(bin_dict.get(BINFILE_KEY_INSERTPOS))
        # Since object loaded from bin file is a dictionary of dictionaries,
        # need to create PageDetails from the loaded raw data.
        if page_details := bin_dict.get(BINFILE_KEY_PAGEDETAILS):
            for img, detail in page_details.items():
                self.page_details[img] = PageDetail(
                    detail["index"], detail["style"], detail["number"]
                )
        self.set_page_marks(self.page_details)
        self.image_dir = bin_dict.get(BINFILE_KEY_IMAGEDIR)

    def create_bin(self) -> BinDict:
        """From relevant variables, etc., create dictionary suitable for saving
        to bin file.

        Returns:
            Dictionary of settings to be saved in bin file
        """
        self.update_page_marks(self.page_details)
        bin_dict: BinDict = {
            BINFILE_KEY_MD5CHECKSUM: self.get_md5_checksum(),
            BINFILE_KEY_INSERTPOS: maintext().get_insert_index(),
            BINFILE_KEY_PAGEDETAILS: self.page_details,
            BINFILE_KEY_IMAGEDIR: self.image_dir,
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
        recents = preferences.get("RecentFiles")
        recents.insert(0, filename)
        del recents[NUM_RECENT_FILES:]
        preferences.set("RecentFiles", recents)

    def remove_recent_file(self, filename: str) -> None:
        """Remove given filename from list of recent files.

        Args:
            filename: Name of new file to add to list.
        """
        recents = preferences.get("RecentFiles")
        if filename in recents:
            recents.remove(filename)
            preferences.set("RecentFiles", recents)

    def set_page_marks(self, page_details: PageDetails) -> None:
        """Set page marks from keys/values in dictionary.

        Args:
            page_details: Dictionary of page details, including indexes.
        """
        self.remove_page_marks()
        if page_details:
            for img, detail in page_details.items():
                mark = page_mark_from_img(img)
                maintext().mark_set(mark, detail["index"])
                maintext().mark_gravity(mark, tk.LEFT)

    def set_initial_position(self, index: str | None) -> None:
        """Set initial cursor position after file is loaded.

        Args:
            index: Location for insert cursor. If none, go to start.
        """
        if not index:
            index = "1.0"
        maintext().set_insert_index(index, see=True)

    def update_page_marks(self, page_details: PageDetails) -> None:
        """Update page mark locations in page details structure.

        Args:
            page_details: Dictionary of page details, including indexes.
        """
        mark = "1.0"
        while mark := page_mark_next(mark):
            img = img_from_page_mark(mark)
            assert img in page_details
            page_details[img]["index"] = maintext().index(mark)

    def mark_page_boundaries(self) -> None:
        """Loop through whole file, ensuring all page separator lines
        are in standard format, and setting page marks at the
        start of each page separator line.
        """

        self.page_details = PageDetails()
        page_num_style = page_details.STYLE_ARABIC
        page_num = "1"

        page_separator_regex = r"File:.+?([^/\\ ]+)\.(png|jpg)"
        pattern = re.compile(page_separator_regex)
        search_start = "1.0"
        while page_index := maintext().search(
            page_separator_regex, search_start, regexp=True, stopindex="end"
        ):
            line_start = maintext().index(page_index + " linestart")
            line_end = page_index + " lineend"
            line = maintext().get(line_start, line_end)
            # Always matches since same regex as earlier search
            if match := pattern.search(line):
                (page, ext) = match.group(1, 2)
                standard_line = f"-----File: {page}.{ext}"
                standard_line += "-" * (75 - len(standard_line))
                if line != standard_line:
                    maintext().delete(line_start, line_end)
                    maintext().insert(line_start, standard_line)
                page_mark = page_mark_from_img(page)
                maintext().mark_set(page_mark, line_start)
                maintext().mark_gravity(page_mark, tk.LEFT)
                self.page_details[page] = PageDetail(
                    line_start, page_num_style, page_num
                )
                page_num_style = page_details.STYLE_DITTO
                page_num = "+1"

            search_start = line_end

    def remove_page_marks(self) -> None:
        """Remove any existing page marks."""
        marklist = []
        mark = "1.0"
        while mark := page_mark_next(mark):
            marklist.append(mark)
        for mark in marklist:
            maintext().mark_unset(mark)

    def contains_page_marks(self) -> bool:
        """Check whether file contains page marks.

        Returns:
            True if file contains page marks.
        """
        return page_mark_next("1.0") != ""

    def get_current_page_mark(self) -> str:
        """Find page mark corresponding to where the insert cursor is.

        Returns:
            Name of preceding mark. Empty string if none found.
        """
        insert = maintext().get_insert_index()
        mark = insert
        good_mark = ""
        # First check for page marks at the current cursor position & return last one
        while (mark := page_mark_next(mark)) and maintext().compare(mark, "==", insert):
            good_mark = mark
        # If not, then find page mark before current position
        if not good_mark:
            if mark := page_mark_previous(insert):
                good_mark = mark
        # If not, then maybe we're before the first page mark, so search forward
        if not good_mark:
            if mark := page_mark_next(insert):
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
        else:
            return img_from_page_mark(mark)

    def get_current_image_path(self) -> str:
        """Return the path of the image file for the page where the insert
        cursor is located.

        Returns:
            Name of the image file for the current page, or the empty string
            if unable to get image file name.
        """
        basename = self.get_current_image_name()
        if self.image_dir and basename:
            basename += ".png"
            path = os.path.join(self.image_dir, basename)
            if os.path.exists(path):
                return path
        return ""

    def choose_image_dir(self) -> None:
        """Allow user to select directory containing png image files"""
        self.image_dir = filedialog.askdirectory(
            mustexist=True, title="Select " + FOLDER_DIR + " containing scans"
        )

    def get_current_page_label(self) -> str:
        """Find page label corresponding to where the insert cursor is.

        Returns:
            Page label of current page. Empty string if none found.
        """
        img = self.get_current_image_name()
        if img == "":
            return ""
        else:
            return self.page_details[img]["label"]

    def goto_line(self) -> None:
        """Go to the line number the user enters"""
        line_num = simpledialog.askinteger(
            "Go To Line", "Line number", parent=maintext()
        )
        if line_num is not None:
            maintext().set_insert_index(f"{line_num}.0", see=True)

    def goto_image(self) -> None:
        """Go to the image the user enters"""
        image_num = simpledialog.askstring(
            "Go To Page", "Image number", parent=maintext()
        )
        self.do_goto_image(image_num)

    def do_goto_image(self, image_num: Optional[str]) -> None:
        """Go to page corresponding to the given image number.

        Args:
            image_num: Number of image to go to, e.g. "001"
        """
        if image_num is not None:
            try:
                index = maintext().index(page_mark_from_img(image_num))
            except tk._tkinter.TclError:  # type: ignore[attr-defined]
                # Bad image number
                return
            maintext().set_insert_index(index, see=True)

    def goto_page(self) -> None:
        """Go to the page the user enters."""
        page_num = simpledialog.askstring(
            "Go To Page", "Page number", parent=maintext()
        )
        if page_num is not None:
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
        insert = maintext().get_insert_index()
        cur_page = self.get_current_image_name()
        mark = page_mark_from_img(cur_page) if cur_page else insert
        while mark := page_mark_next_previous(mark, direction):
            if maintext().compare(mark, "!=", insert):
                maintext().set_insert_index(mark, see=True)
                return
        sound_bell()


def page_mark_previous(mark: str) -> str:
    """Return page mark previous to given one, or empty string if none."""
    return page_mark_next_previous(mark, -1)


def page_mark_next(mark: str) -> str:
    """Return page mark after given one, or empty string if none."""
    return page_mark_next_previous(mark, 1)


def page_mark_next_previous(mark: str, direction: Literal[1, -1]) -> str:
    """Return page mark before/after given one, or empty string if none.

    Args:
        mark: Mark to begin search from
        direction: +1 to go to next page; -1 for previous page
    """
    if direction < 0:
        mark_next_previous = maintext().mark_previous
    else:
        mark_next_previous = maintext().mark_next
    while mark := mark_next_previous(mark):  # type: ignore[assignment]
        if is_page_mark(mark):
            return mark
    return ""


def is_page_mark(mark: str) -> bool:
    """Check whether mark is a page mark, e.g. "Pg027".

    Args:
        mark: String containing name of mark to be checked.

    Returns:
        True if string matches the format of page mark names.
    """
    return mark.startswith(PAGEMARK_PREFIX)


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


def bin_name(basename: str) -> str:
    """Get the name of the bin file associated with a text file.

    Args:
        basename: Name of text file.

    Returns:
        Name of associated bin file.
    """
    return basename + BINFILE_SUFFIX
