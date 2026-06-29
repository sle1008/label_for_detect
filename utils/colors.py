"""Color management for label classes."""


# 36 strong, distinct colors. All are deep enough to stay readable as text and
# box outlines on a light background (no faded pastels). Colors repeat in a
# cycle once there are more than 36 classes.
LABEL_PALETTE = [
    '#e6194b',  # red
    '#f58231',  # orange
    '#b8860b',  # amber / dark gold
    '#808000',  # olive
    '#3cb44b',  # green
    '#2e8b57',  # sea green
    '#16a085',  # green teal
    '#008080',  # teal
    '#0097a7',  # dark cyan
    '#1f77b4',  # steel blue
    '#4363d8',  # blue
    '#000075',  # navy
    '#4b0082',  # indigo
    '#6a1b9a',  # deep purple
    '#911eb4',  # purple
    '#c71585',  # violet red
    '#f032e6',  # magenta
    '#d81b60',  # raspberry pink
    '#c0392b',  # brick red
    '#d2691e',  # chocolate
    '#9a6324',  # brown
    '#800000',  # maroon
    '#5d4037',  # taupe
    '#525252',  # dark gray
    '#c62828',  # crimson
    '#ef6c00',  # vivid orange
    '#689f38',  # olive green
    '#00695c',  # dark teal
    '#0277bd',  # cerulean blue
    '#283593',  # indigo blue
    '#4527a0',  # blue violet
    '#7b1fa2',  # medium purple
    '#880e4f',  # plum
    '#4e342e',  # espresso brown
    '#455a64',  # blue gray
    '#1565c0',  # royal blue
]


def get_color_for_class(class_id: int) -> str:
    """Get a strong color for a class ID, cycling through the palette."""
    return LABEL_PALETTE[class_id % len(LABEL_PALETTE)]


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex color."""
    return f'#{r:02x}{g:02x}{b:02x}'


def blend_hex(fg_hex: str, bg_hex: str = '#e8e8e8', alpha: float = 0.2) -> str:
    """Blend foreground over background (alpha = fg opacity)."""
    fr, fg, fb = hex_to_rgb(fg_hex)
    br, bg, bb = hex_to_rgb(bg_hex)
    r = int(fr * alpha + br * (1 - alpha))
    g = int(fg * alpha + bg * (1 - alpha))
    b = int(fb * alpha + bb * (1 - alpha))
    return rgb_to_hex(r, g, b)
