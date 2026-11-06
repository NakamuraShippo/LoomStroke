# LoomStroke User Guide — Full Translation Package

## Screenshot manual (current)

Open [`manual/index.html`](manual/index.html) for the current screenshot manual,
or [`manual/sam31.html`](manual/sam31.html) for the SAM 3.1 setup guide. Both offer
the application's 12 language choices. Translated pages use `_<locale>.html`
suffixes and share the Japanese screenshots. All pages work offline.

The Japanese pages are the sources. The other languages are machine translations;
their notice links back to the original. Screenshots retain Japanese UI labels.
Traditional Chinese and Taiwan Mandarin currently share the same Traditional
Chinese draft. Native-speaker review is still required before publication.

To update translations, use `tools/build_manual_translations.py --translate` from
the repository root with the project venv. This explicitly sends Japanese prose
to Google's public translation endpoint and caches each text by SHA-256 in
`user_data/cache/manual_translations_20261030/`. Service availability and request
limits may prevent completion; incomplete translations are not emitted. An
existing complete cache can be rebuilt offline by omitting `--translate`.
Task-local offline Qwen drafts can also populate the same cache; see the
[development record](../dev/manual_localization_20261030.md) for provenance.
Review translated prose after updates and run
`./venv/Scripts/python.exe -m pytest tests/unit/test_manual_translations.py -q`.

## Earlier guide

This package contains static HTML pages for all 12 language choices from the uploaded guide. `index.html` is a language landing page.

Files:
- `index_ja.html`
- `index_en.html`
- `index_zh_CN.html`
- `index_zh_Hant.html`
- `index_zh_TW.html`
- `index_ko.html`
- `index_es.html`
- `index_ar.html`
- `index_hi.html`
- `index_pt_BR.html`
- `index_bn.html`
- `index_ru.html`
- `ROADMAP.md` — Japanese user-facing development roadmap

`index_dynamic_original.html` preserves the original uploaded dynamic page for reference. See `TRANSLATION_QA.md` for the residual-check report.
