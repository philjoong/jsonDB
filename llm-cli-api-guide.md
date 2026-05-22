# Claude CLI / Codex CLI / OpenAI API 연동 가이드

이 문서는 이 프로젝트의 `llm_backends.py` 구현을 기준으로, 다른 프로젝트에서도 Claude CLI, Codex CLI, OpenAI API를 같은 패턴으로 붙일 수 있도록 정리한 개발 참고 문서입니다.

작성 기준일: 2026-05-12

## 1. 이 프로젝트의 선택 구조

이 프로젝트는 하이라이트 분석 백엔드를 아래 3개 중 하나로 선택합니다.

| 백엔드 | 설정값 | 인증 방식 | 호출 방식 | 주 용도 |
| --- | --- | --- | --- | --- |
| Claude CLI | `claude_cli` | Claude CLI 로그인 | `claude -p ...` | 로컬 Claude Code CLI를 통한 분석 |
| Codex CLI | `codex_cli` | Codex CLI 로그인 또는 OpenAI 인증 | `codex exec ...` | 로컬 Codex CLI를 통한 비대화형 분석 |
| OpenAI API | `openai_api` | `.env`의 `OPENAI_API_KEY` | `POST /v1/responses` | 서버/앱에서 직접 API 호출 |

관련 파일:

- `llm_backends.py`: 세 백엔드 공통 호출부
- `config.example.json`: 백엔드 선택 및 실행 파일/모델 설정 예시
- `.env`: API 키 등 비밀정보 저장 위치

주요 설정값:

```json
{
  "highlight_analysis_backend": "claude_cli",
  "highlight_additional_analysis_backend": "",
  "claude_cli_bin": "claude",
  "codex_cli_bin": "codex.cmd",
  "codex_cli_model": "",
  "openai_model": "gpt-5-mini"
}
```

`.env` 예시:

```env
OPENAI_API_KEY=your-openai-api-key
OPENAI_API_BASE=https://api.openai.com/v1
```

`OPENAI_API_BASE`는 생략하면 `https://api.openai.com/v1`을 사용합니다.

## 2. 공통 설계 원칙

CLI/API 연동 코드는 아래 인터페이스로 통일하는 것이 유지보수에 좋습니다.

```python
def invoke_prompt(
    *,
    prompt: str,
    backend: str,
    json_schema: dict | None,
    response_path: str,
    stdout_path: str,
    stderr_path: str,
    meta_path: str,
) -> str | None:
    ...
```

권장 원칙:

- 프롬프트, 원본 응답, stderr, 메타데이터를 각각 파일로 남깁니다.
- 타임아웃을 반드시 둡니다.
- CLI 실행 파일 경로는 설정으로 받습니다.
- JSON이 필요한 작업은 가능하면 JSON Schema 또는 후처리 검증을 사용합니다.
- 실패 시 예외를 삼키지 말고 `stderr_path`, `meta_path`에 원인을 기록합니다.
- `.env`, 토큰, 쿠키, 다운로드 산출물은 커밋하지 않습니다.

## 3. Claude CLI 연동

### 설치와 로그인

Claude CLI는 PC에 설치하고 최초 1회 로그인해야 합니다. 설치/로그인 방식은 조직 환경에 따라 다를 수 있으므로 현재 공식 문서를 확인합니다.

설치 확인:

```powershell
claude --version
claude --help
```

### 비대화형 호출 패턴

Claude Code CLI는 `-p` 또는 `--print`로 비대화형 실행을 지원합니다. 공식 CLI reference에 따르면 `--output-format`은 `text`, `json`, `stream-json`를 지원하고, `--json-schema`는 print mode에서 JSON Schema에 맞춘 검증된 JSON 출력을 요청할 때 사용합니다.

텍스트 출력:

```powershell
"요약해줘" | claude -p --output-format text
```

JSON envelope 출력:

```powershell
"요약해줘" | claude -p --output-format json
```

JSON Schema 출력:

```powershell
$schema = '{"type":"object","additionalProperties":false,"properties":{"summary":{"type":"string"}},"required":["summary"]}'
"요약해줘" | claude -p --output-format json --json-schema $schema
```

이 프로젝트의 호출 형태:

```python
cmd = [
    resolved_bin,
    "-p",
    "--output-format",
    "json",
    "--json-schema",
    json.dumps(schema_for_cli, ensure_ascii=False),
]
```

Claude CLI의 `--output-format json` 응답은 일반적으로 메타데이터가 포함된 envelope입니다. 따라서 코드에서는 `structured_output`이 있으면 우선 사용하고, 없으면 `result` 필드를 꺼내 쓰는 방식이 안전합니다.

주의할 점:

