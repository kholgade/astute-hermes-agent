"""XML-delimited prompt building utilities.

Provides helpers for structuring system prompts using XML tags instead of
Markdown sections for better clarity and LLM comprehension.
"""

from typing import Optional, Dict, List


def xml_section(tag: str, content: str) -> str:
    """Wrap content in XML tags.

    Args:
        tag: XML tag name (e.g., "persona", "tools")
        content: Content to wrap

    Returns:
        XML-delimited content
    """
    if not content or not content.strip():
        return ""
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


def xml_item(key: str, value: str) -> str:
    """Create a single XML item.

    Args:
        key: Item key/tag
        value: Item value

    Returns:
        XML-delimited item
    """
    if not value or not str(value).strip():
        return ""
    return f"<{key}>{str(value).strip()}</{key}>"


def xml_metadata(metadata: Dict[str, str]) -> str:
    """Build an XML metadata block from a dictionary.

    Args:
        metadata: Key-value pairs to include

    Returns:
        XML-formatted metadata block
    """
    items = []
    for key, value in metadata.items():
        if value and str(value).strip():
            items.append(xml_item(key, str(value).strip()))
    if not items:
        return ""
    return f"<metadata>\n{chr(10).join(items)}\n</metadata>"


def xml_list(items: List[str], tag: str = "item") -> str:
    """Build an XML list from items.

    Args:
        items: List of items to include
        tag: Tag name for each item (default: "item")

    Returns:
        XML-formatted list
    """
    if not items:
        return ""
    wrapped = [f"<{tag}>{item.strip()}</{tag}>" for item in items if item and item.strip()]
    if not wrapped:
        return ""
    return "\n".join(wrapped)


def convert_markdown_to_xml_prompt(
    soul_content: Optional[str] = None,
    user_info: Optional[str] = None,
    tools_description: Optional[str] = None,
    guidelines: Optional[str] = None,
    skills: Optional[str] = None,
) -> str:
    """Build a complete system prompt using XML structure.

    Assembles prompt sections in XML format for clarity and structure.

    Args:
        soul_content: Agent persona/identity (SOUL.md content)
        user_info: User profile information (USER.md content)
        tools_description: Description of available tools
        guidelines: General guidelines and operational rules
        skills: Available skills information

    Returns:
        Complete XML-formatted system prompt
    """
    sections = []

    if soul_content:
        sections.append(xml_section("persona", soul_content))

    if user_info:
        sections.append(xml_section("user_info", user_info))

    if tools_description:
        sections.append(xml_section("tools", tools_description))

    if skills:
        sections.append(xml_section("skills", skills))

    if guidelines:
        sections.append(xml_section("guidelines", guidelines))

    return "\n\n".join(s for s in sections if s)
