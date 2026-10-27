"""Automatic language detection (Polish / English) with a safe fallback.

Approach
--------
1. Up to ``detect_pages`` pages that need OCR are sampled, spread evenly over the document.
2. Each sampled page is rendered (200 DPI, grayscale) and passed through Tesseract with the
   combined ``pol+eng`` model. That model reads both languages, so the probe text is usable
   whichever language the page is in. Tesseract's TSV output gives words, their paragraph
   structure and per-word confidence.
3. Reading-order paragraphs are merged into chunks of roughly 60+ letters and each chunk is
   classified as Polish or English by `py3langid <https://github.com/adbar/py3langid>`_
   (a maintained fork of langid.py: offline, deterministic, restricted here to the two
   candidate languages). Chunks are weighted by their number of letters, which yields a
   per-document share of Polish, English and *uncertain* text. Classifying chunks rather
   than the whole text is what makes mixed documents and mixed pages visible.
4. Decision (all thresholds are the constants below):

   * too little text, poor OCR quality (mean word confidence), or too much uncertain text
     -> **fallback** to ``pol+eng``;
   * both languages present (minority share >= 5%) -> ``pol+eng``;
   * practically only Polish -> ``pol``; practically only English -> ``eng``, unless Polish
     diacritics occur in >= 1% of the letters (the English model cannot produce them) in which
     case ``pol+eng``.

``pol+eng`` is the superset, so every uncertain outcome degrades to the safe choice rather than
to a wrong single-language guess.

Limitations (see README): only the sampled pages are inspected, so a lone page in the other
language inside a long single-language document can be missed; use ``--language pol+eng``
when that matters. Very short or low-quality scans fall back to ``pol+eng``.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image

from ocrizer.errors import MissingDependencyError
from ocrizer.pdfinfo import render_page

FALLBACK_LANGUAGE = "pol+eng"
PROBE_LANGUAGE = "pol+eng"
PROBE_DPI = 200

MIN_WORDS = 20              # fewer sampled words than this -> fallback
MIN_USABLE_LETTERS = 80     # fewer classifiable letters than this -> fallback
MIN_MEAN_CONFIDENCE = 50.0  # mean Tesseract word confidence (0-100) below this -> fallback
MAX_UNCERTAIN_SHARE = 0.35  # more uncertain text than this -> fallback
CHUNK_MIN_LETTERS = 60      # paragraphs are merged until a chunk has at least this many letters
CHUNK_DROP_LETTERS = 15     # trailing chunks shorter than this carry no signal and are dropped
CONFIDENT_PROBABILITY = 0.85  # classifier probability needed to count a chunk as Polish/English
MINORITY_THRESHOLD = 0.05   # a second language above this share makes the document mixed
DIACRITIC_THRESHOLD = 0.01  # share of Polish diacritics that rules out English-only

POLISH_DIACRITICS = frozenset("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


@dataclass(frozen=True, slots=True)
class DetectionResult:
    language: str            # Tesseract spec to OCR with: "pol", "eng" or "pol+eng"
    confident: bool          # False when the fallback was used
    reason: str
    polish_share: float = 0.0
    english_share: float = 0.0
    uncertain_share: float = 0.0
    mean_confidence: float = 0.0
    words: int = 0
    pages_sampled: int = 0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    paragraphs: tuple[str, ...]
    confidences: tuple[float, ...]  # one per recognised word, 0-100


@dataclass(frozen=True, slots=True)
class Evidence:
    polish: int = 0       # letters in chunks classified as Polish
    english: int = 0
    uncertain: int = 0
    diacritics: int = 0   # Polish diacritic letters anywhere in the usable text

    @property
    def usable(self) -> int:
        return self.polish + self.english + self.uncertain


ProbeFunc = Callable[[Image.Image], ProbeResult]


@cache
def _identifier():
    from py3langid.langid import MODEL_DIR, MODEL_FILE, LanguageIdentifier

    identifier = LanguageIdentifier.from_model_file(MODEL_DIR / MODEL_FILE, norm_probs=True)
    identifier.set_languages(["pl", "en"])
    return identifier


def classify_text(text: str) -> tuple[str, float]:
    """Return ``("pl" | "en", probability)`` for a piece of text."""
    lang, prob = _identifier().classify(text)
    return lang, float(prob)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def chunk_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    """Merge consecutive short paragraphs so that each chunk is long enough to classify."""
    chunks: list[str] = []
    buffer: list[str] = []
    letters = 0
    for paragraph in paragraphs:
        buffer.append(paragraph)
        letters += _letters(paragraph)
        if letters >= CHUNK_MIN_LETTERS:
            chunks.append(" ".join(buffer))
            buffer, letters = [], 0
    if buffer and letters >= CHUNK_DROP_LETTERS:
        chunks.append(" ".join(buffer))
    return chunks


def gather_evidence(paragraphs: Iterable[str]) -> Evidence:
    pl = en = unc = dia = 0
    for chunk in chunk_paragraphs(paragraphs):
        n = _letters(chunk)
        dia += sum(ch in POLISH_DIACRITICS for ch in chunk)
        lang, prob = classify_text(chunk)
        if prob < CONFIDENT_PROBABILITY:
            unc += n
        elif lang == "pl":
            pl += n
        else:
            en += n
    return Evidence(pl, en, unc, dia)


def decide(
    evidence: Evidence, *, mean_confidence: float, words: int, pages_sampled: int
) -> DetectionResult:
    """Turn evidence into a Tesseract language spec (pure function; unit-tested)."""
    usable = evidence.usable
    base: dict[str, Any] = {
        "mean_confidence": mean_confidence, "words": words, "pages_sampled": pages_sampled,
    }

    def fallback(reason: str) -> DetectionResult:
        total = max(usable, 1)
        return DetectionResult(
            FALLBACK_LANGUAGE, False, reason,
            evidence.polish / total, evidence.english / total, evidence.uncertain / total, **base,
        )

    if words < MIN_WORDS or usable < MIN_USABLE_LETTERS:
        return fallback("too little text to identify the language reliably")
    if mean_confidence < MIN_MEAN_CONFIDENCE:
        return fallback(f"poor scan quality (mean OCR confidence {mean_confidence:.0f}%)")
    uncertain_share = evidence.uncertain / usable
    if uncertain_share > MAX_UNCERTAIN_SHARE:
        return fallback("language classifier was not confident enough")
    classified = evidence.polish + evidence.english
    if classified == 0:
        return fallback("no classifiable text")

    pl_share = evidence.polish / classified
    en_share = 1.0 - pl_share
    shares: dict[str, Any] = {
        "polish_share": evidence.polish / usable,
        "english_share": evidence.english / usable,
        "uncertain_share": uncertain_share,
    }

    if min(pl_share, en_share) >= MINORITY_THRESHOLD:
        return DetectionResult("pol+eng", True, "both Polish and English text found", **shares, **base)
    if pl_share > en_share:
        return DetectionResult("pol", True, "text is Polish", **shares, **base)
    if evidence.diacritics / max(usable, 1) >= DIACRITIC_THRESHOLD:
        return DetectionResult(
            "pol+eng", True, "English text with Polish characters", **shares, **base
        )
    return DetectionResult("eng", True, "text is English", **shares, **base)


def sample_pages(pages: Sequence[int], count: int) -> list[int]:
    """Pick up to ``count`` page indices spread evenly over ``pages``."""
    if count >= len(pages):
        return list(pages)
    if count == 1:
        return [pages[len(pages) // 2]]
    picks = {pages[round(i * (len(pages) - 1) / (count - 1))] for i in range(count)}
    return sorted(picks)


def parse_tsv(tsv: str) -> ProbeResult:
    """Parse Tesseract TSV output into paragraphs and word confidences."""
    paragraphs: dict[tuple[str, str, str], list[str]] = {}
    confidences: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 = word
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not text or not math.isfinite(conf) or not 0 <= conf <= 100:
            continue
        confidences.append(conf)
        paragraphs.setdefault((cols[1], cols[2], cols[3]), []).append(text)
    return ProbeResult(tuple(" ".join(words) for words in paragraphs.values()), tuple(confidences))


def tesseract_probe(image: Image.Image, language: str = PROBE_LANGUAGE) -> ProbeResult:
    """Run Tesseract on one image and return paragraphs plus confidences.

    The image is piped through stdin: this avoids passing non-ASCII temp paths to Tesseract,
    which is unreliable on Windows. The argument list contains no user-controlled data and no
    shell is involved.
    """
    exe = shutil.which("tesseract")
    if exe is None:
        raise MissingDependencyError("Tesseract executable not found.")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(  # noqa: S603
        [exe, "stdin", "stdout", "-l", language, "--psm", "3", "tsv"],
        input=buffer.getvalue(), capture_output=True, timeout=180, check=False, **kwargs,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise MissingDependencyError(f"Tesseract language probe failed: {detail[0]}")
    return parse_tsv(proc.stdout.decode("utf-8", "replace"))


def detect_language(
    pdf_path: Path,
    candidate_pages: Sequence[int],
    *,
    sample_count: int = 4,
    probe: ProbeFunc = tesseract_probe,
    render: Callable[[Path, int], Image.Image] | None = None,
) -> DetectionResult:
    """Detect the language to OCR ``pdf_path`` with, looking only at ``candidate_pages``."""
    if not candidate_pages:
        return DetectionResult(FALLBACK_LANGUAGE, False, "no pages to analyse")
    render = render or (lambda p, i: render_page(p, i, PROBE_DPI))
    paragraphs: list[str] = []
    confidences: list[float] = []
    sampled = sample_pages(candidate_pages, sample_count)
    for index in sampled:
        image = render(pdf_path, index)
        try:
            result = probe(image)
        finally:
            image.close()
        paragraphs.extend(result.paragraphs)
        confidences.extend(result.confidences)
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    evidence = gather_evidence(paragraphs)
    return decide(
        evidence, mean_confidence=mean_conf, words=len(confidences), pages_sampled=len(sampled)
    )
