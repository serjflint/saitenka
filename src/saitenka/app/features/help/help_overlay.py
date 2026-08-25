"""The in-player shortcut reference: building its document, and rendering a page of it.

Both are functions of the bindings and the screen, so they live here and can be checked at any size
without a SessionController. Whether the overlay is open and which page it shows is `Owner.INTERACTION`'s
`help` slice (`runtime/help.py` decides, `interaction_slice` holds); `HelpController` owns presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.render.help import HelpEntry, build_document, render_page

if TYPE_CHECKING:
    from collections.abc import Iterable

    from saitenka.app.bindings import ActiveBinding


def help_entries(bindings: Iterable[ActiveBinding]) -> tuple[HelpEntry, ...]:
    """The rows the reference lists — every binding that opts into being shown."""
    return tuple(
        HelpEntry(
            binding.spec.section,
            binding.key,
            binding.spec.label,
            binding.spec.context,
            binding.spec.source,
        )
        for binding in bindings
        if binding.spec.show_in_help
    )


def help_footer(close_key: str) -> str:
    return f"{close_key} / Esc close  ·  PgUp/PgDn or wheel"


def help_document(
    bindings: Iterable[ActiveBinding], *, osd: tuple[int, int], close_key: str, scale: float
):
    """Paginate the reference for a screen. Pure: same bindings and size, same document."""
    return build_document(
        help_entries(bindings), osd=osd, footer=help_footer(close_key), scale=scale
    )


def page_image(document, index: int):
    """Render page ``index`` of ``document``."""
    return render_page(
        document.pages[index],
        width=document.width,
        height=document.height,
        index=index,
        total=len(document.pages),
        scale=document.scale,
    )
