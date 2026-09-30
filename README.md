# ai-code-reviewer

Reviews a unified diff and prints structured findings (markdown or JSON).

Default path is offline heuristics. Pass `--llm` with `OPENAI_API_KEY` (or any OpenAI-compatible base URL) for model review.

Eval fixtures under `evals/fixtures/` check that known issues are still caught.

## Usage

```bash
export PYTHONPATH=src
python3 -m ai_code_reviewer review --diff-file evals/fixtures/hardcoded-secret/diff.patch
python3 -m ai_code_reviewer eval --fixtures evals/fixtures
```

## Optional LLM

```bash
OPENAI_API_KEY=sk-... python3 -m ai_code_reviewer review --diff-file pr.diff --llm --format json

# against a local gateway:
OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=dev-gateway-key \
  python3 -m ai_code_reviewer review --diff-file pr.diff --llm
```

## GitHub Action

Composite action: `action.yml`. CI workflow runs the eval suite on push/PR.

## License

MIT
