"""Store and manipulate page labels."""

import tkinter as tk
from tkinter import simpledialog, ttk

import roman  # type: ignore[import-untyped]

from guiguts.maintext import maintext, page_mark_from_img
from guiguts.widgets import OkApplyCancelDialog, mouse_bind, ToolTip

STYLE_COLUMN = "#2"
STYLE_ARABIC = "Arabic"
STYLE_ROMAN = "Roman"
STYLE_DITTO = '"'
NUMBER_COLUMN = "#3"
NUMBER_PAGENUM = "Number"
NUMBER_INCREMENT = "+1"
NUMBER_NONE = "No Count"
COL_HEAD_IMG = "Img"
COL_HEAD_STYLE = "Style"
COL_HEAD_NUMBER = "Number"
COL_HEAD_LABEL = "Label"
PAGE_LABEL_PREFIX = "Pg "

STYLES = [STYLE_ARABIC, STYLE_ROMAN, STYLE_DITTO]
NUMBERS = [NUMBER_PAGENUM, NUMBER_INCREMENT, NUMBER_NONE]


class PageDetail(dict):
    """Page detail information for one page.

    Attributes:
        index: Index of start of page in file.
        style: Page number style: "Arabic" or "Roman"
        number:
    """

    def __init__(
        self, index: str, style: str = '"', number: str = NUMBER_INCREMENT
    ) -> None:
        """Initialize a PageDetail object to hold page information."""
        self["index"] = index
        self["style"] = style
        self["number"] = number
        self["label"] = ""


class PageDetails(dict[str, PageDetail]):
    """Dictionary of Page Detail objects: key is png name."""

    def recalculate(self) -> None:
        """Recalculate labels from details."""
        number = 0
        style = STYLE_ARABIC
        for _, detail in sorted(self.items()):
            if detail["style"] != STYLE_DITTO:
                style = detail["style"]
            if detail["number"] == NUMBER_NONE:
                detail["label"] = ""
            else:
                if detail["number"] == NUMBER_INCREMENT:
                    number += 1
                else:
                    number = int(detail["number"])
                if style == STYLE_ROMAN:
                    try:
                        label = roman.toRoman(number).lower()
                    except roman.OutOfRangeError:
                        label = "Roman error"
                else:
                    label = str(number)
                detail["label"] = PAGE_LABEL_PREFIX + label

    def png_from_label(self, label: str) -> str:
        """Find the png corresponding to the page with the given label.

        Args:
            label: The label to match against.

        Returns:
            The png number corresponding to the given label, or empty string.
        """
        for png, detail in self.items():
            if detail["label"] == label:
                return png
        return ""

    def copy_details_from(self, details: "PageDetails") -> None:
        """Copy the style and number details from given PageDetails

        Args:
            details: Details to copy from.
        """
        for img, detail in details.items():
            self[img] = PageDetail("", detail["style"], detail["number"])
        self.recalculate()


