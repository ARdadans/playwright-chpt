"""
Image processing module for novel translation.

Handles:
1. Extracting Markdown (![alt](url)) and HTML (<img>) images from source text.
2. Replacing image tags with indexed placeholders (<<<IMG_0>>>, <<<IMG_1>>>, ...).
3. Restoring placeholders into unified HTML <img> tags post-translation,
   preserving sizes/attributes and auto-generating missing alt text.
"""

import html
import re
from typing import Any

# Match markdown images: ![alt](url "optional title") or HTML <img> tags
COMBINED_IMAGE_PATTERN = re.compile(
    r'(?P<md>!\[(?P<md_alt>[^\]]*?)\]\(\s*<?(?P<md_url>[^\s\)>]+)>?(?:\s+["\'](?P<md_title>.*?)["\'])?\s*\))'
    r"|"
    r"(?P<html><img\b(?P<html_attrs>[^>]*)>)",
    flags=re.IGNORECASE | re.DOTALL,
)

HTML_ATTR_PATTERN = re.compile(r'([a-zA-Z0-9_-]+)(?:\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+)))?')


def parse_html_attrs(attrs_str: str) -> dict[str, str]:
    """Parse HTML tag attributes into a dictionary."""
    attrs: dict[str, str] = {}
    for match in HTML_ATTR_PATTERN.finditer(attrs_str):
        name = match.group(1).lower()
        if name in ("/", ""):
            continue
        val = (
            match.group(2)
            if match.group(2) is not None
            else (match.group(3) if match.group(3) is not None else match.group(4))
        )
        attrs[name] = val if val is not None else ""
    return attrs


def extract_images(
    text: str,
    novel_id: str = "",
    chapter_number: float = 1,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Extract all Markdown and HTML images from text and replace with <<<IMG_{index}>>> placeholders.

    Returns:
        tuple of (cleaned_text_with_placeholders, list_of_extracted_images_metadata)
    """
    images: list[dict[str, Any]] = []

    def _replace_image(match: re.Match) -> str:
        idx = len(images)
        if match.group("md"):
            alt = match.group("md_alt")
            url = match.group("md_url")
            images.append(
                {
                    "index": idx,
                    "src": url.strip(),
                    "alt": alt.strip() if alt else None,
                    "extra_attrs": {},
                }
            )
        else:
            attrs_str = match.group("html_attrs")
            attrs = parse_html_attrs(attrs_str)
            src = attrs.pop("src", "")
            alt = attrs.pop("alt", None)
            images.append(
                {
                    "index": idx,
                    "src": src.strip(),
                    "alt": alt.strip() if alt else None,
                    "extra_attrs": attrs,
                }
            )
        return f"<<<IMG_{idx}>>>"

    cleaned_text = COMBINED_IMAGE_PATTERN.sub(_replace_image, text)
    return cleaned_text, images


def restore_images(
    text: str,
    images: list[dict[str, Any]],
    novel_id: str = "",
    chapter_number: float = 1,
) -> str:
    """
    Replace <<<IMG_{index}>>> placeholders in the translated text with HTML <img> tags.
    If alt text was not provided in the original request, generates:
    "{novel_id} - Ch. {chapter_number} illustration"
    """
    if not images:
        return text

    # Format chapter number cleanly (e.g. 2 instead of 2.0)
    ch_str = (
        str(int(chapter_number))
        if isinstance(chapter_number, (int, float)) and float(chapter_number).is_integer()
        else str(chapter_number)
    )

    if novel_id and novel_id.strip():
        default_alt = f"{novel_id.strip()} - Ch. {ch_str} illustration"
    else:
        default_alt = f"Ch. {ch_str} illustration"

    def _build_img_tag(img_info: dict[str, Any]) -> str:
        src = img_info.get("src", "")
        alt = img_info.get("alt")
        if not alt or not str(alt).strip():
            alt = default_alt

        alt_escaped = html.escape(str(alt), quote=True)
        src_escaped = html.escape(str(src), quote=True)

        extra_parts = []
        for k, v in img_info.get("extra_attrs", {}).items():
            if v is not None and v != "":
                extra_parts.append(f'{k}="{html.escape(str(v), quote=True)}"')
            else:
                extra_parts.append(k)

        extra_str = f" {' '.join(extra_parts)}" if extra_parts else ""
        return f'<img src="{src_escaped}"{extra_str} alt="{alt_escaped}">'

    tag_map = {img["index"]: _build_img_tag(img) for img in images}

    pattern = re.compile(r"<<<IMG_(\d+)>>>")
    replaced_indices = set()

    def _replace_placeholder(match: re.Match) -> str:
        idx = int(match.group(1))
        if idx in tag_map:
            replaced_indices.add(idx)
            return tag_map[idx]
        return match.group(0)

    result = pattern.sub(_replace_placeholder, text)

    # If any image placeholders were dropped by LLM, append them at the end safely
    missing = [img for img in images if img["index"] not in replaced_indices]
    if missing:
        append_tags = "\n\n" + "\n\n".join(tag_map[img["index"]] for img in missing)
        result = result.rstrip() + append_tags

    return result
