from dataclasses import dataclass, field, asdict
from pathlib import Path

CATEGORIES = ("Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings")


@dataclass(frozen=True)
class Request:
    matter: str
    category: str
    parser: str = "rules"
    model: str = ""


@dataclass
class Document:
    filename: str
    title: str
    size: int
    sha256: str
    document_id: str = ""


@dataclass
class Result:
    request: Request
    metadata: dict[str, str]
    counts: dict[str, int]
    documents: list[Document] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    source_url: str = "https://uarb.novascotia.ca/fmi/webd/UARB15"
    retrieved_at: str = ""

    @property
    def total(self):
        return sum(self.counts.values())

    def to_dict(self):
        return {**asdict(self), "total_documents": self.total}


@dataclass
class Artifacts:
    result: Result
    directory: Path
    archive: Path | None
    reply: Path
