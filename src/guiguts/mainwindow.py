"""Define key components of main window"""


from idlelib.redirector import WidgetRedirector  # type: ignore[import-not-found]
import logging
import os.path
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Optional

from PIL import Image, ImageTk
import regex as re

from guiguts.maintext import MainText, maintext
from guiguts.preferences import preferences
from guiguts.root import Root, root
from guiguts.utilities import (
    is_mac,
    is_x11,
    bell_set_callback,
    process_accel,
    process_label,
)
from guiguts.widgets import ToplevelDialog

logger = logging.getLogger(__package__)

TEXTIMAGE_WINDOW_ROW = 0
TEXTIMAGE_WINDOW_COL = 0
SEPARATOR_ROW = 1
SEPARATOR_COL = 0
STATUSBAR_ROW = 2
STATUSBAR_COL = 0
MIN_PANE_WIDTH = 20


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
            (label_tilde, label_txt) = process_label(label)
            command_args["label"] = label_txt
            if label_tilde >= 0:
                command_args["underline"] = label_tilde
        # Only needs cascade if a child of menu/menubar, not if a context popup menu
        if isinstance(parent, tk.Menu):
            parent.add_cascade(command_args)

    def add_button(
        self, label: str, handler: str | Callable[[], Any], accel: str = ""
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
        (label_tilde, label_txt) = process_label(label)
        (accel, key_event) = process_accel(accel)
        if isinstance(handler, str):
            bind_all = False

            # Handler is built-in virtual event, which needs to be
            # generated by button click
            def command() -> None:
                widget = root().focus_get()
                if widget is not None:
                    widget.event_generate(handler)

        else:
            bind_all = True
            # Handler is function, so may need key binding
            command = handler

        # If key binding given, then bind it
        if accel:
            # Note that when run via a key binding, Tk will pass in an event arg
            # to the callback function, but this arg is never used, and is absorbed
            # by the lambda function below
            # Key is bound to all widgets, except for built-in virtual events.
            maintext().key_bind(key_event, lambda _event: command(), bind_all=bind_all)

        command_args = {
            "label": label_txt,
            "command": command,
            "accelerator": accel,
        }
        if label_tilde >= 0:
            command_args["underline"] = label_tilde
        self.add_command(command_args)

    def add_checkbox(
        self,
        label: str,
        handler_on: Callable[[], None],
        handler_off: Callable[[], None],
        initial_var: bool,
        accel: str = "",
    ) -> None:
        """Add a button to the menu.

        Args:
            label: Label string for button, including tilde for keyboard
              navigation, e.g. "~Save".
            handler_on: Callback function for when checkbox gets checked
            handler_off: Callback function for when checkbox gets checked
            var: To keep track of state
            accel: String describing optional accelerator key, used when a
              callback function is passed in as ``handler``. Will be displayed
              on the button, and will be bound to the same action as the menu
              button. "Cmd/Ctrl" means `Cmd` key on Mac; `Ctrl` key on
              Windows/Linux.
        """
        (label_tilde, label_txt) = process_label(label)
        (accel, key_event) = process_accel(accel)

        bool_var = tk.BooleanVar(value=initial_var)

        # If key binding given, then bind it
        if accel:

            def accel_command(_event: tk.Event) -> None:
                """Command to simulate checkbox click via shortcut key.

                Because key hasn't been clicked, variable hasn't been toggled.
                """
                bool_var.set(not bool_var.get())
                checkbox_clicked()

            maintext().key_bind(key_event, accel_command, bind_all=True)

        def checkbox_clicked() -> None:
            """Callback when checkbox is clicked.

            Call appropriate handler depending on setting."""
            if bool_var.get():
                handler_on()
            else:
                handler_off()

        command_args = {
            "label": label_txt,
            "command": checkbox_clicked,
            "variable": bool_var,
            "accelerator": accel,
        }
        if label_tilde >= 0:
            command_args["underline"] = label_tilde
        self.add_checkbutton(command_args)

    def add_cut_copy_paste(self, read_only: bool = False) -> None:
        """Add cut/copy/paste buttons to this menu"""
        if not read_only:
            self.add_button("Cu~t", "<<Cut>>", "Cmd/Ctrl+X")
        self.add_button("~Copy", "<<Copy>>", "Cmd/Ctrl+C")
        if not read_only:
            self.add_button("~Paste", "<<Paste>>", "Cmd/Ctrl+V")
        self.add_separator()
        self.add_button("Select ~All", "<<SelectAll>>", "Cmd/Ctrl+A")


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
        self.width = 0
        self.height = 0

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

    def show_image(self, _event=None):  # type: ignore[no-untyped-def]
        """Show image on the Canvas"""
        # get image area & remove 1 pixel shift
        if self.image is None:
            return
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
            self.clear_image()

    def clear_image(self) -> None:
        """Clear the image and reset variables accordingly."""
        self.filename = ""
        self.image = None
        if self.imageid:
            self.canvas.delete(self.imageid)
        self.imageid = None

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
        self.fields[key] = ttk.Button(self, takefocus=0, **kwargs)
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
        # Tk passes event object to callback which is never used. To avoid all
        # callbacks having to ignore it, the lambda below absorbs & discards it.
        mouse_bind(self.fields[key], event, lambda _event: callback())


class ScrolledReadOnlyText(tk.Text):
    """Implement a read only mode text editor class with scroll bar.

    Done by replacing the bindings for the insert and delete events. From:
    http://stackoverflow.com/questions/3842155/is-there-a-way-to-make-the-tkinter-text-widget-read-only
    """

    def __init__(self, parent, context_menu=True, **kwargs):  # type: ignore[no-untyped-def]
        """Init the class and set the insert and delete event bindings."""

        self.frame = ttk.Frame(parent)
        self.frame.grid(row=0, column=0, sticky="NSEW")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        super().__init__(self.frame, **kwargs)
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

        self["inactiveselect"] = self["selectbackground"]

        if context_menu:
            add_text_context_menu(self, read_only=True)

    def grid(self, *args: Any, **kwargs: Any) -> None:
        """Override ``grid``, so placing Text actually places surrounding Frame"""
        return self.frame.grid(*args, **kwargs)


class MessageLogDialog(ToplevelDialog):
    """A dialog that displays error/info messages."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize messagelog dialog."""
        super().__init__("Message Log", *args, **kwargs)
        self.messagelog = ScrolledReadOnlyText(self.top_frame, wrap=tk.NONE)
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
        already_shown = hasattr(self, "dialog") and self.dialog.winfo_exists()
        self.dialog = MessageLogDialog.show_dialog()
        if not already_shown:
            self.dialog.append(self._messagelog)
        self.dialog.lift()


class MainWindow:
    """Handles the construction of the main window with its basic widgets

    These class variables are set in ``__init__`` to store the single instance
    of these main window items. They are exposed externally via convenience
    functions with the same names, e.g. ``root()`` returns ``MainWindow.root``
    """

    menubar: tk.Menu
    mainimage: MainImage
    statusbar: StatusBar
    messagelog: MessageLog

    def __init__(self) -> None:
        Root()
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

        MainText(
            self.paned_window,
            root(),
            undo=True,
            wrap="none",
            autoseparators=True,
            maxundo=-1,
            highlightthickness=0,
        )

        self.paned_window.add(maintext().frame, minsize=MIN_PANE_WIDTH)
        add_text_context_menu(maintext())

        MainWindow.mainimage = MainImage(self.paned_window)

    def hide_image(self) -> None:
        """Stop showing the current image."""
        self.clear_image()
        root().wm_forget(mainimage())  # type: ignore[arg-type]
        self.paned_window.forget(mainimage())

    def float_image(self, _event: Optional[tk.Event] = None) -> None:
        """Float the image into a separate window"""
        mainimage().grid_remove()
        if mainimage().is_image_loaded():
            root().wm_manage(mainimage())
            mainimage().lift()
            tk.Wm.protocol(mainimage(), "WM_DELETE_WINDOW", self.hide_image)  # type: ignore[call-overload]
        else:
            root().wm_forget(mainimage())  # type: ignore[arg-type]
        preferences.set("ImageWindow", "Floated")

    def dock_image(self, _event: Optional[tk.Event] = None) -> None:
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
        mainimage().clear_image()


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


def do_sound_bell() -> None:
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


bell_set_callback(do_sound_bell)


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


def mainimage() -> MainImage:
    """Return the single MainImage widget"""
    assert MainWindow.mainimage is not None
    return MainWindow.mainimage


def menubar() -> tk.Menu:
    """Return the single Menu widget used as the menubar"""
    assert MainWindow.menubar is not None
    return MainWindow.menubar


def statusbar() -> StatusBar:
    """Return the single StatusBar widget"""
    assert MainWindow.statusbar is not None
    return MainWindow.statusbar
