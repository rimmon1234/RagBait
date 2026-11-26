"""Safe XML delimiter formatting and injection-resistant escaping for evidence & prompts."""

import re
from xml.sax.saxutils import escape

from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk

# Control characters other than standard whitespace (\t, \n, \r)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_XML_ENTITIES = {
    '"': "&quot;",
    "'": "&apos;",
}


def sanitize_xml_text(text: str) -> str:
    """Strip unsafe control bytes and XML-escape untrusted content.

    Because `<`, `>`, `&`, `"`, and `'` are converted to XML entities, adversarial
    passages or user messages cannot inject or close structural tags like
    `</study_context_evidence>`, `<source>`, or `<system_instructions>`.
    """
    if not text:
        return ""
    cleaned = _CONTROL_CHAR_RE.sub("", text)
    return escape(cleaned, entities=_XML_ENTITIES)


def format_physical_pages(page_start: int, page_end: int) -> str:
    """Format physical PDF/document page range cleanly."""
    if page_start == page_end:
        return str(page_start)
    return f"{page_start}-{page_end}"


def format_source_block(citation_key: str, chunk: RetrievedChunk) -> str:
    """Format a single verified chunk into an XML-escaped <source> block with provenance."""
    safe_id = sanitize_xml_text(citation_key)
    safe_title = sanitize_xml_text(chunk.document_title)
    pages_label = format_physical_pages(chunk.page_number_start, chunk.page_number_end)
    safe_kind = sanitize_xml_text(getattr(chunk, "source_kind", "text_layer"))
    safe_content = sanitize_xml_text(chunk.raw_text)
    provenance_tag = (
        f"    <source_kind>{safe_kind}</source_kind>\n" if safe_kind != "text_layer" else ""
    )

    return (
        f'  <source id="{safe_id}">\n'
        f"    <document_title>{safe_title}</document_title>\n"
        f"    <physical_pages>{pages_label}</physical_pages>\n"
        f"{provenance_tag}"
        f"    <content>\n"
        f"      {safe_content}\n"
        f"    </content>\n"
        f"  </source>"
    )


def format_evidence_envelope(source_blocks: list[str]) -> str:
    """Wrap formatted source blocks in the <study_context_evidence> container."""
    if not source_blocks:
        return (
            '<study_context_evidence status="empty" trust_level="untrusted_reference_data">\n'
            "  No verified study material excerpts matched this query in the active subject.\n"
            "</study_context_evidence>"
        )
    joined = "\n".join(source_blocks)
    return (
        '<study_context_evidence trust_level="untrusted_reference_data">\n'
        f"{joined}\n"
        "</study_context_evidence>"
    )


def format_summary_envelope(summary_text: str) -> str:
    """Wrap historical conversation summary cleanly outside the study evidence block."""
    safe_summary = sanitize_xml_text(summary_text.strip())
    return (
        '<conversation_summary trust_level="historical_memory_only" citable="false">\n'
        f"  {safe_summary}\n"
        "</conversation_summary>"
    )


def format_user_query_envelope(user_query: str) -> str:
    """Wrap the student's current question in XML delimiters."""
    safe_query = sanitize_xml_text(user_query.strip())
    return f'<user_query trust_level="untrusted_user_input">\n  {safe_query}\n</user_query>'


def format_web_source_block(
    citation_key: str,
    *,
    title: str,
    url: str,
    domain: str,
    excerpt: str,
    published_at: str | None = None,
    retrieved_at_iso: str | None = None,
    snippet_only: bool = True,
) -> str:
    """Format a server-verified external web source into an XML-escaped <web_source> block."""
    safe_id = sanitize_xml_text(citation_key)
    safe_title = sanitize_xml_text(title)
    safe_url = sanitize_xml_text(url)
    safe_domain = sanitize_xml_text(domain)
    safe_excerpt = sanitize_xml_text(excerpt)
    scope = "search_snippet_only" if snippet_only else "fetched_page_excerpt"
    pub_line = (
        f"    <published_at>{sanitize_xml_text(published_at)}</published_at>\n"
        if published_at
        else ""
    )
    ret_line = (
        f"    <retrieved_at>{sanitize_xml_text(retrieved_at_iso)}</retrieved_at>\n"
        if retrieved_at_iso
        else ""
    )

    return (
        f'  <web_source id="{safe_id}">\n'
        f"    <title>{safe_title}</title>\n"
        f"    <domain>{safe_domain}</domain>\n"
        f"    <url>{safe_url}</url>\n"
        f"{pub_line}"
        f"{ret_line}"
        f"    <verification_scope>{scope}</verification_scope>\n"
        f"    <content>\n"
        f"      {safe_excerpt}\n"
        f"    </content>\n"
        f"  </web_source>"
    )


def format_web_evidence_envelope(
    web_blocks: list[str],
    *,
    status_note: str | None = None,
) -> str:
    """Wrap external web excerpts cleanly in an untrusted XML container."""
    if not web_blocks:
        note = sanitize_xml_text(
            status_note or "No external web search results were supplied for this turn."
        )
        return (
            '<web_context_evidence status="empty" trust_level="untrusted_external_web">\n'
            f"  {note}\n"
            "</web_context_evidence>"
        )
    joined = "\n".join(web_blocks)
    return (
        '<web_context_evidence trust_level="untrusted_external_web">\n'
        f"{joined}\n"
        "</web_context_evidence>"
    )
