"""Define key components of main window"""


from idlelib.redirector import WidgetRedirector  # type: ignore[import-not-found]
import logging
import os.path
from PIL import Image, ImageTk
import re
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from types import TracebackType
from typing import Any, Callable, Optional

from guiguts.preferences import preferences
from guiguts.utilities import is_mac, is_x11

logger = logging.getLogger(__package__)

TEXTIMAGE_WINDOW_ROW = 0
TEXTIMAGE_WINDOW_COL = 0
SEPARATOR_ROW = 1
SEPARATOR_COL = 0
STATUSBAR_ROW = 2
STATUSBAR_COL = 0
MIN_PANE_WIDTH = 20


class Root(tk.Tk):
    """Inherits from Tk root window"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.geometry("800x400")
        self.option_add("*tearOff", False)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.after_idle(self.grab_focus)

    def grab_focus(self) -> None:
        """Arcane calls to force window manager to put root window
        to the front and make it active. Then set focus to the text window.
        """
        self.lift()
        self.call("wm", "iconify", ".")
        self.call("wm", "deiconify", ".")
        maintext().focus_set()

    def report_callback_exception(
        self, exc: type[BaseException], val: BaseException, tb: TracebackType | None
    ) -> None:
        """Override tkinter exception reporting rather just
        writing it to stderr.
        """
        err = "Tkinter Exception\n" + "".join(traceback.format_exception(exc, val, tb))
        logger.error(err)


class Menu(tk.Menu):
    """Extend ``tk.Menu`` to make adding buttons with accelerators simpler."""

    def __init__(self, parent: tk.Widget, label: str, **kwargs: Any) -> None:
        """Initialize menu and add to parent

        Args:
            parent: Parent menu/menubar, or another widget if context menu.
            label: Label string for menu, including tilde for keyboard
              navigation, e.g. "~File".
            **kwargs: Optional additional keywords args for ``tk.Menu``.
        """

        super().__init__(parent, **kwargs)
        command_args: dict[str, Any] = {"menu": self}
        if label:
            (label_tilde, label_txt) = _process_label(label)
            command_args["label"] = label_txt
            if label_tilde >= 0:
                command_args["underline"] = label_tilde
        # Only needs cascade if a child of menu/menubar, not if a context popup menu
        if isinstance(parent, tk.Menu):
            parent.add_cascade(command_args)

    def add_button(
        self, label: str, handler: str | Callable[..., Any], accel: str = ""
    ) -> None:
        """Add a button to the menu.

        Args:
            label: Label string for button, including tilde for keyboard
              navigation, e.g. "~Save".
            handler: Callback function or built-in virtual event,
              e.g. "<<Cut>>", in which case button will generate that event.
            accel: String describing optional accelerator key, used when a
              callback function is passed in as ``handler``. Will be displayed
              on the button, and will be bound to the same action as the menu
              button. "Cmd/Ctrl" means `Cmd` key on Mac; `Ctrl` key on
              Windows/Linux.
        """
        (label_tilde, label_txt) = _process_label(label)
        (accel, key_event) = _process_accel(accel)
        if isinstance(handler, str):
            # Handler is built-in virtual event, which needs to be
            # generated by button click
            def command(*args: Any) -> None:
                widget = root().focus_get()
                if widget is not None:
                    widget.event_generate(handler)

        else:
            # Handler is function, so may need key binding
            command = handler

        # If key binding given, then bind it
        if accel:
            maintext().key_bind(key_event, command)

        command_args = {
            "label": label_txt,
            "command": command,
            "accelerator": accel,
        }
        if label_tilde >= 0:
            command_args["underline"] = label_tilde
        self.add_command(command_args)

    def add_cut_copy_paste(self, read_only: bool = False) -> None:
        """Add cut/copy/paste buttons to this menu"""
        if not read_only:
            self.add_button("Cu~t", "<<Cut>>", "Cmd/Ctrl+X")
        self.add_button("~Copy", "<<Copy>>", "Cmd/Ctrl+C")
        if not read_only:
            self.add_button("~Paste", "<<Paste>>", "Cmd/Ctrl+V")
        self.add_separator()
        self.add_button("Select ~All", "<<SelectAll>>", "Cmd/Ctrl+A")


def _process_label(label: str) -> tuple[int, str]:
    """Given a button label string, e.g. "~Save...", where the optional
    tilde indicates the underline location for keyboard activation,
    return the tilde location (-1 if none), and the string without the tilde.
    """
    return (label.find("~"), label.replace("~", ""))


def _process_accel(accel: str) -> tuple[str, str]:
    """Convert accelerator string, e.g. "Ctrl+X" to appropriate keyevent
    string for platform, e.g. "Control-X".

    "Cmd/Ctrl" means use ``Cmd`` key on Mac; ``Ctrl`` key on Windows/Linux.
    """
    if is_mac():
        accel = accel.replace("/Ctrl", "")
    else:
        accel = accel.replace("Cmd/", "")
    keyevent = accel.replace("Ctrl+", "Control-")
    keyevent = keyevent.replace("Shift+", "Shift-")
    keyevent = keyevent.replace("Cmd+", "Meta-")
    return (accel, f"<{keyevent}>")


# TextLineNumbers widget adapted from answer at
# https://stackoverflow.com/questions/16369470/tkinter-adding-line-number-to-text-widget
class TextLineNumbers(tk.Canvas):
    def __init__(
        self, parent: tk.Widget, text_widget: tk.Text, *args: Any, **kwargs: Any
    ) -> None:
        tk.Canvas.__init__(self, parent, *args, **kwargs)
        self.textwidget = text_widget

    def redraw(self, *args: Any) -> None:
        """redraw line numbers"""
        self.delete("all")

        text_pos = self.winfo_width() - 2
        index = self.textwidget.index("@0,0")
        while True:
            dline = self.textwidget.dlineinfo(index)
            if dline is None:
                break
            linenum = str(index).split(".")[0]
            self.create_text(text_pos, dline[1], anchor="ne", text=linenum)
            index = self.textwidget.index("%s+1line" % index)


class MainText(tk.Text):
    """MainText is the main text window, and inherits from ``tk.Text``."""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        """Create a Frame, and put a TextLineNumbers widget, a Text and two
        Scrollbars in the Frame.

        Layout and linking of the TextLineNumbers widget and Scrollbars to
        the Text widget are done here.

        Args:
            parent: Parent widget to contain MainText.
            **kwargs: Optional additional keywords args for ``tk.Text``.
        """

        # Create surrounding Frame
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)

        # Create Text itself & place in Frame
        super().__init__(self.frame, **kwargs)
        tk.Text.grid(self, column=1, row=0, sticky="NSEW")

        # Create a proxy for the underlying widget
        self._w: str  # Let mypy know about _w
        self._orig = self._w + "_orig"
        self.tk.call("rename", self._w, self._orig)
        self.tk.createcommand(self._w, self._proxy)

        # Create Line Numbers widget
        self.linenumbers = TextLineNumbers(self.frame, self, width=35)
        self.linenumbers.grid(column=0, row=0, sticky="NSEW")

        self.bind("<<Change>>", self._on_change)
        self.bind("<Configure>", self._on_change)

        # Create scrollbars, place in Frame, and link to Text
        hscroll = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.xview)
        hscroll.grid(column=1, row=1, sticky="EW")
        self["xscrollcommand"] = hscroll.set
        vscroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.yview)
        vscroll.grid(column=2, row=0, sticky="NS")
        self["yscrollcommand"] = vscroll.set

        # Set up response to text being modified
        self.modifiedCallbacks: list[Callable[[], None]] = []
        self.bind("<<Modified>>", self.modify_flag_changed_callback)

    def _proxy(self, *args: Any) -> Any:
        """Proxy to intercept commands sent to widget and generate a
        <<Changed>> event if line numbers need updating."""

        # Avoid error when copying or deleting
        if (
            (args[0] == "get" or args[0] == "delete")
            and args[1] == "sel.first"
            and args[2] == "sel.last"
            and not self.tag_ranges("sel")
        ):
            return

        # let the actual widget perform the requested action
        cmd = (self._orig,) + args
        try:
            result = self.tk.call(cmd)
        except tk.TclError:
            if args[0:2] == ("edit", "undo") or args[0:2] == ("edit", "redo"):
                sound_bell()
            else:
                raise
            result = None

        # generate an event if something was added or deleted,
        # or the cursor position changed
        if (
            args[0] in ("insert", "replace", "delete")
            or args[0:3] == ("mark", "set", "insert")
            or args[0:2] == ("xview", "moveto")
            or args[0:2] == ("xview", "scroll")
            or args[0:2] == ("yview", "moveto")
            or args[0:2] == ("yview", "scroll")
        ):
            self.event_generate("<<Change>>", when="tail")

        # return what the actual widget returned
        return result

    def _on_change(self, event: tk.Event) -> None:
        """Callback when visible region of file may have changed"""
        self.linenumbers.redraw()

    def grid(self, *args: Any, **kwargs: Any) -> None:
        """Override ``grid``, so placing MainText widget actually places surrounding Frame"""
        return self.frame.grid(*args, **kwargs)

    def toggle_line_numbers(self) -> None:
        """Toggle whether line numbers are shown."""
        self.show_line_numbers(not self.line_numbers_shown())

    def show_line_numbers(self, show: bool) -> None:
        """Show or hide line numbers.

        Args:
            show: True to show, False to hide.
        """
        if self.line_numbers_shown() == show:
            return
        if show:
            self.linenumbers.grid()
        else:
            self.linenumbers.grid_remove()
        preferences.set("LineNumbers", show)

    def line_numbers_shown(self) -> bool:
        """Check if line numbers are shown.

        Returns:
            True if shown, False if not.
        """
        return self.linenumbers.winfo_viewable()

    def key_bind(self, keyevent: str, handler: Callable[[Any], None]) -> None:
        """Bind lower & uppercase versions of ``keyevent`` to ``handler``
        in main text window.

        If this is not done, then use of Caps Lock key causes confusing
        behavior, because pressing ``Ctrl`` and ``s`` sends ``Ctrl+S``.

        Args:
            keyevent: Key event to trigger call to ``handler``.
            handler: Callback function to be bound to ``keyevent``.
        """
        lk = re.sub("[A-Z]>$", lambda m: m.group(0).lower(), keyevent)
        uk = re.sub("[a-z]>$", lambda m: m.group(0).upper(), keyevent)

        def handler_break(event: tk.Event, func: Callable[[tk.Event], None]) -> str:
            """In order for class binding not to be called after widget
            binding, event handler for widget needs to return "break"
            """
            func(event)
            return "break"

        self.bind(lk, lambda event: handler_break(event, handler))
        self.bind(uk, lambda event: handler_break(event, handler))

    #
    # Handle "modified" flag
    #
    def add_modified_callback(self, func: Callable[[], None]) -> None:
        """Add callback function to a list of functions to be called when
        widget's modified flag changes.

        Args:
            func: Callback function to be added to list.
        """
        self.modifiedCallbacks.append(func)

    def modify_flag_changed_callback(self, *args: Any) -> None:
        """This method is bound to <<Modified>> event which happens whenever
        the widget's modified flag is changed - not just when changed to True.

        Causes all functions registered via ``add_modified_callback`` to be called.
        """
        for func in self.modifiedCallbacks:
            func()

    def set_modified(self, mod: bool) -> None:
        """Manually set widget's modified flag (may trigger call to
        ```modify_flag_changed_callback```).

        Args:
            mod: Boolean setting for widget's modified flag."""
        self.edit_modified(mod)

    def is_modified(self) -> bool:
        """Return whether widget's text has been modified."""
        return self.edit_modified()

    def do_save(self, fname: str) -> None:
        """Save widget's text to file.

        Args:
            fname: Name of file to save text to.
        """
        with open(fname, "w", encoding="utf-8") as fh:
            fh.write(self.get_text())
            self.set_modified(False)

    def do_open(self, fname: str) -> None:
        """Load text from file into widget.

        Args:
            fname: Name of file to load text from.
        """
        with open(fname, "r", encoding="utf-8") as fh:
            self.delete("1.0", tk.END)
            self.insert(tk.END, fh.read())
            self.set_modified(False)

    def do_close(self) -> None:
        """Close current file and clear widget."""
        self.delete("1.0", tk.END)
        self.set_modified(False)

    def get_insert_index(self) -> str:
        """Return index of the insert cursor."""
        return self.index(tk.INSERT)

    def set_insert_index(self, index: str, see: bool = False) -> None:
        """Set the position of the insert cursor.

        Args:
            index: String containing index/mark to position cursor.
        """
        self.mark_set(tk.INSERT, index)
        if see:
            self.see(tk.INSERT)
            self.focus_set()

    def get_text(self) -> str:
        """Return all the text from the text widget.

        Strips final additional newline that widget adds at tk.END.

        Returns:
            String containing text widget contents.
        """
        return self.get(1.0, f"{tk.END}-1c")

    # def mark_next(self, index: tk._tkinter.Tcl_Obj) -> str :
    # pos = super().mark_next(index)
    # return pos if pos else ""

    # def mark_previous(self, index: tk._tkinter.Tcl_Obj) -> str :
    # pos = super().mark_previous(index)
    # return pos if pos else ""


