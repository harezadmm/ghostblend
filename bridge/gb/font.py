"""A minimal 5x7 uppercase bitmap font for labelling contact-sheet tiles.

Only the glyphs needed for view names (and a space) are defined; unknown
characters render blank.
"""

# Each glyph is 7 rows of 5 columns; '#' is an inked pixel.
_GLYPHS = {
    "A": ["  #  ", " # # ", "#   #", "#   #", "#####", "#   #", "#   #"],
    "B": ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
    "C": [" ### ", "#   #", "#    ", "#    ", "#    ", "#   #", " ### "],
    "D": ["#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "],
    "E": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
    "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
    "G": [" ### ", "#   #", "#    ", "# ###", "#   #", "#   #", " ### "],
    "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "I": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"],
    "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
    "M": ["#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #"],
    "N": ["#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
    "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    " ": ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
}

GLYPH_W = 5
GLYPH_H = 7


def glyph(ch):
    return _GLYPHS.get(ch.upper(), _GLYPHS[" "])


def text_width(text, scale, spacing=1):
    return len(text) * (GLYPH_W * scale + spacing * scale) if text else 0


def draw_text(pixels, width, height, text, x, y, scale=2, color=(1, 1, 1, 1)):
    """Draw `text` into an RGBA float buffer (`pixels`, row-major, origin top-left).

    `pixels` is a flat list/array of length width*height*4.
    """
    cursor = x
    for ch in text.upper():
        g = glyph(ch)
        for row in range(GLYPH_H):
            for col in range(GLYPH_W):
                if g[row][col] != "#":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px = cursor + col * scale + sx
                        py = y + row * scale + sy
                        if 0 <= px < width and 0 <= py < height:
                            idx = (py * width + px) * 4
                            pixels[idx:idx + 4] = color
        cursor += (GLYPH_W + 1) * scale
