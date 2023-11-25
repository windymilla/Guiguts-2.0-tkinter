"""Define key components of main window

Classes:

    Root
    MainWindow
    Menu
    MenuBar
    MainText
    MainImage
    StatusBar
"""


import os.path
from PIL import Image, ImageTk
import re
import tkinter as tk
from tkinter import ttk

from preferences import Preferences
from singleton import singleton
from tk_utilities import isMac


TEXT_WINDOW_ROW = 0
TEXT_WINDOW_COL = 0
IMAGE_WINDOW_ROW = TEXT_WINDOW_ROW
IMAGE_WINDOW_COL = 1
STATUSBAR_ROW = 1
STATUSBAR_COL = 0
STATUSBAR_COLSPAN = 2


@singleton
class Root(tk.Tk):
    """Singleton class inheriting from Tk root window"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.geometry("800x400")
        self.option_add("*tearOff", False)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)


@singleton
class MainWindow:
    """Handles the construction of the main window with its basic widgets"""

    def __init__(self):
        Root()["menu"] = MenuBar()

        frame = ttk.Frame(Root(), padding="5 5 5 5")
        frame.grid(column=0, row=0, sticky="NSEW")
        # Specify image window weights first, so text window will override if on same row or column
        frame.rowconfigure(IMAGE_WINDOW_ROW, weight=0)
        frame.columnconfigure(IMAGE_WINDOW_COL, weight=0)
        frame.rowconfigure(TEXT_WINDOW_ROW, weight=1)
        frame.columnconfigure(TEXT_WINDOW_COL, weight=1)

        StatusBar(frame).grid(
            column=STATUSBAR_COL,
            row=STATUSBAR_ROW,
            columnspan=STATUSBAR_COLSPAN,
            sticky="NSEW",
        )

        MainText(
            frame,
            undo=True,
            wrap="none",
            autoseparators=True,
            maxundo=-1,
        ).grid(column=TEXT_WINDOW_COL, row=TEXT_WINDOW_ROW, sticky="NSEW")

        MainImage(frame)
        if Preferences().get("ImageWindow") == "Docked":
            self.dockImage()
        else:
            self.floatImage()

    def floatImage(self, *args):
        MainImage().grid_remove()
        if MainImage().isImageLoaded():
            Root().wm_manage(MainImage())
            MainImage().lift()
            tk.Wm.protocol(MainImage(), "WM_DELETE_WINDOW", self.dockImage)
        else:
            Root().wm_forget(MainImage())
        Preferences().set("ImageWindow", "Floated")

    def dockImage(self, *args):
        Root().wm_forget(MainImage())
        if MainImage().isImageLoaded():
            MainImage().grid(
                column=IMAGE_WINDOW_COL, row=IMAGE_WINDOW_ROW, sticky="NSEW"
            )
        else:
            MainImage().grid_remove()

        Preferences().set("ImageWindow", "Docked")


class Menu(tk.Menu):
    """Extend tk.Menu to make adding buttons with accelerators simpler"""

    def __init__(self, parent, label, **kwargs):
        super().__init__(parent, **kwargs)
        command_args = {"menu": self}
        if label:
            (label_tilde, label_txt) = processLabel(label)
            command_args["label"] = (label_txt,)
            if label_tilde >= 0:
                command_args["underline"] = label_tilde
        # Only needs cascade if a child of menu/menubar, not if a context popup menu
        if isinstance(parent, tk.Menu):
            parent.add_cascade(command_args)

    #
    # Add a button to the menu
    #
    # label can contain tilde, e.g. "~Save", to underline S
    # handler can be a function or a built-in virtual event,
    #   e.g. "<<Cut>>", in which case button will generate that event,
    #   but there's no need for key binding, since built-in
    # optional accel is accelerator that will be shown in menu:
    #   "Cmd/Ctrl" means Command key on Mac, else Control key
    #   Key binding string is automatically created from accelerator string
    #   and key is bound to do the same as the menu button
    def addButton(self, label, handler, accel=""):
        (label_tilde, label_txt) = processLabel(label)
        (accel, key_event) = processAccel(accel)
        if isinstance(handler, str):
            # Handler is built-in virtual event, so no key binding needed,
            # but event needs to be generated by button click
            def command(*args):
                Root().focus_get().event_generate(handler)

        else:
            # Handler is function, so may need key binding
            command = handler
            if accel:
                MainText().keyBind(key_event, command)

        command_args = {
            "label": label_txt,
            "command": command,
            "accelerator": accel,
        }
        if label_tilde >= 0:
            command_args["underline"] = label_tilde
        self.add_command(command_args)

    #
    # Add cut/copy/paste buttons to given menu
    def addCutCopyPaste(self):
        self.addButton("Cu~t", "<<Cut>>", "Cmd/Ctrl+X")
        self.addButton("~Copy", "<<Copy>>", "Cmd/Ctrl+C")
        self.addButton("~Paste", "<<Paste>>", "Cmd/Ctrl+V")


#
# Given a button label string, e.g. "~Save...", where the optional
# tilde indicates the underline location for keyboard activation,
# return the tilde location (-1 if none), and the string without the tilde
def processLabel(label):
    return (label.find("~"), label.replace("~", ""))


#
# Convert accelerator string, e.g. "Ctrl+X" to appropriate string
# for platform, e.g. "Control-X"
# Support "Cmd/Ctrl+X" to mean use Cmd key on Macs, else Ctrl key
def processAccel(accel):
    if isMac():
        accel = accel.replace("/Ctrl", "")
    else:
        accel = accel.replace("Cmd/", "")
    keyevent = accel.replace("Ctrl+", "Control-")
    keyevent = keyevent.replace("Shift+", "Shift-")
    keyevent = keyevent.replace("Cmd+", "Meta-")
    return (accel, f"<{keyevent}>")


# MenuBar class is tk.Menu, used for main menubar


@singleton
class MenuBar(tk.Menu):
    def __init__(self, **kwargs):
        super().__init__(Root(), **kwargs)


@singleton
class MainText(tk.Text):
    """MainText is the main text window

    MainText inherits from tk.Text but actually creates a Frame
    containing the Text widget and two scrollbars. The layout and
    linking of the scrollbars to the Text widget is done here.
    """

    def __init__(self, parent, **kwargs):
        # Create surrounding Frame
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        # Create Text itself & place in Frame
        # ASK SOMEONE- why doesn't `super()` work in place of `tk.Text` below?
        tk.Text.__init__(self, self.frame, **kwargs)
        tk.Text.grid(self, column=0, row=0, sticky="NSEW")

        # Create scrollbars, place in Frame, and link to Text
        hscroll = ttk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self.xview)
        hscroll.grid(column=0, row=1, sticky="EW")
        self["xscrollcommand"] = hscroll.set
        vscroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.yview)
        vscroll.grid(column=1, row=0, sticky="NS")
        self["yscrollcommand"] = vscroll.set

        # Set up response to text being modified
        self.modifiedCallbacks = []
        self.bind("<<Modified>>", self.modFlagChanged)

        self.initContextMenu()

    # Override grid, so placing MainText widget actually places surrounding Frame
    def grid(self, *args, **kwargs):
        return self.frame.grid(*args, **kwargs)

    #
    # Bind lower & uppercase versions of keyevent to handler
    # in main text window
    def keyBind(self, keyevent, handler):
        lk = re.sub("[A-Z]>$", lambda m: m.group(0).lower(), keyevent)
        uk = re.sub("[A-Z]>$", lambda m: m.group(0).upper(), keyevent)
        self.bind(lk, handler)
        self.bind(uk, handler)

    #
    # Handle modified flag
    #
    # This method is bound to <<Modified>> event which happens whenever the widget's
    # modified flag is changed - not just when changed to True
    # Causes all registered functions to be called
    def modFlagChanged(self, *args):
        for func in self.modifiedCallbacks:
            func()

    #
    # Manually set widget's modified flag (may trigger call to modFlagChanged)
    def setModified(self, mod):
        self.edit_modified(mod)

    #
    # Return if widget's text has been modified
    def isModified(self):
        return self.edit_modified()

    #
    # Add application function to be called when widget's modified flag changes
    def addModifiedCallback(self, func):
        self.modifiedCallbacks.append(func)

    #
    # Save text in widget to file
    def doSave(self, fname):
        with open(fname, "w", encoding="utf-8") as fh:
            fh.write(self.get(1.0, tk.END))
            self.setModified(False)

    #
    # Open file and load text into widget
    def doOpen(self, fname):
        with open(fname, "r", encoding="utf-8") as fh:
            self.delete("1.0", tk.END)
            self.insert(tk.END, fh.read())
            self.setModified(False)

    #
    # Create a context menu for the main text widget
    def initContextMenu(self):
        menu_context = Menu(self, "")
        menu_context.addCutCopyPaste()

        def postContextMenu(event):
            menu_context.post(event.x_root, event.y_root)

        if isMac():
            self.bind("<2>", postContextMenu)
            self.bind("<Control-1>", postContextMenu)
        else:
            self.bind("<3>", postContextMenu)

    #
    # Get the name of the image file the insert cursor is in
    def getImageFilename(self):
        sep_index = self.search("//-----File: ", self.index(tk.INSERT), backwards=True)
        return (
            self.get(sep_index + "+13c", sep_index + "lineend").rstrip("-")
            if sep_index
            else ""
        )

    def get_insert_index(self):
        return self.index(tk.INSERT)


# MainImage is a frame, containing a label which can display a png/jpeg file


@singleton
class MainImage(tk.Frame):  # Can't use ttk.Frame or it's not un/dockable
    def __init__(self, parent):
        tk.Frame.__init__(
            self, parent, borderwidth=2, relief=tk.SUNKEN, name="*Image Viewer*"
        )

        self.label = tk.Label(self, text="No image")
        self.label.grid(column=0, row=0)
        self.photo = None

    #
    # Load the given image file, or display"No image" label
    def loadImage(self, filename=None):
        if os.path.isfile(filename):
            image = Image.open(filename)
            width = 300
            scale = width / image.width
            height = image.height * scale
            image = image.resize((int(width), int(height)))

            self.photo = ImageTk.PhotoImage(image)
            self.label.config(image=self.photo)
        else:
            self.label.config(image="")

    def isImageLoaded(self):
        return bool(self.label.cget("image"))


# StatusBar - status bar at the bottom of the screen
# Label can be added with optional callback that returns a string
# Callbacks will be called regularly to update labels
# Labels without callbacks will be updated manually by the application using set()


@singleton
class StatusBar(tk.Frame):
    def __init__(self, parent):
        tk.Frame.__init__(self, parent, borderwidth=1, relief=tk.SUNKEN)
        self.labels = {}
        self.callbacks = {}
        self._update()

    #
    # Add label to status bar, with optional callback used when updating it
    def add(self, key, callback=None, **kwargs):
        kwargs["borderwidth"] = 1
        kwargs["relief"] = tk.RIDGE
        self.labels[key] = tk.Label(self, kwargs)
        self.callbacks[key] = callback
        self.labels[key].pack(side=tk.LEFT)

    def set(self, key, value):
        self.labels[key].config(text=value)

    def _update(self):
        for key in self.labels:
            if self.callbacks[key]:
                self.set(key, self.callbacks[key]())
        self.after(200, self._update)
