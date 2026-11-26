"""Unit tests for Page-Aware Deterministic Chunking and multilingual-e5-small tokenizer."""


from app.modules.knowledge_base.chunking import (
    MAX_MODEL_INPUT_TOKENS,
    MAX_RAW_CHUNK_TOKENS,
    PageInput,
    chunk_document_pages,
    count_passage_tokens,
    count_tokens,
    validate_chunk_tokens,
)


def test_tokenizer_counts_and_passage_prefix():
    """Verify tokenizer counts and passage prefix overhead."""
    text = "Computer Systems: A Programmer's Perspective"
    raw_tokens = count_tokens(text)
    passage_tokens = count_passage_tokens(text)

    assert raw_tokens > 0
    # passage: adds 4 tokens (<s>, 'passage', ':', </s>)
    assert passage_tokens == raw_tokens + 4
    is_valid, r_cnt, p_cnt = validate_chunk_tokens(text)
    assert is_valid is True
    assert r_cnt == raw_tokens
    assert p_cnt == passage_tokens


def test_empty_and_whitespace_pages():
    """Empty or whitespace-only pages produce no chunks."""
    pages = [
        PageInput(page_number=1, text="", has_text=False),
        PageInput(page_number=2, text="   \n\t  ", has_text=True),
        PageInput(page_number=3, text="", has_text=True),
    ]
    chunks = chunk_document_pages(pages)
    assert chunks == []


def test_single_page_under_target_size():
    """Small page emits a single chunk with exact page provenance."""
    text = (
        "Operating systems provide an abstraction layer between hardware and software. "
        "Key concepts include processes, virtual memory, file systems, and concurrency."
    )
    pages = [PageInput(page_number=1, text=text, has_text=True)]
    chunks = chunk_document_pages(pages)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_index == 0
    assert chunk.page_number_start == 1
    assert chunk.page_number_end == 1
    assert chunk.raw_text == text
    assert chunk.character_count == len(text)
    assert chunk.token_count <= MAX_RAW_CHUNK_TOKENS
    assert len(chunk.content_hash) == 64
    assert len(chunk.page_spans) == 1
    assert chunk.page_spans[0]["page_number"] == 1


def test_multipage_spanning_chunk():
    """Paragraphs spanning across physical pages correctly record page_number_start and end."""
    page1_text = (
        "Chapter 3: Machine-Level Representation of Programs.\n\n"
        "Computers execute machine code, which consists of elementary instructions encoded as byte sequences. "
        "Compilers generate these instructions based on high-level source code."
    )
    page2_text = (
        "Assembly code is a human-readable representation of machine code. "
        "Understanding assembly code helps programmers write more efficient high-level programs."
    )

    pages = [
        PageInput(page_number=1, text=page1_text, has_text=True),
        PageInput(page_number=2, text=page2_text, has_text=True),
    ]
    chunks = chunk_document_pages(pages)

    assert len(chunks) >= 1
    # Check that chunks correctly track page numbers
    all_pages = {c.page_number_start for c in chunks} | {c.page_number_end for c in chunks}
    assert 1 in all_pages
    assert 2 in all_pages
    for chunk in chunks:
        assert chunk.page_number_start <= chunk.page_number_end
        assert chunk.token_count <= MAX_RAW_CHUNK_TOKENS


def test_hard_token_ceiling_enforcement_on_huge_paragraph():
    """A massive unbroken text block is safely segmented so every chunk is <= 420 tokens."""
    # 2,000 words of repetitive text
    sentence = "Virtual memory simplifies memory management and provides isolation between processes. "
    huge_text = sentence * 120

    pages = [PageInput(page_number=5, text=huge_text, has_text=True)]
    chunks = chunk_document_pages(pages)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= MAX_RAW_CHUNK_TOKENS
        assert chunk.token_count > 0
        assert chunk.character_count == len(chunk.raw_text)
        passage_toks = count_passage_tokens(chunk.raw_text)
        assert passage_toks <= MAX_MODEL_INPUT_TOKENS


def test_bengali_sentence_boundary_support():
    """Bengali academic text splits cleanly at dāṛi (।) punctuation and respects token limits."""
    bengali_text = (
        "কম্পিউটার বিজ্ঞান হলো এমন একটি শাস্ত্র যা তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে আলোচনা করে। "
        "অপারেটিং সিস্টেম হলো কম্পিউটারের হার্ডওয়্যার এবং সফটওয়্যার রিসোর্স পরিচালনার মূল ব্যবস্থা। "
        "মেমরি ম্যানেজমেন্ট প্রোগ্রামের নির্বাহকে গতিশীল ও নিরাপদ রাখে।"
    )
    pages = [PageInput(page_number=1, text=bengali_text, has_text=True)]
    chunks = chunk_document_pages(pages)

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.token_count <= MAX_RAW_CHUNK_TOKENS
        assert "কম্পিউটার" in chunk.raw_text
        passage_toks = count_passage_tokens(chunk.raw_text)
        assert passage_toks <= MAX_MODEL_INPUT_TOKENS


def test_hindi_sentence_boundary_support():
    """Hindi academic text splits cleanly at danda (।, ॥) punctuation and respects token limits."""
    hindi_text = (
        "कंप्यूटर विज्ञान सूचना और संगणना के सैद्धांतिक आधारों का अध्ययन है। "
        "ऑपरेटिंग सिस्टम कंप्यूटर हार्डवेयर और उपयोगकर्ता अनुप्रयोगों के बीच एक सेतु के रूप में कार्य करता है। "
        "यह मेमोरी प्रबंधन, प्रक्रिया शेड्यूलिंग और फ़ाइल सिस्टम को नियंत्रित करता है।"
    )
    pages = [PageInput(page_number=1, text=hindi_text, has_text=True)]
    chunks = chunk_document_pages(pages)

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.token_count <= MAX_RAW_CHUNK_TOKENS
        assert "कंप्यूटर" in chunk.raw_text
        passage_toks = count_passage_tokens(chunk.raw_text)
        assert passage_toks <= MAX_MODEL_INPUT_TOKENS


def test_chunking_determinism():
    """Chunking the same pages multiple times produces identical chunks and hashes."""
    text = (
        "Data structures and algorithms form the backbone of computer science education. "
        "Binary search trees, heaps, graphs, and dynamic programming are core concepts.\n\n"
        "Students must understand asymptotic complexity and Big-O notation."
    )
    pages = [
        PageInput(page_number=1, text=text, has_text=True),
        PageInput(page_number=2, text=text, has_text=True),
    ]

    run_1 = chunk_document_pages(pages)
    run_2 = chunk_document_pages(pages)

    assert len(run_1) == len(run_2)
    for c1, c2 in zip(run_1, run_2, strict=True):
        assert c1.chunk_index == c2.chunk_index
        assert c1.raw_text == c2.raw_text
        assert c1.token_count == c2.token_count
        assert c1.content_hash == c2.content_hash
        assert c1.page_number_start == c2.page_number_start
        assert c1.page_number_end == c2.page_number_end
