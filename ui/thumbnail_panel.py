"""Thumbnail navigation panel with virtual list (fast for large directories)."""



import tkinter as tk

from tkinter import ttk

from typing import Callable, List



from core.image_item import ImageItem

from utils.constants import (

    UI_SURFACE_BG, UI_TEXT_COLOR, UI_TEXT_MUTED,

    UI_ACCENT, UI_BORDER, UI_PANEL_BG,

)





class _PathTooltip:

    """Small floating label shown near the mouse cursor."""



    def __init__(self, widget: tk.Misc):

        self._widget = widget

        self._window: tk.Toplevel = None

        self._label: tk.Label = None



    def show(self, x_root: int, y_root: int, text: str):

        if not text:

            self.hide()

            return

        if self._window is None:

            self._window = tk.Toplevel(self._widget)

            self._window.wm_overrideredirect(True)

            self._window.wm_attributes('-topmost', True)

            self._label = tk.Label(

                self._window, text=text,

                font=('Microsoft YaHei UI', 9),

                bg='#ffffe0', fg=UI_TEXT_COLOR,

                relief='solid', bd=1,

                padx=6, pady=3,

            )

            self._label.pack()

        self._label.config(text=text)

        self._window.geometry(f'+{x_root + 14}+{y_root + 16}')

        self._window.deiconify()

        self._window.lift()



    def hide(self):

        if self._window is not None:

            self._window.withdraw()





