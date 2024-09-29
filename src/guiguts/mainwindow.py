"""Define key components of main window"""

from idlelib.redirector import WidgetRedirector  # type: ignore[import-not-found]
import logging
import os.path
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Optional

from PIL import Image, ImageTk, ImageChops

from guiguts.maintext import MainText, maintext
from guiguts.preferences import preferences, PrefKey, PersistentBoolean
from guiguts.root import Root, root, ImageWindowState
from guiguts.utilities import (
    is_mac,
    is_x11,
    bell_set_callback,
    process_accel,
    process_label,
    IndexRowCol,
)
from guiguts.widgets import (
    ToplevelDialog,
    mouse_bind,
    ToolTip,
    themed_style,
    theme_set_tk_widget_colors,
    Busy,
)

logger = logging.getLogger(__package__)

TEXTIMAGE_WINDOW_ROW = 0
TEXTIMAGE_WINDOW_COL = 0
SEPARATOR_ROW = 1
SEPARATOR_COL = 0
STATUS_ROW = 2
STATUS_COL = 0
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
        bool_var: tk.BooleanVar,
        accel: str = "",
    ) -> None:
        """Add a button to the menu.

        Args:
            label: Label string for button, including tilde for keyboard
              navigation, e.g. "~Save".
            handler_on: Callback function for when checkbox gets checked
            handler_off: Callback function for when checkbox gets checked
            bool_var: Tk variable to keep track of state or set it from elsewhere
            accel: String describing optional accelerator key, used when a
              callback function is passed in as ``handler``. Will be displayed
              on the button, and will be bound to the same action as the menu
              button. "Cmd/Ctrl" means `Cmd` key on Mac; `Ctrl` key on
              Windows/Linux.
        """
        (label_tilde, label_txt) = process_label(label)
        (accel, key_event) = process_accel(accel)

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

        control_frame = ttk.Frame(self, padding=5)
        control_frame.grid(row=0, column=0, columnspan=2, sticky="NEW")
        invert_btn = ttk.Checkbutton(
            control_frame,
            text="Invert image",
            takefocus=False,
            command=self.show_image,
            variable=PersistentBoolean(PrefKey.IMAGE_INVERT),
        )
        invert_btn.grid(row=0, column=0, sticky="NSW", columnspan=5)
        ttk.Label(control_frame, text="Zoom:").grid(row=1, column=0, sticky="NSEW")
        self.zoom_in_btn = ttk.Button(
            control_frame,
            text="+",
            takefocus=False,
            command=lambda: self.image_zoom(zoom_in=True),
        )
        self.zoom_in_btn.grid(row=1, column=1, sticky="NSEW")
        self.zoom_out_btn = ttk.Button(
            control_frame,
            text="-",
            takefocus=False,
            command=lambda: self.image_zoom(zoom_in=False),
        )
        self.zoom_out_btn.grid(row=1, column=2, sticky="NSEW")
        # Separate bindings needed for docked (root) and floated (self) states
        for widget in (root(), self):
            _, cp = process_accel("Cmd/Ctrl+plus")
            _, cm = process_accel("Cmd/Ctrl+minus")
            widget.bind(cp, lambda _: self.zoom_in_btn.invoke())
            widget.bind(cm, lambda _: self.zoom_out_btn.invoke())
        ttk.Button(
            control_frame,
            text="Fit to width",
            takefocus=False,
            command=self.image_zoom_to_width,
        ).grid(row=1, column=3, sticky="NSEW")
        ttk.Button(
            control_frame,
            text="Fit to height",
            takefocus=False,
            command=self.image_zoom_to_height,
        ).grid(row=1, column=4, sticky="NSEW")
        self.hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL)
        self.hbar.grid(row=2, column=0, sticky="EW")
        self.hbar.configure(command=self.scroll_x)
        self.vbar = ttk.Scrollbar(self, orient=tk.VERTICAL)
        self.vbar.grid(row=1, column=1, sticky="NS")
        self.vbar.configure(command=self.scroll_y)

        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            xscrollcommand=self.hbar.set,
            yscrollcommand=self.vbar.set,
        )
        self.canvas.grid(row=1, column=0, sticky="NSEW")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        cmdctrl = "Cmd" if is_mac() else "Ctrl"
        ToolTip(
            self.canvas,
            f"Drag image\nScroll with mousewheel\nZoom with {cmdctrl}+mousewheel",
            use_pointer_pos=True,
        )

        self.canvas.bind("<Configure>", self.show_image)
        self.canvas.bind("<ButtonPress-1>", self.move_from)
        self.canvas.bind("<B1-Motion>", self.move_to)
        if is_x11():
            self.canvas.bind("<Control-Button-5>", self.wheel_zoom)
            self.canvas.bind("<Control-Button-4>", self.wheel_zoom)
            self.canvas.bind("<Button-5>", self.wheel_scroll)
            self.canvas.bind("<Button-4>", self.wheel_scroll)
        else:
            _, cm = process_accel("Cmd/Ctrl+MouseWheel")
            self.canvas.bind(cm, self.wheel_zoom)
            self.canvas.bind("<MouseWheel>", self.wheel_scroll)

        self.image_scale = 1.0
        self.scale_delta = 1.3
        self.image: Optional[Image.Image] = None
        self.imageid = None
        self.imagetk = None
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
        """Zoom with mouse wheel.

        Args:
            event: Event containing mouse wheel info.
        """
        # Respond to Linux (event.num) or Windows/MacOS (event.delta) wheel event
        if event.num == 5 or event.delta < 0:
            self.zoom_out_btn.invoke()
        if event.num == 4 or event.delta > 0:
            self.zoom_in_btn.invoke()

    def image_zoom(self, zoom_in: bool) -> None:
        """Zoom the image in or out.

        Args:
            zoom_in: True to zoom in, False to zoom out.
        """
        if zoom_in:
            if self.image_scale < 3:
                self.image_scale *= self.scale_delta
        else:
            if self.image_scale > 0.1:
                self.image_scale /= self.scale_delta
        self.show_image()

    def image_zoom_to_width(self) -> None:
        """Zoom image to fit to width of image window."""
        assert self.imageid is not None
        bbox_image = self.canvas.bbox(self.imageid)
        scale_factor = (
            self.canvas.canvasx(self.canvas.winfo_width()) - self.canvas.canvasx(0)
        ) / (bbox_image[2] - bbox_image[0])
        self.image_zoom_by_factor(scale_factor)

    def image_zoom_to_height(self) -> None:
        """Zoom image to fit to height of image window."""
        assert self.imageid is not None
        bbox_image = self.canvas.bbox(self.imageid)
        scale_factor = (
            self.canvas.canvasx(self.canvas.winfo_height()) - self.canvas.canvasy(0)
        ) / (bbox_image[3] - bbox_image[1])
        self.image_zoom_by_factor(scale_factor)

    def image_zoom_by_factor(self, scale_factor: float) -> None:
        """Zoom image by the given scale factor.

        Args:
            scale_factor: Factor to zoom by.
        """
        self.image_scale *= scale_factor
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)
        self.show_image()

    def show_image(self, _event=None):  # type: ignore[no-untyped-def]
        """Show image on the Canvas"""
        # get image area & remove 1 pixel shift
        if self.image is None:
            return
        self.canvas["background"] = themed_style().lookup("TButton", "background")
        image = self.image
        scaled_width = int(self.image_scale * image.width)
        scaled_height = int(self.image_scale * image.height)
        if preferences.get(PrefKey.IMAGE_INVERT):
            image = ImageChops.invert(self.image)
        if self.imagetk:
            del self.imagetk
        self.imagetk = ImageTk.PhotoImage(
            image.resize(
                size=(scaled_width, scaled_height), resample=Image.Resampling.LANCZOS
            )
        )
        if self.imageid:
            self.canvas.delete(self.imageid)
        self.imageid = self.canvas.create_image(
            0,
            0,
            anchor="nw",
            image=self.imagetk,
        )
        self.canvas.configure(scrollregion=self.canvas.bbox(self.imageid))

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
            self.image = Image.open(filename).convert("RGB")
            self.width, self.height = self.image.size
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

    def __init__(self, parent: ttk.Frame) -> None:
        """Initialize statusbar within given frame.

        Args:
            parent: Frame to contain status bar.
        """
        super().__init__(parent)
        self.fields: dict[str, ttk.Button] = {}
        self.callbacks: dict[str, Optional[Callable[[], str]]] = {}
        self._update()

    def add(
        self,
        key: str,
        tooltip: str = "",
        update: Optional[Callable[[], str]] = None,
        **kwargs: Any,
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
        if tooltip:
            ToolTip(self.fields[key], msg=tooltip)

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

    # Tag can be used to select a line of text, and to search for the selected line
    # Can't use standard selection since that would interfere with user trying to copy/paste, etc.
    SELECT_TAG_NAME = "chk_select"

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

        self.tag_configure(
            ScrolledReadOnlyText.SELECT_TAG_NAME,
            background="#dddddd",
            foreground="#000000",
        )

        # Since Text widgets don't normally listen to theme changes,
        # need to do it explicitly here.
        super().bind(
            "<<ThemeChanged>>", lambda _event: theme_set_tk_widget_colors(self)
        )
        # Also on creation, so it's correct for the current theme
        theme_set_tk_widget_colors(self)

        # Redirect undo & redo events to main text window
        super().bind("<<Undo>>", lambda _event: maintext().event_generate("<<Undo>>"))
        super().bind("<<Redo>>", lambda _event: maintext().event_generate("<<Redo>>"))

        if context_menu:
            add_text_context_menu(self, read_only=True)

    def grid(self, *args: Any, **kwargs: Any) -> None:
        """Override ``grid``, so placing Text actually places surrounding Frame"""
        return self.frame.grid(*args, **kwargs)

    def select_line(self, line_num: int) -> None:
        """Highlight the line_num'th line of text, removing any other highlights.

        Args:
            line_num: Line number to be highlighted - assumed valid.
        """
        self.tag_remove(ScrolledReadOnlyText.SELECT_TAG_NAME, "1.0", tk.END)
        self.tag_add(
            ScrolledReadOnlyText.SELECT_TAG_NAME, f"{line_num}.0", f"{line_num + 1}.0"
        )
        self.see(f"{line_num}.0")

    def get_select_line_num(self) -> Optional[int]:
        """Get the line number of the currently selected line.

        Returns:
            Line number of selected line, or None if no line selected.
        """
        if tag_range := self.tag_nextrange(ScrolledReadOnlyText.SELECT_TAG_NAME, "1.0"):
            return IndexRowCol(tag_range[0]).row
        return None


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
    busy_widget: ttk.Label

    def __init__(self) -> None:
        Root()
        # Themes
        themed_style(ttk.Style())

        MainWindow.menubar = tk.Menu()
        root()["menu"] = menubar()
        MainWindow.messagelog = MessageLog()

        status_frame = ttk.Frame(root())
        status_frame.grid(
            column=STATUS_COL,
            row=STATUS_ROW,
            sticky="NSEW",
        )
        status_frame.columnconfigure(0, weight=1)
        MainWindow.statusbar = StatusBar(status_frame)
        MainWindow.statusbar.grid(
            column=0,
            row=0,
            sticky="NSW",
        )
        MainWindow.busy_widget = ttk.Label(status_frame, foreground="red")
        MainWindow.busy_widget.grid(
            column=1,
            row=0,
            sticky="NSE",
            padx=10,
        )
        Busy.busy_widget_setup(MainWindow.busy_widget)

        ttk.Separator(root()).grid(
            column=SEPARATOR_COL,
            row=SEPARATOR_ROW,
            sticky="NSEW",
        )

        self.paned_window = tk.PanedWindow(
            root(),
            orient=tk.HORIZONTAL,
            sashwidth=4,
            sashrelief=tk.RIDGE,
            showhandle=True,
            handlesize=10,
        )
        self.paned_window.grid(
            column=TEXTIMAGE_WINDOW_COL, row=TEXTIMAGE_WINDOW_ROW, sticky="NSEW"
        )

        self.paned_text_window = tk.PanedWindow(
            self.paned_window,
            orient=tk.VERTICAL,
            sashwidth=4,
            sashrelief=tk.RIDGE,
            showhandle=True,
            handlesize=10,
        )
        self.paned_text_window.grid(
            column=TEXTIMAGE_WINDOW_COL, row=TEXTIMAGE_WINDOW_ROW, sticky="NSEW"
        )

        MainText(
            self.paned_text_window,
            root(),
            undo=True,
            wrap="none",
            autoseparators=True,
            maxundo=-1,
            highlightthickness=2,
        )

        self.paned_window.add(self.paned_text_window, minsize=MIN_PANE_WIDTH)
        add_text_context_menu(maintext())
        add_text_context_menu(maintext().peer)

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
        preferences.set(PrefKey.IMAGE_WINDOW, ImageWindowState.FLOATED)

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
        preferences.set(PrefKey.IMAGE_WINDOW, ImageWindowState.DOCKED)

    def load_image(self, filename: str) -> None:
        """Load the image for the given page.

        Args:
            filename: Path to image file.
        """
        mainimage().load_image(filename)
        if preferences.get(PrefKey.IMAGE_WINDOW) == ImageWindowState.DOCKED:
            self.dock_image()
        else:
            self.float_image()

    def clear_image(self) -> None:
        """Clear the image currently being shown."""
        mainimage().clear_image()


def do_sound_bell() -> None:
    """Sound warning bell audibly and/or visually.

    Audible uses the default system bell sound.
    Visible flashes the first statusbar button (must be ttk.Button)
    """
    if preferences.get(PrefKey.BELL_AUDIBLE):
        root().bell()
    if preferences.get(PrefKey.BELL_VISUAL):
        bell_button = statusbar().fields["rowcol"]
        # Belt & suspenders: uses the "disabled" state of button in temporary style,
        # but also restores setting in temporary style, and restores default style.
        style = ttk.Style()
        # Set temporary style's disabled bg to red
        style.map("W.TButton", foreground=[("disabled", "red")])
        # Save current disabled bg default for buttons
        save_bg = style.lookup("TButton", "background", state=[("disabled")])
        # Save style currently used by button
        cur_style = bell_button["style"]
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

    mouse_bind(text_widget, "3", post_context_menu)


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
