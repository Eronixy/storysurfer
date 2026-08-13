from __future__ import annotations

from storysurfer.editorial.clean import clean_for_speech, sentence_extract


def test_cleanup_preserves_link_labels_and_redacts_identifiers() -> None:
    cleaned = clean_for_speech(
        "Message **u/example** at person@example.com or +1 (555) 123-4567. "
        "Read [the update](https://example.com/update). AITA?",
        pronunciations=(("AITA", "am I the asshole"),),
    )

    assert "u/example" not in cleaned.text
    assert "person@example.com" not in cleaned.text
    assert "555" not in cleaned.text
    assert "the update" in cleaned.text
    assert "https://" not in cleaned.text
    assert "am I the asshole" in cleaned.text
    assert cleaned.redactions == ("email", "phone", "username")


def test_sentence_extract_never_cuts_a_sentence() -> None:
    extracted = sentence_extract(
        "First complete sentence here. Second sentence should not fit in the budget.",
        5,
    )

    assert extracted.text == "First complete sentence here."
    assert extracted.shortened
