# Universal Model Providers & Local Inference in VerifyCraft

VerifyCraft is designed to be genuinely model-agnostic. It supports major cloud model providers, AWS-hosted models, and offline local inference servers through a unified, shared provider transport architecture.

---

## Provider Support Matrix

| Provider Identifier | Provider Type / Transport | Default Model | Environment Variable | Default Base URL | Local / Remote | Integration & Testing Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `deepseek` | OpenAI-Compatible | `deepseek-chat` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | Remote | Implemented & Hermetically Contract-Tested (Smoke-Tested when key configured) |
| `openai` | OpenAI-Compatible | `gpt-4o` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `kimi` / `moonshot` | OpenAI-Compatible | `moonshot-v1-8k` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `xai` / `grok` | OpenAI-Compatible | `grok-beta` | `XAI_API_KEY` | `https://api.x.ai/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `mistral` | OpenAI-Compatible | `mistral-large-latest` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `groq` | OpenAI-Compatible | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `cerebras` | OpenAI-Compatible | `llama3.1-70b` | `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `together` | OpenAI-Compatible | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` | `TOGETHER_API_KEY` | `https://api.together.xyz/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `fireworks` | OpenAI-Compatible | `accounts/fireworks/models/llama-v3p1-70b-instruct` | `FIREWORKS_API_KEY` | `https://api.fireworks.ai/inference/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `deepinfra` | OpenAI-Compatible | `meta-llama/Meta-Llama-3.1-70B-Instruct` | `DEEPINFRA_API_KEY` | `https://api.deepinfra.com/v1/openai` | Remote | Implemented & Hermetically Contract-Tested |
| `openrouter` | OpenAI-Compatible | `anthropic/claude-3.5-sonnet` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | Remote | Implemented & Hermetically Contract-Tested |
| `ollama` | OpenAI-Compatible (Local) | `llama3.1:latest` | `OLLAMA_API_KEY` (optional) | `http://127.0.0.1:11434/v1` | **Local** | Implemented & Hermetically Contract-Tested |
| `lmstudio` | OpenAI-Compatible (Local) | `local-model` | `LMSTUDIO_API_KEY` (optional) | `http://127.0.0.1:1234/v1` | **Local** | Implemented & Hermetically Contract-Tested |
| `vllm` | OpenAI-Compatible (Local) | `default` (user-supplied) | `VLLM_API_KEY` (optional) | `http://127.0.0.1:8000/v1` | **Local** | Implemented & Hermetically Contract-Tested |
| `llamacpp` / `llama.cpp` | OpenAI-Compatible (Local) | `default` (user-supplied) | `LLAMACPP_API_KEY` (optional) | `http://127.0.0.1:8080/v1` | **Local** | Implemented & Hermetically Contract-Tested |
| `openai-compatible` | OpenAI-Compatible (Custom) | User-supplied | `OPENAI_API_KEY` (optional for local) | User-supplied via `--base-url` | Flexible | Implemented & Hermetically Contract-Tested |
| `anthropic` / `claude` | Anthropic Messages API | `claude-3-5-sonnet-20241022` | `ANTHROPIC_API_KEY` | `https://api.anthropic.com` | Remote | Implemented & Hermetically Contract-Tested |
| `bedrock` / `aws-bedrock` | AWS Bedrock Converse API | `anthropic.claude-3-5-sonnet-20240620-v1:0` | AWS Standard Credential Chain | AWS Region Endpoint | Remote | Implemented & Hermetically Contract-Tested |
| `gemini` / `google` | Google GenAI SDK | `gemini-2.5-flash` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google API Endpoint | Remote | Implemented, Hermetically Tested & Empirical Release Gate |
| `deterministic` / `fake` | Deterministic Fixture | `deterministic` | None | None | **Local** | Implemented & Verified in CI |
| `none` / `model-free` | Model-Free Candidate Ingestion | `model-free` | None | None | **Offline** | Implemented & Zero-Model-Call Verified |

> [!NOTE]
> Testing status definitions:
> - **Implemented**: Transport adapter written with complete typed configurations and schema parsing.
> - **Hermetically Contract-Tested**: Tested against recorded and synthetic response shapes with zero live network calls in standard CI.
> - **Actually Smoke-Tested**: Executed against live external API endpoints when credentials or local servers are explicitly configured in the execution environment.

---

## Installation Options

VerifyCraft keeps its core package lightweight by providing optional dependency groups for specific providers:

