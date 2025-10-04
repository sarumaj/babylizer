from typing import TYPE_CHECKING, Dict, overload

import polib
from textual.app import ComposeResult
from textual.binding import Binding
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Footer, Input

from ..log import Logger
from ..types import FnmatchHighlighter, FStringHighlighter, Note, POFileEntryTag
from ..utils import apply_styles, escape_control_chars, unescape_control_chars, wait_for_element, write_new_tcomment

if TYPE_CHECKING:

    @overload
    def _(message: str) -> str: ...  # pyright: ignore[reportInconsistentOverload, reportNoOverloadImplementation]


class POEditScreen(ModalScreen[Dict[str, str] | str]):
    """A modal screen for editing or filtering PO file entries."""

    BINDINGS = [
        Binding(key="enter", action="submit", description=_("Submit"), show=True, priority=True),
        Binding(key="escape", action="cancel", description=_("Cancel"), show=True),
    ]

    def __init__(self, entry: polib.POEntry | None, idx: int | None):
        """Initialize the POEditScreen modal.

        If entry is None, the screen is used for filtering entries.

        Args:
            entry (polib.POEntry | None): The PO entry to be edited, or None for filtering.
            idx (int | None): The plural index if editing a plural form, or None for singular or filtering.
            parent_screen (_POEditScreenParentProtocol): The parent screen to interact with.
        """
        ModalScreen.__init__(self)  # pyright: ignore[reportUnknownMemberType]
        self.entry = entry
        self.idx = idx
        self._last_enter_time = 0.0  # For debouncing Enter key in Input
        self.logger.info(
            "POEditScreen initialized",
            extra={
                "context": "POEditScreen.init",
                "entry": str(entry) if entry else "None",
                "idx": idx,
            },
        )

    @property
    def logger(self) -> Logger:
        """Return the application logger."""
        return getattr(
            self.app,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            "logger",
        )

    @property
    def entry(self) -> polib.POEntry | None:
        """The PO entry being edited, or None if filtering."""
        return self._entry

    @entry.setter
    def entry(self, value: polib.POEntry | None) -> None:
        """Set the PO entry being edited."""
        self._entry = value

    @property
    def idx(self) -> int | None:
        """The plural index being edited, or None for singular or filtering."""
        return self._idx

    @idx.setter
    def idx(self, value: int | None) -> None:
        """Set the plural index being edited."""
        self._idx = value

    def compose(self) -> ComposeResult:
        """Compose the UI elements for the modal."""
        yield apply_styles(
            (
                Input(
                    value=_('Editing: "{msgid}" [{idx}]').format(
                        msgid=escape_control_chars(
                            self.entry.msgid  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                            if self.idx is None
                            else self.entry.msgid_plural  # pyright: ignore[reportUnknownMemberType]
                        ),
                        idx=self.idx if self.idx is not None else "Singular",
                    ),
                    disabled=True,
                    highlighter=FStringHighlighter(),
                )
                if self.entry is not None
                else Input(
                    value=_("Filter entries (use * and ? as wildcards)."),
                    disabled=True,
                    highlighter=FnmatchHighlighter(),
                )
            ),
            width="1fr",
            vertical="top",
        )
        yield apply_styles(
            Input(
                id="poedit-input",
                valid_empty=True,
                value=escape_control_chars(  # pyright: ignore[reportUnknownArgumentType]
                    (
                        self.entry.msgstr  # pyright: ignore[reportUnknownMemberType]
                        if self.idx is None
                        else self.entry.msgstr_plural[self.idx]  # pyright: ignore[reportUnknownMemberType]
                    )
                    if self.entry is not None
                    else "*"
                ),
                highlighter=FStringHighlighter() if self.entry is not None else FnmatchHighlighter(),
            ),
            width="1fr",
            vertical="top",
        )
        if self.entry is not None:
            comment_input = apply_styles(
                Input(
                    id="poedit-comment",
                    valid_empty=True,
                    value=escape_control_chars(  # pyright: ignore[reportUnknownArgumentType]
                        Note.parse_entry(self.entry)
                    ),
                    placeholder=_("Add a note... (optional)"),
                ),
                width="1fr",
                vertical="top",
            )
            # Style the comment input differently to distinguish it from the main input
            comment_input.styles.text_style = "italic"
            comment_input.styles.opacity = 0.85
            yield comment_input
        yield Footer()

    async def key_enter(self, event: Key):
        """Handle key events for the modal. Extra handling necessary to debounce Enter key."""
        event.prevent_default()
        event.stop()
        self.logger.debug(
            "Double Enter detected, executing submit.",
            extra={"action": "submit", "context": "POEditScreen.key_enter"},
        )
        await self.run_action("submit")

    async def filter_cells(self):
        """Filter the entries based on the input value."""
        self.logger.debug("Filtering entries", extra={"context": "POEditScreen.filter_cells"})
        new_val = (await wait_for_element(lambda: self.query_one("#poedit-input", Input))).value.strip()
        self.dismiss(new_val)
        self.logger.info(
            "Filter applied and modal dismissed",
            extra={"filter_pattern": new_val, "context": "POEditScreen.filter_cells"},
        )

    async def update_cell(self):
        """Update the translation of the selected entry."""
        self.logger.debug("Updating entry translation", extra={"context": "POEditScreen.update_cell"})
        if self.entry is None:
            self.logger.warning("No entry to update", extra={"context": "POEditScreen.update_cell"})
            return

        new_val = (await wait_for_element(lambda: self.query_one("#poedit-input", Input))).value.strip()
        orig_val = unescape_control_chars(new_val)
        if self.idx is None:
            self.entry.msgstr = orig_val
        else:
            self.entry.msgstr_plural[self.idx] = orig_val  # pyright: ignore[reportUnknownMemberType]

        comment_val = (await wait_for_element(lambda: self.query_one("#poedit-comment", Input))).value.strip()
        Note(unescape_control_chars(comment_val)).update_entry(self.entry)
        POFileEntryTag.REVIEWED.apply(self.entry)
        write_new_tcomment(self.entry, " [Manually edited on {timestamp}]")

        self.dismiss({"msgstr": new_val, "tag": POFileEntryTag.REVIEWED.value, "note": comment_val})
        self.logger.info(
            "Entry updated and modal dismissed",
            extra={
                "entry": str(self.entry),
                "idx": self.idx,
                "new_value": new_val,
                "original_value": orig_val,
                "context": "POEditScreen.update_cell",
            },
        )

    async def action_submit(self):
        """Submit the changes or filter the entries."""
        self.logger.debug("Submitting changes", extra={"context": "POEditScreen.action_submit"})
        if self.entry is None:
            await self.filter_cells()
        else:
            await self.update_cell()
        self.logger.info("Submit action completed", extra={"context": "POEditScreen.action_submit"})

    async def action_cancel(self):
        """Cancel and close the modal without making changes."""
        self.logger.info(
            "Cancelling and closing modal without changes", extra={"context": "POEditScreen.action_cancel"}
        )
        self.dismiss()
        self.logger.info("POEditScreen modal dismissed", extra={"context": "POEditScreen.action_cancel"})