class MainImage(tk.Frame):
    """MainImage is a Frame, containing a Canvas which can display a png/jpeg file.

    Also contains scrollbars, and can be scrolled with mousewheel (vertically),
    Shift-mousewheel (horizontally) and zoomed with Control-mousewheel.

    MainImage can be docked or floating. Floating is not supported with ttk.Frame,
    hence inherits from tk.Frame.

    Adapted from https://stackoverflow.com/questions/41656176/tkinter-canvas-zoom-move-pan
    and https://stackoverflow.com/questions/56043767/show-large-image-using-scrollbar-in-python

    Attributes:
        hbar: Horizontal scrollbar.
        vbar: Vertical scrollbar.
        canvas: Canvas widget.
        image: Whole loaded image (or None)
        image_scale: Zoom scale at which image should be drawn.
        scale_delta: Ratio to multiply/divide scale when Control-scrolling mouse wheel.
    """

    def __init__(self, parent: tk.Widget) -> None:
        """Initialize the MainImage to contain an empty Canvas with scrollbars"""
        tk.Frame.__init__(self, parent)

        self.hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL)
        self.hbar.grid(row=1, column=0, sticky="EW")
        self.hbar.configure(command=self.scroll_x)
        self.vbar = ttk.Scrollbar(self, orient=tk.VERTICAL)
        self.vbar.grid(row=0, column=1, sticky="NS")
        self.vbar.configure(command=self.scroll_y)

        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            xscrollcommand=self.hbar.set,
            yscrollcommand=self.vbar.set,
        )
        self.canvas.grid(row=0, column=0, sticky="NSEW")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas.bind("<Configure>", self.show_image)
        self.canvas.bind("<ButtonPress-1>", self.move_from)
        self.canvas.bind("<B1-Motion>", self.move_to)
        if is_x11():
            self.canvas.bind("<Control-Button-5>", self.wheel_zoom)
            self.canvas.bind("<Control-Button-4>", self.wheel_zoom)
            self.canvas.bind("<Button-5>", self.wheel_scroll)
            self.canvas.bind("<Button-4>", self.wheel_scroll)
        else:
            self.canvas.bind("<Control-MouseWheel>", self.wheel_zoom)
            self.canvas.bind("<MouseWheel>", self.wheel_scroll)

        self.image_scale = 1.0
        self.scale_delta = 1.3
        self.image: Optional[Image.Image] = None
        self.imageid = None
        self.container: int = 0
        self.filename = ""

    def scroll_y(self, *args: Any, **kwargs: Any) -> None:
        """Scroll canvas vertically and redraw the image"""
        self.canvas.yview(*args, **kwargs)
        self.show_image()

    def scroll_x(self, *args: Any, **kwargs: Any) -> None:
        """Scroll canvas horizontally and redraw the image."""
        self.canvas.xview(*args, **kwargs)
        self.show_image()

    def move_from(self, event: tk.Event) -> None:
        """Remember previous coordinates for dragging with the mouse."""
        self.canvas.scan_mark(event.x, event.y)

    def move_to(self, event: tk.Event) -> None:
        """Drag canvas to the new position."""
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self.show_image()

    def wheel_zoom(self, event: tk.Event) -> None:
        """Zoom with mouse wheel."""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        bbox_scroll = self.canvas.bbox(self.container)  # get image area
        if not (
            bbox_scroll[0] < x < bbox_scroll[2] and bbox_scroll[1] < y < bbox_scroll[3]
        ):
            return  # zoom only inside image area
        scale = 1.0
        # Respond to Linux (event.num) or Windows/MacOS (event.delta) wheel event
        if event.num == 5 or event.delta < 0:
            min_dimension = min(self.width, self.height)
            if int(min_dimension * self.image_scale) < 30:
                return  # image too small
            self.image_scale /= self.scale_delta
            scale /= self.scale_delta
        if event.num == 4 or event.delta > 0:
            min_dimension = min(self.canvas.winfo_width(), self.canvas.winfo_height())
            if min_dimension < self.image_scale:
                return  # image too large
            self.image_scale *= self.scale_delta
            scale *= self.scale_delta
        self.canvas.scale("all", x, y, scale, scale)  # rescale all canvas objects
        self.show_image()

    def show_image(self, event=None):  # type: ignore[no-untyped-def]
        """Show image on the Canvas"""
        # get image area & remove 1 pixel shift
        bbox_image = self.canvas.bbox(self.container)
        bbox_image = (
            bbox_image[0] + 1,
            bbox_image[1] + 1,
            bbox_image[2] - 1,
            bbox_image[3] - 1,
        )
        # get visible area of the canvas
        bbox_visible = (
            self.canvas.canvasx(0),
            self.canvas.canvasy(0),
            self.canvas.canvasx(self.canvas.winfo_width()),
            self.canvas.canvasy(self.canvas.winfo_height()),
        )
        # get scroll region box
        bbox_scroll = [
            min(bbox_image[0], bbox_visible[0]),
            min(bbox_image[1], bbox_visible[1]),
            max(bbox_image[2], bbox_visible[2]),
            max(bbox_image[3], bbox_visible[3]),
        ]
        # whole image width in the visible area
        if bbox_scroll[0] == bbox_visible[0] and bbox_scroll[2] == bbox_visible[2]:
            bbox_scroll[0] = bbox_image[0]
            bbox_scroll[2] = bbox_image[2]
        # whole image height in the visible area
        if bbox_scroll[1] == bbox_visible[1] and bbox_scroll[3] == bbox_visible[3]:
            bbox_scroll[1] = bbox_image[1]
            bbox_scroll[3] = bbox_image[3]
        self.canvas.configure(scrollregion=bbox_scroll)

        # get coordinates (x1,y1,x2,y2) of the image tile
        x1 = max(bbox_visible[0] - bbox_image[0], 0)
        y1 = max(bbox_visible[1] - bbox_image[1], 0)
        x2 = min(bbox_visible[2], bbox_image[2]) - bbox_image[0]
        y2 = min(bbox_visible[3], bbox_image[3]) - bbox_image[1]
        # show image if it is in the visible area
        xm1 = min(int(x1 / self.image_scale), self.width)
        ym1 = min(int(y1 / self.image_scale), self.height)
        xm2 = min(int(x2 / self.image_scale), self.width)
        ym2 = min(int(y2 / self.image_scale), self.height)
        if int(xm2 - xm1) > 0 and int(ym2 - ym1) > 0:
            image = self.image.crop((xm1, ym1, xm2, ym2))
            self.canvas.imagetk = ImageTk.PhotoImage(
                image.resize(
                    (
                        int(self.image_scale * image.width),
                        int(self.image_scale * image.height),
                    )
                )
            )
            if self.imageid:
                self.canvas.delete(self.imageid)
            self.imageid = self.canvas.create_image(
                max(bbox_visible[0], bbox_image[0]),
                max(bbox_visible[1], bbox_image[1]),
                anchor="nw",
                image=self.canvas.imagetk,
            )

    def wheel_scroll(self, evt: tk.Event) -> None:
        """Scroll image up/down using mouse wheel"""
        if evt.state == 0:
            if is_mac():
                self.canvas.yview_scroll(-1 * (evt.delta), "units")
            else:
                self.canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")
        if evt.state == 1:
            if is_mac():
                self.canvas.xview_scroll(-1 * (evt.delta), "units")
            else:
                self.canvas.xview_scroll(int(-1 * (evt.delta / 120)), "units")
        self.show_image()

    def load_image(self, filename: Optional[str] = None) -> None:
        """Load or clear the given image file.

        Args:
            filename: Optional name of image file. If none given, clear image.
        """
        if filename == self.filename:
            return

        if filename and os.path.isfile(filename):
            self.filename = filename
            self.image = Image.open(filename)
            self.width, self.height = self.image.size
            if self.container:
                self.canvas.delete(self.container)
            self.container = self.canvas.create_rectangle(
                0, 0, self.width, self.height, width=0
            )
            self.canvas.config(scrollregion=self.canvas.bbox(self.container))
            self.canvas.yview_moveto(0)
            self.canvas.xview_moveto(0)
            self.show_image()
        else:
            self.image = None
            self.filename = ""

    def is_image_loaded(self) -> bool:
        """Return if an image is currently loaded"""
        return self.image is not None


