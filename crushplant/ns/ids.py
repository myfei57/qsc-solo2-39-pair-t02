"""Human readable identifiers for production and calibration orders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..errors import InvalidRequest, NameConflict
from .namespace import KIND_BATCH, KIND_CALIBRATION

CODE_PATTERN = re.compile(
    r"^(?P<site>[A-Z0-9]{2,10})-(?P<kind>[A-Z]{3,5})-(?P<day>\d{8})-(?P<serial>\d{4})$"
)
ISSUABLE_KINDS = (KIND_BATCH, KIND_CALIBRATION)
KIND_TAGS = {KIND_BATCH: "BTCH", KIND_CALIBRATION: "CALB"}


def site_code(site: str) -> str:
    """Reduce a site name to the prefix used inside issued codes."""

    letters = "".join(character for character in site.upper() if character.isalnum())
    if len(letters) < 2:
        raise InvalidRequest("site name is too short to form a code prefix", site=site)
    return letters[:10]


@dataclass(frozen=True)
class TagCode:
    """A parsed identifier."""

    site: str
    kind: str
    issued_on: str
    serial: int

    @property
    def text(self) -> str:
        return f"{self.site}-{self.kind}-{self.issued_on}-{self.serial:04d}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.text,
            "site": self.site,
            "kind": self.kind,
            "issued_on": self.issued_on,
            "serial": self.serial,
        }


def normalise_code(text: str) -> str:
    """Upper-case and trim a code so callers cannot smuggle duplicates in."""

    if not isinstance(text, str) or not text.strip():
        raise InvalidRequest("a code must be a non-empty string")
    return text.strip().upper()


def parse_code(text: str) -> TagCode:
    """Read a code back, refusing anything that was not issued by this shape."""

    matched = CODE_PATTERN.match(normalise_code(text))
    if matched is None:
        raise InvalidRequest("code does not follow the issued pattern", code=text)
    return TagCode(
        site=matched.group("site"),
        kind=matched.group("kind"),
        issued_on=matched.group("day"),
        serial=int(matched.group("serial")),
    )


class IdIssuer:
    """Issues sequential identifiers per kind and refuses foreign ones."""

    def __init__(self, site: str) -> None:
        self.site = site_code(site)
        self._codes: list[str] = []

    def _tag(self, kind: str) -> str:
        tag = KIND_TAGS.get(kind)
        if tag is None:
            raise InvalidRequest("that kind of code is not issued here", kind=kind)
        return tag

    def issue(self, kind: str, moment: datetime) -> str:
        tag = self._tag(kind)
        code = f"{self.site}-{tag}-{moment.strftime('%Y%m%d')}-{len(self._codes) + 1:04d}"
        self._codes.append(code)
        return code

    def register(self, code: str) -> str:
        """Adopt an external code after checking it belongs to this site."""

        parsed = parse_code(code)
        if parsed.site != self.site:
            raise InvalidRequest("code belongs to another site", code=code, site=self.site)
        if parsed.kind not in KIND_TAGS.values():
            raise InvalidRequest("code kind is not issued here", code=code)
        if parsed.text in self._codes:
            raise NameConflict("that code is already on the register", code=parsed.text)
        self._codes.append(parsed.text)
        return parsed.text

    def issued(self, kind: str) -> list[str]:
        tag = self._tag(kind)
        return [code for code in self._codes if f"-{tag}-" in code]

    def all_codes(self) -> list[str]:
        return list(self._codes)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tag in KIND_TAGS.values():
            counts[tag] = len([code for code in self._codes if f"-{tag}-" in code])
        return counts

    def codes_by_kind(self) -> dict[str, list[str]]:
        """Every code the site holds, grouped the way the register reports it."""

        return {
            kind: [code for code in self._codes if f"-{tag}-" in code]
            for kind, tag in KIND_TAGS.items()
        }
