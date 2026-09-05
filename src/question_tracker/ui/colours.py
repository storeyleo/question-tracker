from __future__ import annotations


def contrasting_text_colour(background: str) -> str:
    """Return black or white using the WCAG relative-luminance threshold."""
    red, green, blue = (
        int(background[index : index + 2], 16) / 255
        for index in (1, 3, 5)
    )

    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    luminance = (
        0.2126 * linear(red)
        + 0.7152 * linear(green)
        + 0.0722 * linear(blue)
    )
    return "#000000" if luminance > 0.179 else "#FFFFFF"