class PageDetailsDialog(OkApplyCancelDialog):
    """A dialog that allows the user to view/edit page details.

    Attributes:
        master_details: Dictionary of page details as used by text file.
        details: Temporary dictionary to hold values as changed in dialog.
        changed: True if any changes have been made in dialog.
    """

    def __init__(self, page_details: PageDetails) -> None:
        """Initialize class members from page details."""
        super().__init__("Configure Page Labels")
        self.master_details = page_details
        self.details = PageDetails()
        self.details.copy_details_from(self.master_details)
        self.changed = False

        columns = (COL_HEAD_IMG, COL_HEAD_STYLE, COL_HEAD_NUMBER, COL_HEAD_LABEL)
        widths = (50, 80, 80, 120)
        self.list = ttk.Treeview(
            self.top_frame,
            columns=columns,
            show="headings",
            height=10,
            selectmode=tk.BROWSE,
        )
        ToolTip(
            self.list,
            "\n".join(
                [
                    "Click in style column to cycle Arabic/Roman/Ditto",
                    "Click in number column to cycle +1/No Count/Set Number",
                    "Shift click to cycle in reverse order",
                ]
            ),
            use_pointer_pos=True,
        )
        for col, column in enumerate(columns):
            self.list.column(
                f"#{col + 1}",
                minwidth=10,
                width=widths[col],
                stretch=False,
                anchor=tk.CENTER,
            )
            self.list.heading(f"#{col + 1}", text=column)

        mouse_bind(
            self.list, "1", lambda event: self.item_clicked(event, reverse=False)
        )
        mouse_bind(
            self.list, "Shift+1", lambda event: self.item_clicked(event, reverse=True)
        )
        self.list.grid(row=0, column=0, sticky=tk.NSEW)

        self.scrollbar = ttk.Scrollbar(
            self.top_frame, orient=tk.VERTICAL, command=self.list.yview
        )
        self.list.configure(yscroll=self.scrollbar.set)  # type: ignore[call-overload]
        self.scrollbar.grid(row=0, column=1, sticky=tk.NS)

        self.populate_list(self.details)

    def populate_list(self, details: PageDetails, see_index: int = 0) -> None:
        """Populate the page details list from the given details.

        Args:
            details: PageDetails to be displayed in dialog.
            see_index: Index of item in list that should be made visible.
        """
        children = self.list.get_children()
        for child in children:
            self.list.delete(child)

        for png, detail in sorted(details.items()):
            entry = (png, detail["style"], detail["number"], detail["label"])
            self.list.insert("", tk.END, values=entry)

        children = self.list.get_children()
        if children:
            self.list.see(children[see_index])
            self.list.selection_set(children[see_index])

    def item_clicked(self, event: tk.Event, reverse: bool) -> None:
        """Called when page detail item is clicked.

        If click is in style or number column, then advance style/number
        setting to the next value. Refresh the list to show new labels.

        Args:
            event: Event containing location of mouse click
            reverse: True to "advance" in reverse!
        """
        row_id = self.list.identify_row(event.y)
        row = self.list.set(row_id)

        # Display the page
        png = row[COL_HEAD_IMG]
        index = maintext().rowcol(page_mark_from_img(png))
        maintext().set_insert_index(index)

        col_id = self.list.identify_column(event.x)
        if col_id not in (STYLE_COLUMN, NUMBER_COLUMN):
            return

        if col_id == STYLE_COLUMN:
            if COL_HEAD_STYLE not in row:
                return
            # Click in style column advances/retreats style
            style_index = STYLES.index(row[COL_HEAD_STYLE])
            if reverse:
                style_index = len(STYLES) - 1 if style_index == 0 else style_index - 1
            else:
                style_index = 0 if style_index == len(STYLES) - 1 else style_index + 1
            self.details[row[COL_HEAD_IMG]]["style"] = STYLES[style_index]
        elif col_id == NUMBER_COLUMN:
            if COL_HEAD_NUMBER not in row:
                return
            # Click in number column advances/retreats number type.
            # May need to prompt user for page number.
            value = row[COL_HEAD_NUMBER]
            if value.isdecimal():
                value = NUMBER_PAGENUM
            number_index = NUMBERS.index(value)
            if reverse:
                number_index = (
                    len(NUMBERS) - 1 if number_index == 0 else number_index - 1
                )
            else:
                number_index = (
                    0 if number_index == len(NUMBERS) - 1 else number_index + 1
                )
            new_value = NUMBERS[number_index]
            if new_value == NUMBER_PAGENUM:
                pagenum = simpledialog.askinteger(
                    "Set Number", "Enter page number", parent=self
                )
                new_value = pagenum if pagenum else value
            self.details[row[COL_HEAD_IMG]]["number"] = new_value

        # Refresh the list
        self.details.recalculate()
        self.populate_list(self.details, self.list.index(row_id))
        self.changed = True

    def apply_changes(self) -> bool:
        """Overridden to update page label settings from the dialog."""
        if self.changed:
            self.master_details.copy_details_from(self.details)
            maintext().set_modified(True)
        return True
