"""Intermediate representation (IR) of a 2D engineering drawing, independent of where it was read from.

A reader (pdf_drawing.py for vector PDFs; a native SLDDRW decoder later) produces a Drawing. Everything after that, linking the
dimensions to a 3D model and comparing, uses only this structure. Coordinates are the source's own paper units (PDF points,
origin bottom-left, y up); `scale` is drawn length / real length (a 1:2 drawing has scale 0.5); real lengths are in `unit`.

JSON round trip: Drawing.to_dict() / Drawing.from_dict(). Nothing here reads a file.
"""
from dataclasses import dataclass, field, asdict, fields
from typing import Optional

MM_PER_PT = 25.4 / 72.0


@dataclass
class Tol:
    """kind: none | bilateral (upper/lower offsets, either sign) | limits (absolute high/low) | basic (boxed theoretical value) | reference (parenthesised).
    upper/lower are offsets from the nominal for bilateral, absolute values for limits."""
    kind: str = 'none'
    upper: Optional[float] = None
    lower: Optional[float] = None


@dataclass
class Dimension:
    id: str
    kind: str                                  # linear | diameter | radius | angle
    text: str                                  # the dimension's text as read, runs joined with ' '
    value: Optional[float] = None              # nominal, in `unit`, from the text
    unit: str = 'mm'
    tol: Tol = field(default_factory=Tol)
    count: Optional[int] = None                # "2X", "4 PLCS"
    suffix: list = field(default_factory=list) # words such as THRU, TYP
    view: Optional[str] = None                 # View.id it belongs to
    text_pos: Optional[list] = None            # [x, y] of the main text's origin
    text_rot: int = 0                          # 0 or 90
    tips: list = field(default_factory=list)   # arrow tips [[x, y], ...] (where the dimension touches the feature)
    ext: list = field(default_factory=list)    # extension lines [[x0, y0, x1, y1], ...]; the far end sits next to the feature
    arc: Optional[list] = None                 # [cx, cy, r] of the circle/arc a diameter or radius dimension points at
    drawn: Optional[float] = None              # value measured from the drawn geometry, in `unit` (drawn length / scale); None when not measurable
    drawn_dev: Optional[float] = None          # drawn - value, when both exist
    style: str = ''                            # paired (two arrows on a dimension line) | leader (one arrow + leader) | text-only
    conf: str = 'ok'                           # ok | partial (some part could not be read)


@dataclass
class View:
    id: str
    bbox: list                                 # [x0, y0, x1, y1] of the object linework
    label: Optional[str] = None                # text found under or beside it (FRONT, SECTION A-A, ...)
    n_segments: int = 0
    scale: Optional[float] = None              # per-view scale when the label gives one, else the sheet scale


@dataclass
class Note:
    id: str
    text: str
    pos: list
    size: float = 0.0
    kind: str = 'note'                         # note | view_label | title_block | general_tolerance


@dataclass
class Datum:
    id: str
    letter: str
    pos: list


@dataclass
class GdtFrame:
    id: str
    pos: list                                  # [x0, y0, x1, y1]
    cells: list                                # raw text of each cell, left to right
    symbol: Optional[str] = None               # characteristic name when the cell-0 glyph is recognised, else None (raw text stays in cells[0])
    value: Optional[float] = None
    diameter_zone: bool = False
    modifiers: list = field(default_factory=list)  # M, L, S, P ...
    datums: list = field(default_factory=list)     # datum letters in order
    attached_to: Optional[str] = None          # Dimension.id of the nearest dimension/leader


@dataclass
class Sheet:
    index: int
    size_pt: list                              # [width, height]
    scale: Optional[float] = None
    scale_text: Optional[str] = None
    unit: str = 'mm'
    unit_assumed: bool = True
    views: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    datums: list = field(default_factory=list)
    gdt: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class Drawing:
    source: str                                # reader name, e.g. 'pdf-vector'
    sheets: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        def mk(cls, x): return cls(**{f.name: x[f.name] for f in fields(cls) if f.name in x})
        sheets = []
        for s in d['sheets']:
            s = dict(s)
            s['views'] = [mk(View, v) for v in s.get('views', [])]
            s['dimensions'] = [Dimension(**{**{f.name: x[f.name] for f in fields(Dimension) if f.name in x}, 'tol': Tol(**x['tol'])}) for x in s.get('dimensions', [])]
            s['notes'] = [mk(Note, n) for n in s.get('notes', [])]
            s['datums'] = [mk(Datum, n) for n in s.get('datums', [])]
            s['gdt'] = [mk(GdtFrame, n) for n in s.get('gdt', [])]
            sheets.append(mk(Sheet, s))
        return Drawing(d['source'], sheets)