- array 같은 top-level non-object schema가 필요한 경우, CLI가 object schema를 요구할 수 있으므로 `{ "result": [...] }` 형태로 감싸고 응답 후 다시 꺼냅니다.
- Windows GUI 앱에서 실행할 때 콘솔 창이 뜨지 않도록 `subprocess.CREATE_NO_WINDOW`를 사용합니다.
- CLI가 설치되어 있어도 로그인 세션이 없으면 실패할 수 있으므로 stderr를 사용자에게 노출 가능한 로그로 남깁니다.

## 4. Codex CLI 연동

### 설치와 로그인

OpenAI Codex CLI 공식 문서와 GitHub README 기준 설치 예시는 다음과 같습니다.

```powershell
npm install -g @openai/codex
codex --version
codex
```

Windows에서는 환경과 버전에 따라 `codex` 또는 `codex.cmd`가 PATH에 잡힙니다. 이 프로젝트는 기본값으로 `codex.cmd`를 사용합니다.

### 비대화형 호출 패턴

Codex CLI는 `codex exec`로 비대화형 실행을 지원합니다. OpenAI의 Codex CLI README는 `codex exec PROMPT` 또는 stdin 입력으로 자동화 작업을 실행할 수 있다고 설명합니다.

기본 실행:

```powershell
codex exec "이 저장소 구조를 요약해줘"
```

stdin 입력:

```powershell
"이 텍스트를 JSON으로 요약해줘" | codex exec -
```

이 프로젝트의 호출 형태:

```python
cmd = [
    resolved_bin,
    "exec",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--color",
    "never",
    "--output-last-message",
    response_path,
    "-",
]
```

모델 오버라이드가 있으면 `--model <model>`을 추가합니다.

```python
if codex_cli_model.strip():
    cmd[2:2] = ["--model", codex_cli_model.strip()]
```

운영 팁:

- 분석 전용이면 `--sandbox read-only`를 기본으로 둡니다.
- 자동화 결과만 파일로 받고 싶으면 `--output-last-message <path>`를 사용합니다.
- CI나 GUI에서 ANSI 컬러가 섞이면 파싱이 어려우므로 `--color never`를 사용합니다.
- git 저장소가 아닌 작업 디렉터리에서 실행할 수 있어야 하면 `--skip-git-repo-check`를 사용합니다.
- Codex CLI는 버전별 옵션 변화가 있을 수 있으므로 새 프로젝트 적용 전 `codex exec --help`를 확인합니다.

## 5. OpenAI API 연동

### 인증

서버나 GUI 앱에서 직접 호출할 때는 `.env` 또는 환경변수에 API 키를 둡니다.

```env
OPENAI_API_KEY=your-openai-api-key
```

Python에서 로드:

```python
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY", "").strip()
```

API 키는 절대 `config.json`, README, 로그, git diff에 넣지 않습니다.

### Responses API 기본 요청

이 프로젝트는 OpenAI Responses API의 `POST /v1/responses`를 직접 호출합니다. 공식 API reference 기준으로 `input`에는 텍스트 입력을 줄 수 있고, 응답의 content에는 `output_text` 타입이 포함됩니다.

최소 요청:

```python
payload = {
    "model": "gpt-5-mini",
    "input": prompt,
}
```

HTTP 요청:

```python
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    "https://api.openai.com/v1/responses",
    data=body,
    method="POST",
)
req.add_header("Content-Type", "application/json")
req.add_header("Authorization", f"Bearer {api_key}")
```

응답 텍스트 추출:

```python
raw_output = str(parsed.get("output_text", "") or "").strip()
if not raw_output:
    raw_output = extract_output_text(parsed).strip()
```

fallback 추출 함수:

```python
def extract_output_text(payload: dict) -> str:
    texts = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text", "")
                if text:
                    texts.append(str(text))
    return "\n".join(texts)
```

### Structured Outputs

OpenAI 공식 문서는 JSON Schema 기반 Structured Outputs를 권장합니다. Responses API에서는 `text.format.type = "json_schema"` 형태로 스키마를 전달합니다.

요청 예시:

```python
payload = {
    "model": "gpt-5-mini",
    "input": prompt,
    "text": {
        "format": {
            "type": "json_schema",
            "name": "highlight_result",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["summary", "score"],
            },
            "strict": True,
        }
    },
}
```

주의할 점:

- `strict: true`에서는 지원되는 JSON Schema subset만 사용할 수 있습니다.
- 모든 object에는 `additionalProperties: false`를 명시하는 편이 안전합니다.
- required 필드는 누락 없이 정의합니다.
- top-level array가 필요하면 이 프로젝트처럼 `{ "result": [...] }` object로 감싸고 응답 후 `result`를 꺼내는 방식이 호환성이 좋습니다.
- 모델이 거절(refusal)하거나 빈 응답을 줄 수 있으므로 JSON parse 실패, 빈 output, HTTP error를 모두 분기 처리합니다.

