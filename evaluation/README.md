# Local ASR evaluation

P0 replays the same local WAV through multiple SeACo hotword configurations.
Recordings, manifests, transcripts, and generated reports are written under
`runtime/p0/` and remain ignored by Git.

Keep filename-to-reference labels in the ignored
`runtime/p0/references.json`. Copy `evaluation/references.example.json` as a
starting point; never hard-code real recording timestamps or private speech in
the evaluation source.

Run the known-term matrix and then the full historical pair:

```bash
./.venv/bin/python evaluation/p0_hotword_ab.py --refresh-manifest --only-labeled
./.venv/bin/python evaluation/p0_hotword_ab.py --configs no_hotword,all_effective
```

The runner appends one JSON object per completed audio/configuration pair and
resumes by default. Regenerate the report without loading a model:

```bash
./.venv/bin/python evaluation/p0_hotword_ab.py --report-only
```

Only samples whose intended text is known receive a reference. Unlabeled
history is used to measure decode drift and latency, never as fabricated truth.

P1 uses the P0 no-hotword result as a stand-in for the live draft and measures
conservative, utterance-scoped selection both with and without a matching app:

```bash
./.venv/bin/python evaluation/p1_dynamic_ab.py
```