```bash
# Core package (model-free review and deterministic verification engine)
pip install verifycraft

# OpenAI and OpenAI-compatible providers (DeepSeek, Grok, Kimi, Ollama, LM Studio, etc.)
pip install "verifycraft[openai]"

# Anthropic Claude native Messages API
pip install "verifycraft[anthropic]"

# AWS Bedrock Converse API
pip install "verifycraft[bedrock]"

# Google Gemini API
pip install "verifycraft[gemini]"

# All providers bundle
pip install "verifycraft[all-providers]"
```

Using `uv`:

```bash
uv add "verifycraft[openai]"
uv add "verifycraft[anthropic]"
uv add "verifycraft[bedrock]"
uv add "verifycraft[gemini]"
```

---

## Authentication & Security

### Credential Resolution Chain

1. **Provider-Specific Environment Variables**:
   VerifyCraft inspects provider-specific environment variables (`DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY`, `XAI_API_KEY`, `OPENAI_API_KEY`, etc.).
2. **AWS Bedrock Standard Resolution**:
   AWS Bedrock uses standard AWS credential resolution (boto3 Session handles `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, IAM instance roles, and AWS SSO). Static API keys are never required.
3. **Local Keyless Inference**:
   Local endpoints (`ollama`, `lmstudio`, `vllm`, `llamacpp`, or any custom loopback address) default to dummy local authorization (`api_key="local"`) when no key is set, enabling 100% offline usage without cloud API credentials.

### Privacy & Network Security Invariants

- **Explicit Disclosure**: VerifyCraft discloses before every review run whether code is being processed locally or transmitted to a remote cloud API.
- **Fail-Closed on Plain HTTP**: Plain HTTP (`http://`) is strictly rejected for non-loopback endpoints unless the user passes `--allow-insecure-http`. Loopback addresses (`127.0.0.1`, `localhost`, `::1`) are permitted plain HTTP by default.
- **Secret Redaction**: API keys and tokens are automatically sanitized and redacted (`[REDACTED]`) from error messages and logs.
- **No Implicit Fallback**: VerifyCraft never silently falls back from a local provider to a paid remote cloud provider, or from one paid provider to another.

---

## CLI Usage Examples

### DeepSeek (Cloud)

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"

verifycraft review . \
  --base main --head HEAD \
  --provider deepseek \
  --model deepseek-chat \
  --format comments
```

### OpenAI (Cloud)

```bash
export OPENAI_API_KEY="your-openai-api-key"

verifycraft review . \
  --base main --head HEAD \
  --provider openai \
  --model gpt-4o \
  --format json
```

### Kimi / Moonshot AI (Cloud)

```bash
export MOONSHOT_API_KEY="your-moonshot-api-key"

verifycraft review . \
  --base main --head HEAD \
  --provider kimi \
  --model moonshot-v1-8k
```

### xAI / Grok (Cloud)

```bash
export XAI_API_KEY="your-xai-api-key"

verifycraft review . \
  --base main --head HEAD \
  --provider xai \
  --model grok-beta
```

### Anthropic Claude (Cloud)

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"

verifycraft review . \
  --base main --head HEAD \
  --provider anthropic \
  --model claude-3-5-sonnet-20241022
```

### AWS Bedrock (Cloud)

```bash
# Standard AWS credentials via environment or AWS SSO
export AWS_REGION="us-east-1"

verifycraft review . \
  --base main --head HEAD \
  --provider bedrock \
  --model anthropic.claude-3-5-sonnet-20240620-v1:0
```

### Ollama (Local)

```bash
# Ensure Ollama is running locally: ollama serve
# Download model: ollama pull llama3.1

verifycraft review . \
  --base main --head HEAD \
  --provider ollama \
  --model llama3.1:latest
```

### LM Studio (Local)

```bash
# Start local server in LM Studio (default: http://127.0.0.1:1234)

verifycraft review . \
  --base main --head HEAD \
  --provider lmstudio \
  --model local-model
```

### Custom OpenAI-Compatible Endpoint

```bash
verifycraft review . \
  --base main --head HEAD \
  --provider openai-compatible \
  --base-url http://127.0.0.1:8000/v1 \
  --model served-model-id
```

### Model-Free Review (Zero LLM Calls)

```bash
# Ingest candidate findings from external host agent
verifycraft review . \
  --base main --head HEAD \
  --candidates findings.json \
  --format comments
```

---

## Python SDK Usage Examples

```python
from verifycraft.contracts.review import ReviewPolicy, ReviewRequest
from verifycraft.review.engine import ReviewEngine

engine = ReviewEngine()

# Example 1: Review using DeepSeek
req = ReviewRequest(
    repo_path=".",
    base_revision="main",
    head_revision="HEAD",
    provider="deepseek",
    model="deepseek-chat",
    policy=ReviewPolicy(timeout_seconds=60.0),
)
report = engine.review(req)
print(report.to_conventional_comments())

# Example 2: Review using local Ollama
req_ollama = ReviewRequest(
    repo_path=".",
    base_revision="main",
    head_revision="HEAD",
    provider="ollama",
    model="llama3.1:latest",
)
report_ollama = engine.review(req_ollama)

# Example 3: Model-free review ingesting external findings
req_model_free = ReviewRequest(
    repo_path=".",
    base_revision="main",
    head_revision="HEAD",
    provider="none",
    candidate_findings=[],
)
report_model_free = engine.review(req_model_free)
```

---

## Local Inference Quickstarts

### 1. Ollama Quickstart

1. Install Ollama: `brew install ollama` (macOS) or visit [ollama.com](https://ollama.com).
2. Start the service: `ollama serve`.
3. Pull a recommended code model: `ollama pull qwen2.5-coder:7b` or `ollama pull llama3.1`.
4. Run review with VerifyCraft:
   ```bash
   verifycraft review . --base main --head HEAD --provider ollama --model qwen2.5-coder:7b
   ```

### 2. LM Studio Quickstart

1. Download and install [LM Studio](https://lmstudio.ai/).
2. Load any GGUF model (e.g. Qwen 2.5 Coder or Llama 3.1).
3. Open the "Local Server" tab and click "Start Server" (runs on `http://localhost:1234`).
4. Run review with VerifyCraft:
   ```bash
   verifycraft review . --base main --head HEAD --provider lmstudio --model local-model
   ```

### 3. vLLM Quickstart

1. Launch vLLM server:
   ```bash
   python -m vllm.entrypoints.openai.api_server \
     --model Qwen/Qwen2.5-Coder-7B-Instruct \
     --port 8000
   ```
2. Run review with VerifyCraft:
   ```bash
   verifycraft review . \
     --base main --head HEAD \
     --provider vllm \
     --model Qwen/Qwen2.5-Coder-7B-Instruct
   ```

---

## Review Quality Gate & Security Policies

VerifyCraft enforces strict review quality and defensive execution invariants:

### 1. Safe Environment Loading Policy
- Automatic loading of `.env` files from untrusted working directories is disabled by default.
- Users can explicitly opt in to load environment variables using `--load-dotenv`, `--env-file <path>`, or setting `VERIFYCRAFT_LOAD_DOTENV=1`.

### 2. Network Endpoint Security
- Plain HTTP (`http://`) is strictly limited to local loopback addresses (`localhost`, `127.0.0.1`, `::1`).
- Unspecified addresses (`0.0.0.0`, `::`) are bind wildcards, not loopback interfaces, and require `--allow-insecure-http` when accessed over plain HTTP.
- Custom `base_url` settings across all providers (including Anthropic) undergo endpoint validation before client initialization.

### 3. Review Completeness & Diff Preservation
- Diffs are acquired deterministically without ever splitting hunks in half.
- When diffs exceed line limits, fair representation is allocated across changed files to ensure critical changes at the end of the patch are examined.
- When diff or context scope is truncated, the review scope is explicitly marked as `PARTIAL`. Incomplete reviews cannot be interpreted as proof of clean code.

### 4. Semantic Anchor Grounding
- Line anchors are verified against actual supporting symbols, definitions, or code tokens.
- When an LLM cites a valid symbol at a drifted line, VerifyCraft deterministically remaps to the actual definition or reference site.
- Findings that cannot be semantically grounded in code are downgraded to file-level comments rather than fabricated locations.

### 5. Severity, Verification Status, and Strict Mode
- Finding severity (Critical, High, Medium, Low, Info) is kept distinct from merge-blocking status (`blocking: bool`).
- Unverified model inferences are not silently promoted to blocking issues.
- Under `--strict` mode, VerifyCraft exits with code `2` only when actionable blocking issues are confirmed.

---

## Limitations & Token Accounting

- **JSON Mode Differences**: Some open-weights or local servers do not natively support OpenAI's `response_format={"type": "json_object"}`. VerifyCraft's shared OpenAI-compatible transport automatically detects this and gracefully falls back to prompt-guided JSON schema instructions.
- **Token Accounting**: Token usage is recorded whenever the provider supplies it in `response.usage` or `response.usage_metadata`. If a local server omits token counts, VerifyCraft reports `None` rather than fabricating synthetic counts.
- **Context Limits**: Local models with small context windows (e.g., 4k or 8k tokens) may truncate large diffs. VerifyCraft's `GitContextCollector` bounds context to prevent exceeding token limits.