## 6. 세 방식 비교

| 항목 | Claude CLI | Codex CLI | OpenAI API |
| --- | --- | --- | --- |
| 장점 | 로컬 Claude Code 설정 활용, JSON Schema CLI 옵션 | 코드베이스 분석/자동화에 적합, sandbox 옵션 | 서버/앱 배포에 적합, 의존성이 명확함 |
| 단점 | 설치/로그인 상태 의존 | CLI 버전/옵션 변화 확인 필요 | API 키/요금/네트워크 처리 필요 |
| 추천 상황 | 개발자 PC에서 분석 작업 자동화 | 저장소 기반 코드 작업 자동화 | 제품 기능, 서버 배치, GUI 앱 |
| 출력 제어 | `--output-format`, `--json-schema` | `--output-last-message` | `text.format.json_schema` |
| 보안 기준 | 로그인 세션 관리 | sandbox mode 설정 | API 키 비밀 관리 |

## 7. 다른 프로젝트에 붙일 때 체크리스트

1. 백엔드 설정값을 config에 둡니다.

```json
{
  "llm_backend": "openai_api",
  "claude_cli_bin": "claude",
  "codex_cli_bin": "codex.cmd",
  "codex_cli_model": "",
  "openai_model": "gpt-5-mini"
}
```

2. `.env.example`에는 키 이름만 넣습니다.

```env
OPENAI_API_KEY=
OPENAI_API_BASE=https://api.openai.com/v1
```

3. 공통 호출 함수는 `str | None`처럼 실패 가능성을 표현합니다.

4. CLI stdout/stderr와 API raw response를 파일로 남깁니다.

5. JSON 결과는 schema, parser, validator 중 하나로 검증합니다.

6. 타임아웃 기준을 작업 성격에 맞게 둡니다.

- 짧은 요약: 60-180초
- 긴 코드/문서 분석: 300-600초
- 대용량 배치: 청크 분할 후 개별 타임아웃

7. 실패 로그에는 secret이 들어가지 않게 합니다.

8. 운영 전 아래 명령으로 설치 상태를 확인합니다.

```powershell
claude --version
codex --version
python -c "import os; print(bool(os.getenv('OPENAI_API_KEY')))"
```

## 8. 장애 대응

| 증상 | 확인할 것 | 대응 |
| --- | --- | --- |
| `FileNotFoundError` | CLI가 PATH에 있는지 | `claude_cli_bin`, `codex_cli_bin`에 절대경로 또는 올바른 명령명 지정 |
| CLI 응답이 비어 있음 | 로그인, 권한, stderr | stderr 파일과 CLI 단독 실행 확인 |
| Codex가 git repo 오류를 냄 | 작업 디렉터리 | `--skip-git-repo-check` 사용 |
| ANSI 코드가 섞임 | CLI color 설정 | `--color never` 사용 |
| OpenAI HTTP 401 | API 키 | `.env`의 `OPENAI_API_KEY` 확인 |
| OpenAI HTTP 400 | schema/model/payload | `text.format`, `required`, `additionalProperties` 확인 |
| OpenAI 응답 파싱 실패 | 응답 구조 | `output_text` 우선, 없으면 `output[].content[].text` fallback |
| timeout | 입력이 너무 큼 | 청크 분할, max output 제한, 작업 단순화 |

## 9. 보안 기준

- `.env`, `config.json`, `cookies/`, `state.json`, `downloads/`, 로그 파일은 민감정보가 들어갈 수 있으므로 커밋 전 diff를 확인합니다.
- CLI 프롬프트에 토큰, 쿠키, 사내 비밀 URL을 그대로 넣지 않습니다.
- API 키는 환경변수 또는 secret manager에서 주입합니다.
- 디버그 로그에 Authorization header를 남기지 않습니다.
- Codex CLI 자동화는 기본적으로 `--sandbox read-only`부터 시작하고, 쓰기가 필요한 작업만 `workspace-write`로 올립니다.

## 10. 참고 링크

- Claude Code CLI reference: https://code.claude.com/docs/en/cli-usage
- Claude Code headless / SDK usage: https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless
- OpenAI Codex CLI docs: https://developers.openai.com/codex/cli
- OpenAI Codex GitHub README: https://github.com/openai/codex/blob/main/codex-rs/README.md
- OpenAI Responses API create reference: https://platform.openai.com/docs/api-reference/responses/create
- OpenAI Structured Outputs guide: https://platform.openai.com/docs/guides/structured-outputs
