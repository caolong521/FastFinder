import os

DOCUMENT_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".rtf"
}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".svg"
}
PROGRAM_EXTENSIONS = {
    ".exe", ".dll", ".bat", ".cmd", ".ps1", ".py", ".pyw", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".js", ".ts", ".java", ".jar"
}


def split_name(path_or_name: str) -> tuple[str, str]:
    name = os.path.basename(path_or_name)
    stem, ext = os.path.splitext(name)
    return stem, ext.casefold()


# Pre-compute frozen sets for faster membership testing
_DOCUMENT_EXTENSIONS = frozenset(DOCUMENT_EXTENSIONS)
_IMAGE_EXTENSIONS = frozenset(IMAGE_EXTENSIONS)
_PROGRAM_EXTENSIONS = frozenset(PROGRAM_EXTENSIONS)


def category_for(extension: str, is_directory: bool) -> str:
    if is_directory:
        return "folder"
    ext = (extension or "").casefold()
    if ext in _DOCUMENT_EXTENSIONS:
        return "document"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _PROGRAM_EXTENSIONS:
        return "program"
    return "file"


# Pre-computed units tuple to avoid list allocation on every call
_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_size(size: int) -> str:
    if size is None:
        return ""
    value = float(max(0, size))
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
