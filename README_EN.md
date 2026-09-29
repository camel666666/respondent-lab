# Respondent Lab

> Rehearse a survey before you send it.

**[Try the live demo →](https://camel666666.github.io/respondent-lab/)** · [中文说明](README.md)

Respondent Lab is an open-source workbench for **survey pretesting**. Set sample quotas for students and working adults, simulate a questionnaire, inspect distributions, and draft a transparent report. The included 1,000-person demo panel is entirely fictional.

## Quick start

Python 3.10+ is required. No API key is needed for the offline demo.

```bash
python3 -m pip install -e .
respondent-lab demo --sample-size 1000 --output result.json --report report.md
python3 -m http.server 8000 --directory web
```

Open `http://localhost:8000` and import `result.json` to explore the results. To try it without installation, run `PYTHONPATH=src python3 -m respondent_lab demo --sample-size 1000 --output result.json --report report.md` from the repository root.

The engine supports single choice, multiple choice, Likert, and open text questions. Closed answers use declared scenario weights; open text uses a deterministic local template by default. An explicit OpenAI provider option uses `gpt-6-sol` for open answers and optionally `gpt-6-astra` for a small quality review sample. No paid API call occurs in the default demo.

For an OpenAI-compatible subscription API, set `OPENAI_API_KEY` and `OPENAI_BASE_URL` in your own environment. The default endpoint mode is Responses API; set `OPENAI_API_MODE=chat_completions` only if your provider requires it. Test one fictional source record first and confirm the provider's model names, privacy terms, and actual billing before processing consented material.

## Responsible use

Simulated answers are predictions, even when the local source profile comes from a consenting real person. They are **not actual survey responses** and must not be presented as fieldwork. The repository contains no real-person data. See [the research guide](docs/research-guide.md) for validation and provenance guidance.

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed.