class StatusBar(ttk.Frame):
    """Statusbar at the bottom of the screen.

    Fields in statusbar can be automatically or manually updated.
    """

    def __init__(self, parent: Root) -> None:
        """Initialize statusbar within given frame.

        Args:
            parent: Frame to contain status bar.
        """
        super().__init__(parent)
        self.fields: dict[str, ttk.Button] = {}
        self.callbacks: dict[str, Optional[Callable[[], str]]] = {}
        self._update()

    def add(
        self, key: str, update: Optional[Callable[[], str]] = None, **kwargs: Any
    ) -> None:
        """Add field to status bar

        Args:
            key: Key to use to refer to field.
            update: Optional callback function that returns a string.
              If supplied, field will be regularly updated automatically with
              the string returned by ``update()``. If argument not given,
              application is responsible for updating, using ``set(key)``.
        """
        self.fields[key] = ttk.Button(self, **kwargs)
        self.callbacks[key] = update
        self.fields[key].grid(column=len(self.fields), row=0)

    def set(self, key: str, value: str) -> None:
        """Set field in statusbar to given value.

        Args:
            key: Key to refer to field.
            value: String to use to update field.
        """
        self.fields[key].config(text=value)

    def _update(self) -> None:
        """Update fields in statusbar that have callbacks. Updates every
        200 milliseconds.
        """
        for key in self.fields:
            func = self.callbacks[key]
            if func is not None:
                self.set(key, func())
        self.after(200, self._update)

    def add_binding(self, key: str, event: str, callback: Callable[[], None]) -> None:
        """Add an action to be executed when the given event occurs

        Args:
            key: Key to refer to field.
            callback: Function to be called when event occurs.
            event: Event to trigger action. Use button release to avoid
              clash with button activate appearance behavior.
        """
        mouse_bind(self.fields[key], event, lambda *args: callback())


