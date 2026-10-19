# Changelog

Each release is on [GitHub Releases](https://github.com/0xPD33/commonplace/releases) with its signed APK and SHA-256. All releases use the same signing certificate, so each one installs over the one before and keeps the installed packs. The packs are in the Hugging Face dataset [0xPD33/commonplace-packs](https://huggingface.co/datasets/0xPD33/commonplace-packs), folder `packs-2026-09`. After 0.1.1 the folder got two new files: `minicpm5-2b.tar` and `commonplace-starter-small.tar`.

## [0.2.3] - 2026-10-19

### Added

- **A starter set for 8 GB phones** (8.1 GB): Wikipedia, Wikidata facts and the small model, MiniCPM5-2B. The set for 12 GB phones (11.3 GB) keeps Ling-3.0-tiny. The app shows the set for your phone first and marks it "For this phone".

### Changed

- **MiniCPM5-2B replaces LFM2.5-1.2B as the small model.** On 63 seed questions it scores 0.485 against 0.345, and its Apache-2.0 license has no revenue limit. A phone with less than 10 GB of RAM uses it by default and does not load Ling by itself, because Android kills the app at about 5.5 GB.
- **Better routing.** "How do I reverse a list in Python?" is a question, not a request to rewrite the last answer. "What should I know before I go?" no longer links the film "Before I Go". "And in the world?" asks the last question again for the new place.
- **Exact calculations.** "What is 15 percent of 240?" and "How many cups are in a liter?" get the exact value at once, with no model call.
- **Faster on a warm phone.** The app sends less evidence from a thermal headroom of 0.70, not 0.85. On a Pixel 10 the first word came after 5.9–7.5 s in 6 questions.
- **No duplicate topic chips.** "About Angkor Wat" no longer appears for the band "Angkor Wat" under an answer about the temple.


### Changed

- **Faster in long sessions.** A hot phone reads its prompt slowly, so the app sends less evidence when the phone reports heat: 400 instead of 600 tokens.
- **Number tables are counted correctly.** Ling reads each digit as one token. A table of exchange rates was 2.5 times larger than the app thought, and the app now cuts such a table to the budget. On a Pixel 10, "What is the exchange rate of the yen to the euro?" shows its first word after 12 s instead of 39 s.
- **Unit questions without a number.** "How many cups are in a liter?" gets a calculated line (1 L = 4.227 US cups). The answer says 4.23 US cups instead of 2.11.
- **Follow-ups after a surname.** When an answer names its subject only by a surname ("Armstrong played the trumpet…"), a follow-up such as "Was he also a singer?" still finds the right person.

### Removed

- **The planner**, an extra model call before some follow-ups. On a hot Pixel it took 35 to 66 s and mostly repeated the question. "Explain it for a ten year old" now takes 41 s from tap to answer instead of about two minutes.
- **Deep mode** ("Think harder"). It needed a model pack that does not fit in phone RAM, so no one could use it. Thinking mode gives a more careful answer.

Measured on a Pixel 10 with 29 questions in one conversation: no crash, first word after 18 s and the full answer after 41 s (medians), against 29 s and 67 s for 0.2.1. Part of the gain comes from a cooler phone at the start ([BENCHMARKS](docs/BENCHMARKS.md#pixel-10-the-build-after-021-2026-10-16)).

## [0.2.1] - 2026-10-15

### Changed

- **Follow-up questions find the right topic.** A follow-up searches with the first sentence of the answer it refers to, which usually names the subject. "Which countries does it flow through?" after "What is the longest river in Europe?" answers about the Volga, not the Danube.
- **A question two turns back works.** The app checks the last two answers and takes the one that the question fits best.
- **A new question keeps its own topic.** "What are the symptoms of dehydration?" asked after another topic no longer searches with that topic.

Measured on a Pixel 10 with 75 questions in three long conversations: correct answers rose from 27 to 37, and answers about the wrong topic fell from 3 to 1 ([BENCHMARKS](docs/BENCHMARKS.md#desktop-follow-up-topics-in-long-sessions-2026-10-15)).

## [0.2.0] - 2026-10-14

### Changed

- **Faster answers.** On a Pixel 10, the first card shows after 0.50 s at the median (0.91 s in 0.1.1). A new reranker, Ettin-17m distilled from Ettin-400m on passages from our own packs, ranks better and reranks 24 passages in about 300 ms. The APK is 38 MB smaller (285 MB).
- **Faster model start.** The app saves the prepared answer prompt to storage. After the app was closed, the model starts in about 16 s instead of 24–26 s.
- **Conversations.** A new question on another topic no longer carries the old topic. "Tell me more" brings new passages instead of the same ones.
- **Wikidata.** Facts are numbered sources that you can open and check. For a question such as "the country that hosted the 1964 Summer Olympics", the app follows one more fact (Japan) to find the answer.
- **Answer prompt.** The model sees two worked examples, and the question comes before and after the sources.
- **Think.** The Think switch is in the question box. A thinking answer shows its reasoning while it runs and folds it to "Thought for N s". "Think it through" asks the last question again with reasoning.
- **Interface.** New answers and sources come in with a short motion, which "Remove animations" in Android turns off. The source cards, the offline badge and the welcome screen are quieter. The timings line under answers is off for new installs.

Quality: on 63 seed questions with the phone's settings and the Starter set, Commonplace gets 53% of the score of Claude Opus 5.5 with web search ([EVAL](docs/EVAL.md)).

## [0.1.1] - 2026-10-06

### Fixed

- 0.1.0 installed only 2 of the 3 packs in the Starter set. Some published pack files store files with holes as GNU sparse tar entries, which 0.1.0 did not read. Install 0.1.1 over 0.1.0 and run Install from Downloads again.

## [0.1.0] - 2026-10-06

The first release.

- Answers from Wikipedia, Wikidata and other reference packs on the phone, with numbered citations and a source reader.
- No `INTERNET` permission. The packs come from Hugging Face through the browser, and the app checks every byte with SHA-256 during the import.
- An answer card with the best passage and Wikidata facts before the model writes. Ling-3.0-tiny writes the answer, and a small model pack serves phones with 8 GB of RAM.
- Comparisons, follow-up questions, thinking mode, voice input (a separate pack) and "My documents" for your own PDF, text and Markdown files.
- The Library with an on/off switch for each pack and a 50 GB limit.

[0.2.3]: https://github.com/0xPD33/commonplace/releases/tag/v0.2.3
[0.2.2]: https://github.com/0xPD33/commonplace/releases/tag/v0.2.2
[0.2.1]: https://github.com/0xPD33/commonplace/releases/tag/v0.2.1
[0.2.0]: https://github.com/0xPD33/commonplace/releases/tag/v0.2.0
[0.1.1]: https://github.com/0xPD33/commonplace/releases/tag/v0.1.1
[0.1.0]: https://github.com/0xPD33/commonplace/releases/tag/v0.1.0