class ThumbnailPanel(tk.Frame):

    """Left-side image list using Listbox (no per-image widget creation)."""



    def __init__(self, parent, on_image_selected: Callable[[int], None] = None,
                 path_formatter: Callable[[ImageItem], str] = None,
                 on_context_menu: Callable[[int, object], None] = None,
                 on_prev_image: Callable[[], None] = None,
                 on_next_image: Callable[[], None] = None):
        super().__init__(parent, bg=UI_SURFACE_BG, bd=0,
                         highlightthickness=1, highlightbackground=UI_BORDER)

        self._on_image_selected = on_image_selected
        self._on_context_menu = on_context_menu
        self._on_prev_image = on_prev_image
        self._on_next_image = on_next_image

        self._path_formatter = path_formatter or (lambda item: item.name)

        self._image_list: List[ImageItem] = []

        self._full_indices: List[int] = []

        self._current_index: int = -1

        self._hover_index: int = -1

        self._filter_hint: str = ''

        self._suppress_select = False

        self._path_tooltip = _PathTooltip(self)



        self._setup_ui()



    def _setup_ui(self):

        header = tk.Frame(self, bg=UI_PANEL_BG, bd=0)

        header.pack(fill='x', padx=0, pady=0)

        tk.Label(

            header, text='图片列表', anchor='center',

            font=('Microsoft YaHei UI', 9, 'bold'),

            bg=UI_PANEL_BG, fg=UI_TEXT_COLOR,

        ).pack(fill='x', padx=6, pady=6)



        self._info_var = tk.StringVar(value='')

        tk.Label(

            self, textvariable=self._info_var,

            font=('Microsoft YaHei UI', 8),

            bg=UI_SURFACE_BG, fg=UI_TEXT_MUTED,

        ).pack(fill='x', padx=6, pady=(0, 4))



        self._list_frame = tk.Frame(self, bg=UI_SURFACE_BG, bd=0)

        self._list_frame.pack(fill='both', expand=True, padx=2, pady=(0, 4))

        self._list_frame.grid_rowconfigure(0, weight=1)
        self._list_frame.grid_rowconfigure(1, weight=0)
        self._list_frame.grid_columnconfigure(0, weight=1)
        self._list_frame.grid_columnconfigure(1, weight=0, minsize=16)



        self._listbox = tk.Listbox(

            self._list_frame,

            font=('Microsoft YaHei UI', 9),

            bg=UI_SURFACE_BG,

            fg=UI_TEXT_COLOR,

            selectbackground=UI_ACCENT,

            selectforeground='white',

            activestyle='none',

            exportselection=False,

            relief='flat',

            bd=0,

            highlightthickness=1,

            highlightbackground=UI_BORDER,

        )

        self._listbox.grid(row=0, column=0, sticky='nsew')



        self._v_scroll = ttk.Scrollbar(
            self._list_frame, orient='vertical', command=self._listbox.yview,
        )
        self._h_scroll = ttk.Scrollbar(
            self._list_frame, orient='horizontal', command=self._listbox.xview,
        )
        self._listbox.config(
            yscrollcommand=self._on_yscroll,
            xscrollcommand=self._on_xscroll,
        )



        self._listbox.bind('<<ListboxSelect>>', self._on_select)

        self._listbox.bind('<Double-Button-1>', self._on_select)
        self._listbox.bind('<Button-3>', self._on_right_click)
        self._listbox.bind('<Motion>', self._on_list_motion)

        self._listbox.bind('<Leave>', self._on_list_leave)

        self._listbox.bind('<MouseWheel>', self._on_mousewheel)
        self._listbox.bind('<Left>', self._on_listbox_left)
        self._listbox.bind('<Right>', self._on_listbox_right)

        self._listbox.bind('<Configure>', lambda e: self.after_idle(self._update_scrollbar))

        self._list_frame.bind('<Configure>', lambda e: self.after_idle(self._update_scrollbar))



    def _on_xscroll(self, first, last):
        self._h_scroll.set(first, last)
        self.after_idle(self._update_scrollbar)

    def _on_yscroll(self, first, last):

        self._v_scroll.set(first, last)

        self.after_idle(self._update_scrollbar)



    def _update_scrollbar(self):

        if not self._listbox.winfo_exists():

            return

        if self._listbox.size() == 0:

            self._v_scroll.grid_remove()
            self._h_scroll.grid_remove()

            return

        first, last = self._listbox.yview()

        need_v = (float(last) - float(first)) < 0.999

        if need_v:

            self._v_scroll.grid(row=0, column=1, sticky='ns')

        else:

            self._v_scroll.grid_remove()

            self._listbox.yview_moveto(0)

        xfirst, xlast = self._listbox.xview()

        need_h = (float(xlast) - float(xfirst)) < 0.999

        if need_h:

            self._h_scroll.grid(row=1, column=0, sticky='ew')

        else:

            self._h_scroll.grid_remove()

            self._listbox.xview_moveto(0)



    def _on_mousewheel(self, event):

        delta = -1 * (event.delta // 120)

        if event.state & 0x1 and self._h_scroll.winfo_ismapped():
            self._listbox.xview_scroll(delta, 'units')
            return

        if not self._v_scroll.winfo_ismapped():
            return

        self._listbox.yview_scroll(delta, 'units')

    def _on_listbox_left(self, event=None):
        """Prev image only — block Listbox default horizontal x-scroll."""
        if self._on_prev_image:
            self._on_prev_image()
        return 'break'

    def _on_listbox_right(self, event=None):
        """Next image only — block Listbox default horizontal x-scroll."""
        if self._on_next_image:
            self._on_next_image()
        return 'break'



    def set_images(self, image_list: List[ImageItem],

                   full_indices: List[int] = None, filter_hint: str = ''):

        """Set the visible image list (filename only, no image decode)."""

        self._path_tooltip.hide()

        self._image_list = image_list

        self._full_indices = (

            list(full_indices) if full_indices is not None

            else list(range(len(image_list)))

        )

        self._filter_hint = filter_hint

        self._suppress_select = True
        try:
            self._listbox.delete(0, 'end')

            if image_list:
                self._listbox.insert('end', *[item.name for item in image_list])
                self._refresh_row_styles()
        finally:
            self._suppress_select = False



        total = len(image_list)

        if total:

            if filter_hint:

                self._info_var.set(f'{filter_hint} {total} 张')

            else:

                self._info_var.set(f'共 {total} 张图片')

        else:

            self._info_var.set(filter_hint or '')

        self.after_idle(self._update_scrollbar)



    def _path_text_for(self, list_index: int) -> str:

        if 0 <= list_index < len(self._image_list):

            return self._path_formatter(self._image_list[list_index])

        return ''



    def _on_list_motion(self, event):

        index = self._listbox.nearest(event.y)

        if index == self._hover_index:

            return

        self._hover_index = index

        if 0 <= index < len(self._image_list):

            self._path_tooltip.show(

                event.x_root, event.y_root,

                self._path_text_for(index),

            )

        else:

            self._path_tooltip.hide()



    def _on_list_leave(self, event=None):

        self._hover_index = -1

        self._path_tooltip.hide()

    def _on_right_click(self, event):
        index = self._listbox.nearest(event.y)
        if 0 <= index < len(self._image_list) and self._on_context_menu:
            self._on_context_menu(self._full_index_at(index), event)

    def set_path_formatter(self, formatter: Callable[[ImageItem], str]):

        self._path_formatter = formatter



    def clear(self):

        """Clear the list."""

        self._path_tooltip.hide()

        self._image_list = []

        self._full_indices = []

        self._filter_hint = ''

        self._current_index = -1

        self._listbox.delete(0, 'end')

        self._info_var.set('')

        self._update_scrollbar()



    def _refresh_row_styles(self):

        """Paint current row with accent color (visible even when list loses focus)."""

        total = self._listbox.size()

        cur = self._current_index

        for i in range(total):

            if i == cur:

                self._listbox.itemconfig(

                    i, bg=UI_ACCENT, fg='white',

                    selectbackground=UI_ACCENT, selectforeground='white',

                )

            else:

                self._listbox.itemconfig(

                    i, bg=UI_SURFACE_BG, fg=UI_TEXT_COLOR,

                    selectbackground=UI_ACCENT, selectforeground='white',

                )



    def _on_select(self, event=None):

        if self._suppress_select:

            return

        selection = self._listbox.curselection()

        if not selection:

            return

        index = selection[0]

        if 0 <= index < len(self._image_list):

            self._current_index = index

            self._refresh_row_styles()

            if self._on_image_selected:

                self._on_image_selected(self._full_index_at(index))



    def _full_index_at(self, list_index: int) -> int:

        if 0 <= list_index < len(self._full_indices):

            return self._full_indices[list_index]

        return list_index



    def set_current_by_full_index(self, full_index: int):

        """Highlight the row that maps to an index in the full image list."""

        try:

            list_index = self._full_indices.index(full_index)

        except ValueError:

            self._current_index = -1

            self._listbox.selection_clear(0, 'end')

            self._refresh_row_styles()

            return

        self.set_current(list_index)



    def set_current(self, index: int):

        """Highlight and scroll to the current image."""

        self._current_index = index

        total = len(self._image_list)

        if total <= 0 or not (0 <= index < total):

            return



        self._suppress_select = True

        try:

            self._listbox.selection_clear(0, 'end')

            self._listbox.selection_set(index)

            self._listbox.see(index)

            self._refresh_row_styles()

        finally:

            self._suppress_select = False

        self.after_idle(self._update_scrollbar)