class ScrolledReadOnlyText(tk.Text):
    """Implement a read only mode text editor class with scroll bar.

    Done by replacing the bindings for the insert and delete events. From:
    http://stackoverflow.com/questions/3842155/is-there-a-way-to-make-the-tkinter-text-widget-read-only
    """

    def __init__(self, parent, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Init the class and set the insert and delete event bindings."""

        self.frame = ttk.Frame(parent)
        self.frame.grid(row=0, column=0, sticky="NSEW")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        super().__init__(self.frame, *args, **kwargs)
        super().grid(column=0, row=0, sticky="NSEW")
        self.redirector = WidgetRedirector(self)
        self.insert = self.redirector.register("insert", lambda *args, **kw: "break")
        self.delete = self.redirector.register("delete", lambda *args, **kw: "break")

        hscroll = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.xview)
        hscroll.grid(column=0, row=1, sticky="NSEW")
        self["xscrollcommand"] = hscroll.set
        vscroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.yview)
        vscroll.grid(column=1, row=0, sticky="NSEW")
        self["yscrollcommand"] = vscroll.set

        add_text_context_menu(self, read_only=True)

    def grid(self, *args: Any, **kwargs: Any) -> None:
        """Override ``grid``, so placing Text actually places surrounding Frame"""
        return self.frame.grid(*args, **kwargs)


class MessageLogDialog(tk.Toplevel):
    """A Tk simpledialog that displays error/info messages."""

    def __init__(self, parent: tk.Tk, *args: Any, **kwargs: Any) -> None:
        """Initialize messagelog dialog."""
        super().__init__(parent, *args, **kwargs)
        self.title("Message Log")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        frame = ttk.Frame(self)
        frame.columnconfigure(0, weight=1)
        frame.grid(row=0, column=0, sticky="NSEW")
        frame.rowconfigure(0, weight=1)

        self.messagelog = ScrolledReadOnlyText(frame, wrap=tk.NONE)
        self.messagelog.grid(column=0, row=0, sticky="NSEW")

    def append(self, message: str) -> None:
        """Append a message to the message log dialog."""
        self.messagelog.insert("end", message)


class ErrorHandler(logging.Handler):
    """Handle GUI output of error messages."""

    def __init__(self, *args: Any) -> None:
        """Initialize error logging handler."""
        super().__init__(*args)

    def emit(self, record: logging.LogRecord) -> None:
        """Output error message to message box.

        Args:
            record: Record containing error message.
        """
        messagebox.showerror(title=record.levelname, message=record.getMessage())


class MessageLog(logging.Handler):
    """Handle GUI output of all messages."""

    def __init__(self, *args: Any) -> None:
        """Initialize the message log handler."""
        super().__init__(*args)
        self._messagelog: str = ""
        self.dialog: MessageLogDialog

    def emit(self, record: logging.LogRecord) -> None:
        """Log message in message log.

        Args:
            record: Record containing message.
        """
        message = self.format(record) + "\n"
        self._messagelog += message

        # If dialog is visible, append error
        if hasattr(self, "dialog") and self.dialog.winfo_exists():
            self.dialog.append(message)
            self.dialog.lift()

    def show(self) -> None:
        """Show the message log dialog."""
        if not (hasattr(self, "dialog") and self.dialog.winfo_exists()):
            self.dialog = MessageLogDialog(root())
            self.dialog.append(self._messagelog)
        self.dialog.lift()


class MainWindow:
    """Handles the construction of the main window with its basic widgets

    These class variables are set in ``__init__`` to store the single instance
    of these main window items. They are exposed externally via convenience
    functions with the same names, e.g. ``root()`` returns ``MainWindow.root``
    """

    root: Root
    menubar: tk.Menu
    maintext: MainText
    mainimage: MainImage
    statusbar: StatusBar
    messagelog: MessageLog

    def __init__(self) -> None:
        MainWindow.root = Root()
        MainWindow.menubar = tk.Menu()
        root()["menu"] = menubar()
        MainWindow.messagelog = MessageLog()

        MainWindow.statusbar = StatusBar(root())
        statusbar().grid(
            column=STATUSBAR_COL,
            row=STATUSBAR_ROW,
            sticky="NSEW",
        )

        ttk.Separator(root()).grid(
            column=SEPARATOR_COL,
            row=SEPARATOR_ROW,
            sticky="NSEW",
        )

        self.paned_window = tk.PanedWindow(
            root(), orient=tk.HORIZONTAL, sashwidth=4, sashrelief=tk.GROOVE
        )
        self.paned_window.grid(
            column=TEXTIMAGE_WINDOW_COL, row=TEXTIMAGE_WINDOW_ROW, sticky="NSEW"
        )

        MainWindow.maintext = MainText(
            self.paned_window,
            undo=True,
            wrap="none",
            autoseparators=True,
            maxundo=-1,
            highlightthickness=0,
        )
        self.paned_window.add(maintext().frame, minsize=MIN_PANE_WIDTH)
        add_text_context_menu(maintext())

        MainWindow.mainimage = MainImage(self.paned_window)

    def float_image(self, *args: Any) -> None:
        """Float the image into a separate window"""
        mainimage().grid_remove()
        if mainimage().is_image_loaded():
            root().wm_manage(mainimage())
            mainimage().lift()
            tk.Wm.protocol(mainimage(), "WM_DELETE_WINDOW", self.dock_image)  # type: ignore[call-overload]
        else:
            root().wm_forget(mainimage())  # type: ignore[arg-type]
        preferences.set("ImageWindow", "Floated")

    def dock_image(self, *args: Any) -> None:
        """Dock the image back into the main window"""
        root().wm_forget(mainimage())  # type: ignore[arg-type]
        if mainimage().is_image_loaded():
            self.paned_window.add(mainimage(), minsize=MIN_PANE_WIDTH)
        else:
            try:
                self.paned_window.forget(mainimage())
            except tk.TclError:
                pass  # OK - image wasn't being managed by paned_window
        preferences.set("ImageWindow", "Docked")

    def load_image(self, filename: str) -> None:
        """Load the image for the given page.

        Args:
            filename: Path to image file.
        """
        mainimage().load_image(filename)
        if preferences.get("ImageWindow") == "Docked":
            self.dock_image()
        else:
            self.float_image()

    def clear_image(self) -> None:
        """Clear the image currently being shown."""
        mainimage().load_image("")


def mouse_bind(
    widget: tk.Widget, event: str, callback: Callable[[tk.Event], object]
) -> None:
    """Bind mouse button callback to event on widget.

    If binding is to mouse button 2 or 3, also bind the other button
    to support all platforms and 2-button mice.

    Args:
        widget: Widget to bind to
        event: Event string to trigger callback
        callback: Function to be called when event occurs
    """
    widget.bind(event, callback)

    if match := re.match(r"(<.*Button.*)([23])(>)", event):
        other_button = "2" if match.group(2) == "3" else "3"
        other_event = match.group(1) + other_button + match.group(3)
        widget.bind(other_event, callback)


def sound_bell() -> None:
    """Sound warning bell audibly and/or visually.

    Audible uses the default system bell sound.
    Visible flashes the first statusbar button (must be ttk.Button)
    Preference "Bell" contains "Audible", "Visible", both or neither
    """
    bell_pref = preferences.get("Bell")
    if "Audible" in bell_pref:
        root().bell()
    if "Visible" in bell_pref:
        bell_button = statusbar().fields["rowcol"]
        # Belt & suspenders: uses the "disabled" state of button in temporary style,
        # but also restores setting in temporary style, and restores default style.
        style = ttk.Style()
        # Set temporary style's disabled bg to red, inherting
        style.map("W.TButton", foreground=[("disabled", "red")])
        # Save current disabled bg default for buttons
        save_bg = style.lookup("TButton", "background", state=[("disabled")])
        # Save style currently used by button
        cur_style = statusbar().fields["rowcol"]["style"]
        # Set button to use temporary style
        bell_button.configure(style="W.TButton")
        # Flash 3 times
        for state in ("disabled", "normal", "disabled", "normal", "disabled", "normal"):
            bell_button["state"] = state
            bell_button.update()
            time.sleep(0.08)
        # Set button to use its previous style again
        bell_button.configure(style=cur_style)
        # Just in case, set the temporary style back to the default
        style.map("W.TButton", background=[("disabled", save_bg)])


def add_text_context_menu(text_widget: tk.Text, read_only: bool = False) -> None:
    """Add a context menu to a Text widget.

    Puts Cut, Copy, Paste, Select All menu buttons in a context menu.

    Args:
        read_only: True if text is read-only, so does not require Cut & Paste options.
    """
    menu_context = Menu(text_widget, "")
    menu_context.add_cut_copy_paste(read_only=read_only)

    def post_context_menu(event: tk.Event) -> None:
        event.widget.focus_set()
        menu_context.post(event.x_root, event.y_root)

    if is_mac():
        text_widget.bind("<2>", post_context_menu)
        text_widget.bind("<Control-1>", post_context_menu)
    else:
        text_widget.bind("<3>", post_context_menu)


def root() -> Root:
    """Return the single instance of Root"""
    assert MainWindow.root is not None
    return MainWindow.root


def mainimage() -> MainImage:
    """Return the single MainImage widget"""
    assert MainWindow.mainimage is not None
    return MainWindow.mainimage


def maintext() -> MainText:
    """Return the single MainText widget"""
    assert MainWindow.maintext is not None
    return MainWindow.maintext


def menubar() -> tk.Menu:
    """Return the single Menu widget used as the menubar"""
    assert MainWindow.menubar is not None
    return MainWindow.menubar


def statusbar() -> StatusBar:
    """Return the single StatusBar widget"""
    assert MainWindow.statusbar is not None
    return MainWindow.statusbar
