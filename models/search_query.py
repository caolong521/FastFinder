from dataclasses import dataclass, field


@dataclass(slots=True)
class SearchQuery:
    raw_text: str
    keywords: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    type_filter: str | None = None  # file / folder
    path_filter: str | None = None
    after_time: int | None = None
    before_time: int | None = None
    size_min: int | None = None
    size_max: int | None = None
