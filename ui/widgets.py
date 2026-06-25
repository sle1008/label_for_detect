"""Custom widgets."""



import tkinter as tk

from tkinter import ttk



from utils.constants import UI_TEXT_MUTED, UI_ACCENT, UI_BORDER





class ColorButton(tk.Canvas):

    """A colored button widget."""

    

    def __init__(self, parent, color: str, size: int = 20,

                 command=None, **kwargs):

        super().__init__(parent, width=size, height=size,

                        highlightthickness=1, highlightbackground='gray',

                        bg=color, **kwargs)

        self._command = command

        self._color = color

        self.bind('<Button-1>', self._on_click)

    

    def _on_click(self, event):

        if self._command:

            self._command()

    

    def set_color(self, color: str):

        self._color = color

        self.config(bg=color)





class ThresholdSlider(ttk.Frame):

    """Slider for confidence threshold."""

    

    def __init__(self, parent, from_=0.0, to=1.0,

                 value=0.25, command=None, **kwargs):

        super().__init__(parent, **kwargs)

        

        self._command = command

        

        style = ttk.Style(self)

        style.configure(

            'Threshold.Horizontal.TScale',

            troughcolor='#b0b0b0',

            background=UI_ACCENT,

            darkcolor='#005a9e',

            lightcolor='#4da6ff',

            bordercolor=UI_BORDER,

            sliderthickness=18,

        )

        style.map(

            'Threshold.Horizontal.TScale',

            background=[('active', '#005a9e'), ('!active', UI_ACCENT)],

        )

        

        self._label = ttk.Label(self, text=f'阈值: {value:.2f}',

                               font=('Microsoft YaHei UI', 9),

                               foreground=UI_TEXT_MUTED)

        self._label.pack(side='top', anchor='w', pady=(0, 4))

        

        self._scale = ttk.Scale(

            self, from_=from_, to=to, orient='horizontal',

            value=value, command=self._on_change,

            style='Threshold.Horizontal.TScale',

        )

        self._scale.pack(fill='x', side='top', ipady=4)

    

    def _on_change(self, value):

        self._label.config(text=f'阈值: {float(value):.2f}')

        if self._command:

            self._command(float(value))

    

    def get(self) -> float:

        return self._scale.get()

    

    def set(self, value: float):

        self._scale.set(value)

        self._label.config(text=f'阈值: {float(value):.2f}')


