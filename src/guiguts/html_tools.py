"""Tools relating to HTML."""

import gzip
import logging
import os.path
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Optional

from PIL import Image, ImageTk, UnidentifiedImageError
import regex as re
import requests

from guiguts.checkers import CheckerDialog
from guiguts.file import the_file
from guiguts.maintext import maintext
from guiguts.preferences import (
    PersistentString,
    PersistentBoolean,
    PrefKey,
    preferences,
)
from guiguts.utilities import IndexRange, sound_bell, DiacriticRemover, IndexRowCol
from guiguts.widgets import ToplevelDialog

logger = logging.getLogger(__package__)

IMAGE_THUMB_SIZE = 200
RETURN_ARROW = "⏎"
EM_PX = 16.0  # 16 pixels per em
LAND_X = 4  # Common landscape screen ratio
LAND_Y = 3


class HTMLImageDialog(ToplevelDialog):
    """Dialog for inserting image markup into HTML."""

    manual_page = "HTML_Menu#Add_Illustrations"

    def __init__(self) -> None:
        """Initialize HTML Image dialog."""
        super().__init__(
            "HTML Images",
            resize_x=False,
            resize_y=False,
        )

        self.image: Optional[Image.Image] = None
        self.imagetk: Optional[ImageTk.PhotoImage] = None
        self.width = 0
        self.height = 0
        self.illo_range: Optional[IndexRange] = None

        # File
        file_frame = ttk.LabelFrame(self.top_frame, text="File", padding=2)
        file_frame.grid(row=0, column=0, sticky="NSEW")
        file_frame.columnconfigure(0, weight=1)
        file_name_frame = ttk.Frame(file_frame)
        file_name_frame.grid(row=0, column=0)
        file_name_frame.columnconfigure(0, weight=1)
        self.filename_textvariable = tk.StringVar(self, "")
        self.fn_entry = ttk.Entry(
            file_name_frame,
            textvariable=self.filename_textvariable,
            width=30,
        )
        self.fn_entry.grid(row=0, column=0, sticky="EW", padx=(0, 2))
        ttk.Button(
            file_name_frame,
            text="Browse...",
            command=self.choose_file,
            takefocus=False,
        ).grid(row=0, column=1, sticky="NSEW")

        # Buttons to see prev/next file
        file_btn_frame = ttk.Frame(file_frame)
        file_btn_frame.grid(row=1, column=0)
        ttk.Button(
            file_btn_frame,
            text="Prev File",
            command=lambda: self.next_file(reverse=True),
            takefocus=False,
        ).grid(row=0, column=0, padx=2)
        ttk.Button(
            file_btn_frame,
            text="Next File",
            command=lambda: self.next_file(reverse=False),
            takefocus=False,
        ).grid(row=0, column=1, padx=2)

        # Label to display thumbnail of image - allocate a
        # square space the same width as the filename frame
        file_name_frame.update_idletasks()
        frame_width = file_name_frame.winfo_width()
        thumbnail_frame = ttk.LabelFrame(self.top_frame, text="Thumbnail")
        thumbnail_frame.grid(row=1, column=0, sticky="NSEW")
        thumbnail_frame.columnconfigure(0, minsize=frame_width, weight=1)
        thumbnail_frame.rowconfigure(0, minsize=frame_width, weight=1)
        self.thumbnail = ttk.Label(thumbnail_frame, justify=tk.CENTER)
        self.thumbnail.grid(row=0, column=0)
        self.thumbsize = frame_width - 10

        # Caption text
        caption_frame = ttk.LabelFrame(self.top_frame, text="Caption text", padding=2)
        caption_frame.grid(row=2, column=0, sticky="NSEW")
        caption_frame.columnconfigure(0, weight=1)
        self.caption_textvariable = tk.StringVar(self, "")
        ttk.Entry(
            caption_frame,
            textvariable=self.caption_textvariable,
        ).grid(row=0, column=0, sticky="NSEW")

        # Alt text
        alt_frame = ttk.LabelFrame(self.top_frame, text="Alt text", padding=2)
        alt_frame.grid(row=3, column=0, sticky="NSEW")
        alt_frame.columnconfigure(0, weight=1)
        self.alt_textvariable = tk.StringVar(self, "")
        ttk.Entry(
            alt_frame,
            textvariable=self.alt_textvariable,
        ).grid(row=0, column=0, sticky="NSEW")

        # Geometry
        geom_frame = ttk.LabelFrame(self.top_frame, padding=2, text="Geometry")
        geom_frame.grid(row=4, column=0, pady=(5, 0), sticky="NSEW")
        geom_frame.columnconfigure(0, weight=1)
        width_height_frame = ttk.Frame(geom_frame)
        width_height_frame.grid(row=0, column=0)
        width_height_frame.columnconfigure(0, weight=1)
        self.image_width = 0
        self.image_height = 0

        def width_updated(new_value: str) -> bool:
            """Use validation routine for width box to update height box."""
            self.height_textvariable.set(self.get_height_from_width(new_value))
            return True

        ttk.Label(width_height_frame, text="Width").grid(row=0, column=0, padx=4)
        self.width_textvariable = tk.StringVar(self, "")
        ttk.Entry(
            width_height_frame,
            textvariable=self.width_textvariable,
            width=8,
            validate="all",
            validatecommand=(self.register(width_updated), "%P"),
        ).grid(row=0, column=1, sticky="NSEW", padx=(4, 10))
        ttk.Label(width_height_frame, text="Height").grid(row=0, column=2, padx=(10, 4))
        self.height_textvariable = tk.StringVar(self, "")
        ttk.Entry(
            width_height_frame,
            textvariable=self.height_textvariable,
            width=8,
            state=tk.DISABLED,
        ).grid(row=0, column=3, sticky="NSEW", padx=4)

        unit_frame = ttk.Frame(geom_frame)
        unit_frame.grid(row=1, column=0, pady=5)
        unit_textvariable = PersistentString(PrefKey.HTML_IMAGE_UNIT)
        ttk.Radiobutton(
            unit_frame,
            text="%",
            variable=unit_textvariable,
            value="%",
            takefocus=False,
            command=self.update_geometry_fields,
        ).grid(row=0, column=4, sticky="NSEW", padx=10)
        ttk.Radiobutton(
            unit_frame,
            text="em",
            variable=unit_textvariable,
            value="em",
            takefocus=False,
            command=self.update_geometry_fields,
        ).grid(row=0, column=5, sticky="NSEW", padx=10)
        ttk.Radiobutton(
            unit_frame,
            text="px",
            variable=unit_textvariable,
            value="px",
            takefocus=False,
            command=self.update_geometry_fields,
        ).grid(row=0, column=6, sticky="NSEW", padx=10)

        self.file_info_textvariable = tk.StringVar(self, "")
        ttk.Label(geom_frame, text="", textvariable=self.file_info_textvariable).grid(
            row=2, column=0
        )
        self.max_width_textvariable = tk.StringVar(self, "")
        ttk.Label(geom_frame, text="", textvariable=self.max_width_textvariable).grid(
            row=3, column=0
        )
        self.override_checkbutton = ttk.Checkbutton(
            geom_frame,
            text="Override % with 100% in epub",
            variable=PersistentBoolean(PrefKey.HTML_IMAGE_OVERRIDE_EPUB),
            takefocus=False,
        )
        self.override_checkbutton.grid(row=4, column=0, sticky="NS")
        # Dummy placeholder label for when above checkbutton is ungridded
        ttk.Label(geom_frame, width=0, text="").grid(
            row=4, column=1, sticky="NS", pady=3
        )

        # Alignment
        align_frame = ttk.LabelFrame(self.top_frame, padding=2, text="Alignment")
        align_frame.grid(row=5, column=0, pady=(5, 0), sticky="NSEW")
        align_frame.columnconfigure(0, weight=1)
        align_frame.columnconfigure(1, weight=1)
        align_frame.columnconfigure(2, weight=1)
        align_textvariable = PersistentString(PrefKey.HTML_IMAGE_ALIGNMENT)
        ttk.Radiobutton(
            align_frame,
            text="Left",
            variable=align_textvariable,
            value="left",
            takefocus=False,
        ).grid(row=0, column=0, sticky="NSW", padx=10)
        ttk.Radiobutton(
            align_frame,
            text="Center",
            variable=align_textvariable,
            value="center",
            takefocus=False,
        ).grid(row=0, column=1, sticky="NSW", padx=10)
        ttk.Radiobutton(
            align_frame,
            text="Right",
            variable=align_textvariable,
            value="right",
            takefocus=False,
        ).grid(row=0, column=2, sticky="NSW", padx=10)

        # Buttons to Find illos and Convert to HTML
        btn_frame = ttk.Frame(self.top_frame, padding=2)
        btn_frame.grid(row=6, column=0, pady=(5, 0))
        ttk.Button(
            btn_frame,
            text="Convert to HTML",
            command=self.convert_to_html,
            takefocus=False,
            width=18,
        ).grid(row=0, column=0, sticky="NSEW", padx=2)
        ttk.Button(
            btn_frame,
            text="Find [Illustration]",
            command=self.find_illo_markup,
            takefocus=False,
            width=18,
        ).grid(row=0, column=1, sticky="NSEW", padx=2)

        self.find_illo_markup()

    def load_file(self, file_name: str) -> None:
        """Load given image file."""
        assert file_name
        file_name = os.path.normpath(file_name)
        if not os.path.isfile(file_name):
            logger.error(f"Unsuitable image file: {file_name}")
            self.clear_image()
            return

        # Display filename and load image file
        rel_file_name = os.path.relpath(
            file_name, start=os.path.dirname(the_file().filename)
        )
        # We want forward slashes in HTML file, even if Windows uses backslashes
        self.filename_textvariable.set(rel_file_name.replace("\\", "/"))
        self.fn_entry.xview_moveto(1.0)
        if self.image is not None:
            del self.image
        try:
            self.image = Image.open(file_name).convert("RGB")
        except UnidentifiedImageError:
            self.image = None
            logger.error(f"Unable to identify image file: {file_name}")
            return
        self.image_width, self.image_height = self.image.size
        self.update_geometry_fields()

        # Resize image to fit thumbnail label
        scale = min(
            self.thumbsize / self.image_width, self.thumbsize / self.image_height, 1.0
        )
        width = int(self.image_width * scale)
        height = int(self.image_height * scale)
        image = self.image.resize(
            size=(width, height), resample=Image.Resampling.LANCZOS
        )
        if self.imagetk:
            del self.imagetk
        self.imagetk = ImageTk.PhotoImage(image)
        del image
        self.thumbnail.config(image=self.imagetk)
        em_info = f"{self.image_width / EM_PX:.4f} x {self.image_height / EM_PX:.4f} em"
        px_info = f"({self.image_width} x {self.image_height} px)"
        self.file_info_textvariable.set(f"File size: {em_info} {px_info}")
        self.lift()

    def choose_file(self) -> None:
        """Allow user to choose image file."""
        if file_name := filedialog.askopenfilename(
            filetypes=(
                ("Image files", "*.jpg *.png *.gif"),
                ("All files", "*.*"),
            ),
            title="Select Image File",
            parent=self,
        ):
            self.load_file(file_name)

    def next_file(self, reverse: bool = False) -> None:
        """Load the next file alphabetically.

        Args:
            reverse: True to load previous file instead.
        """
        # If no current file, can't get "next", so make user choose one
        current_fn = self.filename_textvariable.get()
        if not current_fn:
            self.choose_file()
            return
        current_fn = os.path.join(os.path.dirname(the_file().filename), current_fn)
        # Check current directory is valid
        current_dir = os.path.dirname(current_fn)
        if not os.path.isdir(current_dir):
            logger.error(f"Image directory invalid: {current_dir}")
            return
        current_basename = os.path.basename(current_fn)
        found = False
        for fn in sorted(os.listdir(current_dir), reverse=reverse):
            # Skip non-image files by checking extension
            if os.path.splitext(fn)[1] not in (".jpg", ".gif", ".png"):
                continue
            # If found on previous time through loop, this is the file we want
            if found:
                self.load_file(os.path.join(current_dir, fn))
                return
            if fn == current_basename:
                found = True
        # Reached end of dir listing without finding next file
        sound_bell()

    def find_illo_markup(self) -> None:
        """Find first unconverted illo markup in file and
        advance to the next file."""
        self.illo_range = None
        # Find and go to start of first unconverted illo markup
        illo_match_start = maintext().find_match(
            r"(<p>)?\[Illustration",
            IndexRange(maintext().start(), maintext().end()),
            regexp=True,
        )
        if illo_match_start is None:
            sound_bell()
            return
        maintext().set_insert_index(illo_match_start.rowcol, focus=False)
        # Find end of markup and spotlight it
        illo_match_end = maintext().find_match(
            r"](</p>)?",
            IndexRange(illo_match_start.rowcol, maintext().end()),
            regexp=True,
        )
        if illo_match_end is None:
            logger.error("Unclosed [Illustration markup")
            return
        self.illo_range = IndexRange(
            illo_match_start.rowcol,
            maintext().rowcol(
                f"{illo_match_end.rowcol.index()}+{illo_match_end.count}c"
            ),
        )
        maintext().spotlight_range(self.illo_range)
        # Display caption in dialog and add <p> markup if none
        caption = maintext().get(
            self.illo_range.start.index(), self.illo_range.end.index()
        )
        caption = re.sub(r"(?<=(^<p.?>|^))\[Illustration:? ?", "", caption)
        caption = re.sub(r"\](?=(</p>$|$))", "", caption)
        # Remove simple <p> markup and replace newlines with return_arrow character
        if caption:
            caption = re.sub("</?p>", "", caption)
            caption = re.sub("^\n+", "", caption)
            caption = re.sub("\n$", "", caption)
            caption = re.sub("\n", RETURN_ARROW, caption)
        self.caption_textvariable.set(caption)
        # Clear alt text, ready for user to type in required string
        self.alt_textvariable.set("")
        self.next_file()
        self.lift()

    def convert_to_html(self) -> None:
        """Convert selected [Illustration...] markup to HTML."""
        filename = self.filename_textvariable.get()
        if self.illo_range is None or not filename:
            sound_bell()
            return
        # Get caption & add some space to prettify HTML
        caption = self.caption_textvariable.get()
        if caption:
            caption = re.sub(RETURN_ARROW, "\n    ", f"      {caption}")
            caption = f"  <figcaption>\n{caption}\n  </figcaption>\n"
        # Now alt text - escape any double quotes
        alt = self.alt_textvariable.get().replace('"', "&quot;")
        alt = f' alt="{alt}"'
        # Create a unique ID from the filename
        image_id = os.path.splitext(os.path.basename(filename))[0]
        image_id = DiacriticRemover.remove_diacritics(image_id)
        # If ID already exists in file, try suffixes "_2", "_3", etc.
        id_base = image_id
        id_suffix = 1
        whole_file = IndexRange(maintext().start(), maintext().end())
        # Loop until we find an id that is not found in the file
        while maintext().find_match(f'id="{image_id}"', whole_file):
            id_suffix += 1
            image_id = f"{id_base}_{id_suffix}"
        # Alignment
        alignment = f"fig{preferences.get(PrefKey.HTML_IMAGE_ALIGNMENT)}"
        # Set up classes & styles
        unit_type = preferences.get(PrefKey.HTML_IMAGE_UNIT)
        width = self.width_textvariable.get()
        img_size = img_class = fig_class = style = ""
        if unit_type == "px":
            img_size = f' width="{self.image_width}" height="{self.image_height}"'
            style = f' style="width: {self.image_width}px;"'
        else:
            img_class = ' class="w100"'
            fig_class = " illow"
            fig_class += "p" if unit_type == "%" else "e"
            fig_class += width.replace(".", "_")
            if unit_type == "%":  # Never want %-width image to exceed natural size
                style = f' style="max-width: {self.image_width / EM_PX}em;"'

        # Construct HTML
        html = f'<figure class="{alignment}{fig_class}" id="{image_id}"{style}>\n'
        html += f'  <img{img_class} src="{filename}"{img_size}{alt}>\n'
        html += f"{caption}</figure>"

        # Replace [Illustration...] with HTML
        maintext().undo_block_begin()
        maintext().replace(
            self.illo_range.start.index(), self.illo_range.end.index(), html
        )
        self.illo_range = None

        # Now to insert CSS at end of style block, except for px sizes
        insert_point = maintext().search("</style", "1.0", tk.END)
        if unit_type == "px" or not insert_point:
            return
        fig_class = fig_class[1:]  # Remove leading space to get classname
        class_def = f".{fig_class} {{width: {width}{unit_type};}}"
        cssdef = class_def
        # If % width and override flag set  then also add CSS to override width to 100% for epub
        if (
            unit_type == "%"
            and preferences.get(PrefKey.HTML_IMAGE_OVERRIDE_EPUB)
            and width != "100"
        ):
            cssdef += f"\n.x-ebookmaker .{fig_class} {{width: 100%;}}"
        # Add heading if there's not one already
        heading = "/* Illustration classes */"
        if not maintext().search(heading, "1.0", insert_point):
            cssdef = f"\n{heading}\n{cssdef}"
        # Only insert if definition not already in file
        if not maintext().search(class_def, "1.0", insert_point):
            maintext().insert(f"{insert_point} linestart", f"{cssdef}\n")
        # Remove spotlight
        maintext().remove_spotlights()

    def clear_image(self) -> None:
        """Clear the image and reset variables accordingly."""
        if self.image:
            del self.image
        self.image = None
        if self.imagetk:
            del self.imagetk
        self.imagetk = None
        self.thumbnail.config(image="")

    def update_geometry_fields(self) -> None:
        """Update the width and height fields with data from the image."""
        if self.image_width <= 0 or self.image_height <= 0:
            logger.error("Image file has illegal width/height")
            return
        unit_type = preferences.get(PrefKey.HTML_IMAGE_UNIT)
        match unit_type:
            case "%":
                # Percentage width for the current image such that
                # both its width and height will fit a landscape screen
                size_x = f"{min(100, int(100.0 * LAND_Y / LAND_X * self.image_width / self.image_height))}"
            case "em":
                size_x = f"{self.image_width / EM_PX:.4f}"
            case "px":
                size_x = f"{self.image_width}"
            case _:
                size_x = ""
        self.width_textvariable.set(size_x)
        self.height_textvariable.set(self.get_height_from_width(size_x))
        if unit_type == "%":
            # Tell user maximum % width such that both dimensions will fit a 4:3 screen
            self.max_width_textvariable.set(
                f"Max width to fit {LAND_X}:{LAND_Y} screen is {size_x}%"
            )
            self.override_checkbutton.grid()
        else:
            self.max_width_textvariable.set("")
            self.override_checkbutton.grid_remove()

    def get_height_from_width(self, width: str) -> str:
        """Return HTML image height as string, based on width,
        type of units, and aspect ratio of image.

        Args:
            width: HTML width of image as string.

        Return:
            String containing expected height of image (or "--")
        """
        try:
            width_fl = float(width)
        except ValueError:
            return "--"
        if self.image_width <= 0 or self.image_height <= 0:
            return "--"
        match preferences.get(PrefKey.HTML_IMAGE_UNIT):
            case "%":
                return "--"
            case "em":
                return f"{width_fl * self.image_height / self.image_width:.4f}"
            case "px":
                return f"{width_fl * self.image_height / self.image_width:.0f}"
        return "--"


