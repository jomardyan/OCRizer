"""Language detection: the decision logic is unit-tested on text, the whole pipeline on real scans."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFilter

import factory
from ocrizer import language
from ocrizer.language import (
    DetectionResult,
    ProbeResult,
    chunk_paragraphs,
    classify_text,
    decide,
    detect_language,
    gather_evidence,
    parse_tsv,
    sample_pages,
)

POLISH = [
    "Umowa o kredyt konsumencki została zawarta pomiędzy bankiem a kredytobiorcą w dniu podpisania.",
    "Zgodnie z obowiązującymi przepisami dane osobowe klientów są przetwarzane wyłącznie w celu realizacji umowy.",
    "W przypadku opóźnienia w spłacie raty bank naliczy odsetki za opóźnienie w wysokości ustawowej.",
    "Wniosek o udzielenie pożyczki gotówkowej należy złożyć w formie pisemnej wraz z kompletem załączników.",
    "Posiedzenie zarządu odbędzie się w siedzibie spółki w Warszawie przy ulicy Świętokrzyskiej.",
    "Kupujący zobowiązuje się do zapłaty ceny w terminie czternastu dni od daty otrzymania faktury.",
    "Zażółć gęślą jaźń, czyli pangram, który zawiera wszystkie polskie litery z ogonkami i kreskami.",
    "Pracownik ma prawo do corocznego, nieprzerwanego i płatnego urlopu wypoczynkowego w wymiarze dwudziestu dni.",
]
ENGLISH = [
    "The loan agreement was concluded between the bank and the borrower on the date of signing.",
    "In accordance with applicable regulations, customer data is processed solely to perform the contract.",
    "In the event of a late payment the bank will charge default interest at the statutory rate.",
    "An application for a cash loan must be submitted in writing together with all required attachments.",
    "The board meeting will take place at the registered office of the company in London next Friday.",
    "The buyer undertakes to pay the price within fourteen days from the date of receiving the invoice.",
    "The quick brown fox jumps over the lazy dog, a sentence that contains every letter of the alphabet.",
    "Every employee is entitled to an annual, uninterrupted and paid holiday of twenty working days.",
]


def decide_text(paragraphs, *, confidence=95.0, pages=2) -> DetectionResult:
    words = sum(len(p.split()) for p in paragraphs)
    return decide(gather_evidence(paragraphs), mean_confidence=confidence, words=words, pages_sampled=pages)


@pytest.mark.parametrize("sentence", POLISH)
def test_classifier_recognises_polish_sentences(sentence):
    assert classify_text(sentence)[0] == "pl"


@pytest.mark.parametrize("sentence", ENGLISH)
def test_classifier_recognises_english_sentences(sentence):
    assert classify_text(sentence)[0] == "en"


def test_pure_polish_selects_pol():
    result = decide_text(POLISH)
    assert (result.language, result.confident) == ("pol", True)
    assert result.polish_share > 0.95


def test_pure_english_selects_eng():
    result = decide_text(ENGLISH)
    assert (result.language, result.confident) == ("eng", True)
    assert result.english_share > 0.95


def test_mixed_document_selects_both_languages():
    result = decide_text(POLISH[:4] + ENGLISH[:4])
    assert (result.language, result.confident) == ("pol+eng", True)
    assert result.polish_share > 0.3 and result.english_share > 0.3


def test_small_english_share_in_polish_document_still_selects_both():
    result = decide_text(POLISH * 2 + ENGLISH[:1])  # ~6% English
    assert result.language == "pol+eng"


def test_negligible_foreign_share_keeps_single_language():
    result = decide_text(POLISH * 4 + ENGLISH[:1])  # ~3% English
    assert result.language == "pol"


def test_english_text_with_polish_names_includes_polish():
    names = [
        "Contact persons: Łukasz Żółkiewski, Małgorzata Świątek, Grzegorz Brzęczyszczykiewicz "
        "and Zdzisław Wójcik from Gdańsk, Łódź and Kraków, ul. Świętokrzyska, Żoliborz.",
    ]
    result = decide_text(ENGLISH + names)
    assert result.language == "pol+eng"


@pytest.mark.parametrize(
    ("paragraphs", "confidence", "needle"),
    [
        ([], 90.0, "too little"),
        (["Faktura VAT 12/2024"], 95.0, "too little"),
        (POLISH, 30.0, "poor scan quality"),
        (["kdjs ladf qxzv wvrt mnbv cxzl kjhg fdsa poiu ytre wqaz xsed cfrv tgby hnuj mikl"] * 4, 90.0,
         "not confident"),
    ],
)
def test_fallback_when_uncertain(paragraphs, confidence, needle):
    result = decide_text(paragraphs, confidence=confidence)
    assert result.language == language.FALLBACK_LANGUAGE == "pol+eng"
    assert result.confident is False
    assert needle in result.reason


def test_classification_accuracy_on_sentence_corpus():
    """Documented accuracy of the classifier on this corpus (see README)."""
    hits = sum(classify_text(s)[0] == "pl" for s in POLISH) + sum(
        classify_text(s)[0] == "en" for s in ENGLISH
    )
    assert hits == len(POLISH) + len(ENGLISH)


def test_chunking_merges_short_paragraphs_and_drops_tiny_tail():
    chunks = chunk_paragraphs(["Faktura", "VAT", "nr", "12/2024", POLISH[0], "ok"])
    assert len(chunks) == 1 and chunks[0].startswith("Faktura VAT nr 12/2024 Umowa")


def test_sample_pages_spreads_evenly():
    assert sample_pages(list(range(100)), 4) == [0, 33, 66, 99]
    assert sample_pages([3, 5], 4) == [3, 5]
    assert sample_pages(list(range(10)), 1) == [5]
    assert sample_pages([2, 4, 8], 2) == [2, 8]


def test_parse_tsv_groups_words_into_paragraphs():
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "4\t1\t1\t1\t1\t0\t0\t0\t9\t9\t-1\t",
            "5\t1\t1\t1\t1\t1\t0\t0\t9\t9\t96.5\tUmowa",
            "5\t1\t1\t1\t1\t2\t0\t0\t9\t9\t91.0\tkredytu",
            "5\t1\t2\t1\t1\t1\t0\t0\t9\t9\t88.0\tInvoice",
            "5\t1\t2\t1\t1\t2\t0\t0\t9\t9\t-1\t  ",
        ]
    )
    result = parse_tsv(tsv)
    assert result.paragraphs == ("Umowa kredytu", "Invoice")
    assert result.confidences == (96.5, 91.0, 88.0)


def test_detect_language_uses_sampled_pages_only():
    rendered: list[int] = []

    def render(path, index):
        rendered.append(index)
        return Image.new("L", (10, 10))

    def probe(image):
        return ProbeResult(tuple(POLISH), (95.0,) * 150)

    result = detect_language("x.pdf", list(range(50)), sample_count=3, probe=probe, render=render)
    assert rendered == [0, 24, 49]
    assert result.language == "pol" and result.pages_sampled == 3


def test_detect_language_without_pages_falls_back():
    result = detect_language("x.pdf", [], probe=lambda i: pytest.fail("must not probe"))
    assert result.language == "pol+eng" and not result.confident


def test_parse_tsv_ignores_nonfinite_and_out_of_range_confidence():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [f"5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t{confidence}\tword"
            for confidence in ("nan", "inf", "101", "-1", "95")]
    result = parse_tsv("\n".join([header, *rows]))
    assert result.paragraphs == ("word",)
    assert result.confidences == (95.0,)


def test_language_detection_releases_probe_images_when_probe_fails():
    image = Image.new("L", (10, 10))

    def fail_probe(image):
        raise RuntimeError("probe failed")

    with pytest.raises(RuntimeError, match="probe failed"):
        detect_language("x.pdf", [0], render=lambda path, index: image, probe=fail_probe)
    with pytest.raises(ValueError, match="closed image"):
        image.getpixel((0, 0))


# --------------------------------------------------------------------- real OCR on rendered scans
@pytest.fixture
def probe_detect(docs):
    def run(pages, **image_kwargs):
        pdf = factory.scanned_pdf(docs / "scan.pdf", pages, **image_kwargs)
        return detect_language(pdf, list(range(len(pages))), sample_count=4)

    return run


@pytest.mark.integration
def test_scanned_polish_page_is_detected(probe_detect):
    result = probe_detect([factory.POLISH_TEXT])
    assert (result.language, result.confident) == ("pol", True)


@pytest.mark.integration
def test_scanned_english_page_is_detected(probe_detect):
    result = probe_detect([factory.ENGLISH_TEXT])
    assert (result.language, result.confident) == ("eng", True)


@pytest.mark.integration
def test_scanned_mixed_pages_are_detected_as_both(probe_detect):
    result = probe_detect([factory.POLISH_TEXT, factory.ENGLISH_TEXT])
    assert (result.language, result.confident) == ("pol+eng", True)
    assert result.pages_sampled == 2


@pytest.mark.integration
def test_scanned_page_mixing_languages_is_detected_as_both(probe_detect):
    result = probe_detect([factory.POLISH_TEXT[:2] + factory.ENGLISH_TEXT[:2]])
    assert (result.language, result.confident) == ("pol+eng", True)


@pytest.mark.integration
def test_unreadable_scan_falls_back_to_both_languages(docs):
    """Noise instead of text: Tesseract finds (almost) nothing, so the fallback must kick in."""
    image = Image.new("L", (2480, 1754), 255)
    draw = ImageDraw.Draw(image)
    for i in range(400):
        x, y = (i * 97) % 2300, (i * 53) % 1600
        draw.ellipse([x, y, x + 12, y + 9], fill=0)
    image = image.filter(ImageFilter.GaussianBlur(3))
    pdf = docs / "noise.pdf"
    image.save(pdf, "PDF", resolution=300.0)
    result = detect_language(pdf, [0])
    assert result.language == "pol+eng" and result.confident is False