def html_validator_check() -> None:
    """Validate the current HTML file."""

    class HTMLValidatorDialog(CheckerDialog):
        """Minimal class to identify dialog type so that it can exist
        simultaneously with other checker dialogs."""

        manual_page = "HTML_Menu#HTML_Validator"

    checker_dialog = HTMLValidatorDialog.show_dialog(
        "HTML Validator Results",
        rerun_command=html_validator_check,
        tooltip="\n".join(
            [
                "Left click: Select & find comment",
                "Right click: Remove comment from this list",
            ]
        ),
    )

    do_validator_check(checker_dialog)


def do_validator_check(checker_dialog: CheckerDialog) -> None:
    """Do the actual check and add messages to the dialog."""

    validator_url = "https://validator.w3.org/nu/"

    def report_exception(message: str, exc: Exception | str) -> None:
        """Report exception to user and suggest manual validation.
        Also call `display_entries to clear "busy" message.

        Args:
            message: Initial part of message to user.
            exc: Exception that was thrown, or a string describing the problem
        """
        logger.error(f"{message}\nValidate manually online.\nError details:\n{exc}")
        checker_dialog.display_entries()

    try:
        req = requests.post(
            validator_url,
            data=gzip.compress(bytes(maintext().get("1.0", tk.END), "UTF-8")),
            params={"out": "json"},
            headers={
                "Content-Type": "text/html; charset=UTF-8",
                "Content-Encoding": "gzip",
                "Accept-Encoding": "gzip",
            },
            timeout=15,
        )
    except requests.exceptions.Timeout as exc:
        report_exception(f"Request to {validator_url} timed out.", exc)
        return
    except ConnectionError as exc:
        report_exception(f"Connection error to {validator_url}.", exc)
        return
    # Check if HTTP request was unsuccessful
    try:
        req.raise_for_status()
    except (requests.exceptions.HTTPError, requests.exceptions.TooManyRedirects) as exc:
        report_exception(f"Request to {validator_url} was unsuccessful.", exc)
        return

    # Even if there are no errors, there should still be a messages list
    try:
        messages = req.json()["messages"]
    except KeyError:
        report_exception(
            f"Invalid data returned from {validator_url}.", 'No "messages" key'
        )
        return

    # Add a line to the checker dialog for each message
    for message in messages:
        try:
            end = IndexRowCol(int(message["lastLine"]), int(message["lastColumn"]))
        except KeyError:
            end = None
        # Missing start line means it's all on one line, so same as end line
        try:
            start_row = int(message["firstLine"])
        except KeyError:
            if end is None:
                start_row = None
            else:
                start_row = end.row
        # Missing start column means it's just one character
        try:
            start_col = int(message["firstColumn"]) - 1
        except KeyError:
            if end is None:
                start_col = None
            else:
                start_col = end.col - 1
        if start_row is None or start_col is None:
            start = None
        else:
            # Messages sometimes range from end of previous line,
            # in which case switch to start of next line
            if start_col >= maintext().rowcol(f"{start_row}.end").col:
                start_col = 0
                start_row += 1
            start = IndexRowCol(start_row, start_col)
        if start is None or end is None:
            error_range = None
        else:
            error_range = IndexRange(start, end)
        try:
            line = message["message"]
        except KeyError:
            line = "Data error - no message found"
        try:
            error_type = f'{message["type"].upper()}: '
        except KeyError:
            error_type = ""

        checker_dialog.add_entry(line, error_range, error_prefix=error_type)

    if not messages:
        checker_dialog.add_entry("No errors reported by validator")
    checker_dialog.display_entries()
