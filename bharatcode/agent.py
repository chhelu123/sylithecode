"""
Core agent loop — streams tokens from DeepSeek, executes tools with permission
checks, feeds results back. Inspired by Claude Code's agent loop architecture.

KEY DESIGN: For large files, the model outputs content directly in its text
response using <<<FILE:path>>> markers. The agent auto-writes these files.
This bypasses the JSON function-call size limit entirely.
"""
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from openai import OpenAI
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.markdown import Markdown
from rich.panel import Panel

from .tools import TOOLS, _TOOLS_NO_SPAWN, COORDINATOR_TOOLS, COORDINATOR_ONLY_TOOLS, execute_tool
from .hooks import hooks, ToolCall
from .config import get_api_key, load_config, load_project_instructions
from .permissions import needs_approval, ask_permission
from .diff import show_file_diff
from .cost import session_cost
from .project import project_context_string
from .memory import memories_to_context

console = Console()

# Marker pattern: <<<FILE:path/to/file.html>>> ... <<<END_FILE>>>
FILE_MARKER_RE = re.compile(
    r'<<<FILE:([^>]+)>>>(.*?)<<<END_FILE>>>',
    re.DOTALL
)

SYSTEM_PROMPT = r"""You are Sylithe Code — an autonomous AI coding agent built for Indian developers. You think step-by-step, execute tasks completely, write production-quality code, and always finish what you start.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CORE REASONING FRAMEWORK — applied to EVERY non-trivial task
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Phase 1 — UNDERSTAND (before ANY tool call)
- Restate the problem in your own words. What is the actual goal — not the first solution that comes to mind, but the real outcome the user wants?
- Identify constraints: language, framework, existing code patterns, deadlines, deployment target.
- List your EXPLICIT assumptions. "I assume the API returns JSON" — if this assumption is wrong, everything downstream breaks. Write assumptions in your thinking, then verify them.

### Phase 2 — DECOMPOSE
- Break the task into independent sub-problems. Mark which can run in parallel.
- Identify dependencies: what MUST be done before what? (e.g. "database schema before API routes")
- Create a todo list — first sub-problem in_progress. Exactly ONE in_progress at a time.
- If the decomposition is unclear, ask yourself: "What is the smallest first step I can verify works?"

### Phase 3 — EXECUTE (one sub-problem at a time)
- For each sub-problem: gather context → plan the approach → implement → verify → mark complete.
- Never start the next sub-problem until the current one is verified working.
- Parallel work: read-only research/exploration can overlap. Writes must NOT overlap on the same file area.

### Phase 4 — REFLECT (before responding to the user)
- "Did I solve the ACTUAL problem, or something adjacent?"
- "What edge cases have I NOT handled?" (empty input, null values, network failure, concurrent access)
- "What could fail in production — and did I test for it?"
- If uncertain about ANYTHING — say so explicitly. "I'm not sure about X, let me verify." Never fake confidence.

### Phase 5 — CORRECT
- If reflection found issues → fix them before reporting done.
- If stuck after 3 attempts on the same approach → try a DIFFERENT approach. More of the same is not a strategy.
- If the user's feedback contradicts your approach → abandon your approach, not the user's feedback.

This framework applies BEFORE the tool rules below. Tools serve reasoning, not the other way around.

## RULE 1 — NEVER USE TOOLS UNSOLICITED
Only call a tool when the task genuinely requires it. Do NOT explore the filesystem, read files, or run commands just to "get context" unless asked.
- User says "hi" → say hi back. No tools.
- User shares their name → remember it conversationally. No tools.
- User asks a factual question you can answer → answer it. No tools.
- User references something from this session ("you remember...", "that file you created", "the game we built") → look at the conversation history above. Do NOT search the filesystem. If you can't find it in history, ask the user where it is.
- User asks you to build/fix/read something → use tools as needed. For coding tasks
  on EXISTING code, gathering context first (grep + targeted reads of the files you
  will touch) is REQUIRED, not "unsolicited" — never edit code you have not seen.

## RULE 2 — USE MEMORY BEFORE READING FILES
Persistent memories are injected below. Read them before reaching for tools.
- File already described in memory → you know its contents, skip read_file.
- Project stack already known → skip re-discovering it.
- Key numbers/metrics already saved → use them directly.

## RULE 3 — READING AND EDITING FILES
read_file returns up to 2000 lines per call. The header shows the total line
count; if the file continues, a notice tells you the offset to read next.
Most files fit in one call.
- Never pass a line number as path (path='C:/file.py', not path='200').
- Use offset=/limit= for targeted ranges (e.g. after grep gave you a line number).
- Re-reading a file is FREE — repeat reads are served from the session cache.
  If you are about to edit_file and are not 100% certain of the file's CURRENT
  exact content, re-read it first.
- READ-BEFORE-EDIT IS ENFORCED: edit_file is mechanically BLOCKED on any file
  you have not read this session, and BLOCKED if the file changed on disk after
  your last read. When an edit is blocked → read_file the file, then retry.
  (Files you wrote yourself this session count as read.)

## RULE 4 — WRITE LARGE FILES VIA MARKER (not write_file tool)
For files >150 lines (HTML dashboards, full rewrites, reports), output content directly in your response using this format — JSON function-call args get truncated for large content:
<<<FILE:C:/full/absolute/path/output.html>>>
<!DOCTYPE html>
... complete file content, every single line ...
</html>
<<<END_FILE>>>
The agent auto-detects this, writes it to disk, and shows a colored diff.
Multiple <<<FILE:>>> blocks in one response are fine.
Use write_file tool only for short files (<150 lines).

## RULE 5 — SAVE MEMORY AFTER EVERY TASK
After completing any task, call remember() with dense, specific facts.
What to save: file paths + line counts + what they contain, numbers/metrics extracted, project structure, user preferences learned, task outcomes.
Good: remember("Created C:/analysis/dashboard.html — 800 lines, Chart.js dark-theme, 7 charts for 3 Sylithe forest sites. Site1=5.84ha/335tCO2e, Site2=3.92ha/254tCO2e, Site3=16.06ha/1097tCO2e", tag="file")
Bad: remember("worked on dashboard")

## RULE 6 — COMPLETE TASKS FULLY
Never stop halfway. If you start writing a file, write the whole file. If you start a fix, fix it completely. Run tests if they exist. Summarize what changed at the end.

## RULE 6.5 — SELF-VERIFY AFTER EVERY SIGNIFICANT CHANGE
Before you claim something is "done", run at least ONE verification step:
1. **Syntax check**: py -c "import py_compile; py_compile.compile('file.py', doraise=True)" (Python) / node -c file.js (Node) / go build (Go)
2. **Chain integrity**: If you changed a route, does the service that calls it still match? If you changed a model, are the migrations correct?
3. **Tests exist?** Run them. If they fail → the test caught a real bug. Fix the bug, NOT the test.
4. **Tests don't exist?** Explain why they should and what they'd cover. For critical logic (auth, payments, data transforms), write them.
5. **Server verification**: For apps you built, use the verify-by-running loop (RULE 7) — start the server, hit the health endpoint, kill it.
Never say "it should work" — KNOW it works, or say "I verified X but could not verify Y because Z."

## RULE 6.6 — DETECT AND RESOLVE CONTRADICTIONS
Before starting any new sub-task, scan your recent work:
- "Does this approach contradict something I already built in this session?"
- "Am I about to define a different API contract than what's in API_CONTRACT.md or BUILD_LOG.md?"
- "Does this logic already exist elsewhere in the codebase — am I duplicating it?"
- "Did the user explicitly say NOT to do something this approach requires?"
If you find a contradiction → STOP. Resolve it BEFORE writing any new code. A contradiction caught late costs 10x more to fix than one caught early.

## RULE 7 — SERVERS RUN IN THE BACKGROUND, NEVER IN FOREGROUND BASH
NEVER run a long-lived process (npm run dev, python app.py, flask run, uvicorn,
vite, nodemon, webpack --watch) in a normal bash call — it never exits, so the
call hangs forever.

To VERIFY something actually runs (do this after building an app — never just
claim it works):
  1. bash(command="cd backend && python app.py", run_in_background=true)
     → returns a process_id like 'proc-1' immediately
  2. process_output(process_id="proc-1", wait_seconds=4)
     → read the boot logs; a traceback here means FIX IT before going further
  3. web_fetch("http://localhost:5000/api/health")
     → prove the endpoint actually responds
  4. process_kill(process_id="proc-1")
     → ALWAYS kill every process you started once verification is done

When the USER wants the server running for themselves (not verification):
print the exact commands to run in their own terminals, with ports. Your
background processes die when the session ends — they are for verification only.

## RULE 8 — GREP BEFORE READ
Never cold-read a whole file just to find something inside it. Grep first.
- Need a function? → grep "def function_name" **/*.py  — tells you file + line
- Need a class? → grep "class ClassName"
- Need where something is imported? → grep "import module_name"
Then read_file with offset= and limit= to load only the relevant section.
The Project Index below already lists every file and its top symbols — check it
before reaching for list_dir or read_file just to discover what exists.

## RULE 9 — THE SURGICAL BUG-FIX PROTOCOL
When fixing a bug or editing existing code, follow this exact sequence every time.
Skipping steps is what causes you to create helper scripts, burn iterations, and break things.

### STEP 1 — READ THE ERROR FULLY BEFORE TOUCHING ANY FILE
Copy the exact error message and categorize it:
- `UnicodeDecodeError` / `charmap` → encoding issue in file read or subprocess
- `ModuleNotFoundError` / `ImportError` → wrong import path or missing package
- `AttributeError` / `NameError` → function/variable doesn't exist where expected
- `SyntaxError` → broken code was written, check the file that was last edited
- `KeyError` / `TypeError` → wrong data shape, check what the function receives
- `not found` / `no such file` → path is wrong, check how the path is constructed
Categorizing first tells you WHERE to look before you look anywhere.

### STEP 2 — LOCATE WITH GREP, NOT WITH READ
Never open a file to find something. Grep for it:
- Error mentions a function name → grep "def that_function"
- Error mentions a class → grep "class ThatClass"
- Error mentions a variable → grep "variable_name\s*="
- Error mentions a file path → grep for the path string in the codebase
- Error mentions an import → grep "from module import" or "import module"
grep gives you: exact file + exact line number. You now know where to look.

### STEP 3 — READ ONLY THE RELEVANT SECTION
With the line number from grep, use read_file with offset and limit:
- read_file(path="file.py", offset=LINE-10, limit=40)
This loads 40 lines centered on the problem. Do NOT read the whole file.
Read the whole file ONLY if you need to understand the full structure (e.g. before a large edit).

### STEP 4 — UNDERSTAND THE FIX BEFORE MAKING IT
Before calling edit_file, state to yourself:
- What is the current code doing?
- What should it be doing instead?
- Is this a one-line fix or does it affect multiple places?
If multiple places are affected → grep for all of them BEFORE editing any of them.
If you are not sure what the fix is → re-read the section or grep for related code.
Never edit blindly.

### STEP 5 — MAKE THE SMALLEST POSSIBLE EDIT
Use edit_file for surgical changes. Rules:
- old_string must be copied VERBATIM from the read_file output — never typed from memory
- Include 2-3 lines of context around the change to make old_string unique
- If the edit is >20 lines, ask: can this be split into smaller edits?
- If you are rewriting a whole function, use <<<FILE:>>> marker, not edit_file

### STEP 6 — NEVER CREATE HELPER SCRIPTS TO DO EDITS
If edit_file returns "old_string not found":
  DO THIS: call read_file(offset=LINE-5, limit=20) on that exact section.
           Copy the exact text from the output. Retry edit_file with that exact text.
  NOT THIS: write a _fix.py helper script that does content.replace(...)
Helper scripts (_fix.py, _check.py, _verify.py, _rewrite.py) are a sign you gave up.
They leave junk in the project, hide what actually changed, and break git history.
One re-read + one retry is always faster and cleaner.

### STEP 7 — VERIFY WITH ONE COMMAND, NOT A NEW FILE
After an edit, verify with a single bash call — never write a verification script:
- Python syntax: bash("py -c \"import py_compile; py_compile.compile('file.py', doraise=True)\"")
- Function exists: grep("def function_name", path="file.py")
- Import works: bash("py -c \"from routes.decorators import require_auth\"")
If verification fails, go back to STEP 1 with the new error.

### QUICK REFERENCE — The 7-step checklist
1. Read error → categorize (encoding / import / logic / path / syntax)
2. grep for the exact function/string mentioned in the error → get file + line
3. read_file with offset/limit centered on that line → get 30-40 lines of context
4. Understand: what is it doing vs what should it do?
5. edit_file with old_string copied VERBATIM from step 3 output
6. If edit_file fails → re-read step 3, copy exact text, retry. Never write a helper script.
7. Verify with one bash command. Done.

## RULE 10 — NO REWRITES. EVER.

### During app building (newapp / newsite):
Write each file ONCE — completely and correctly the first time using <<<FILE:>>> marker.
After a file is written in this session, it is LOCKED. You may NOT write_file it again.
If a written file has a bug → edit_file the specific broken lines. That's it.
The ONLY exception: the file is architecturally wrong from the ground up.
  If so: say "Rewriting [file] because [specific reason]" before doing it. Never silent rewrites.
THIS IS MECHANICALLY ENFORCED: the agent BLOCKS any second full write of a file
unless your response text contains that explicit "Rewriting <file> because <reason>"
declaration. A blocked write means: switch to edit_file immediately.

### When user reports an error from your built app:
The error tells you EXACTLY what to fix. Do not touch anything else.
1. Parse the error: which FILE + LINE is it pointing at? (React/Flask/Express all tell you)
2. grep for the exact component name / function name / route mentioned
3. read_file offset/limit around that line — 20 lines of context
4. Fix ONLY that line or function — nothing surrounding it
5. Do NOT rewrite the whole file because of one 5-line bug

### The rewrite death spiral (what you must never do):
  User: "There's an error in login"
  BAD:  Write an entirely new LoginPage.jsx and auth.service.js from scratch
  GOOD: grep "LoginPage" → read_file lines 80-120 → edit_file the broken 3 lines

### Frontend ↔ Backend connection errors — standard checklist:
If the frontend can't reach the backend, check in this exact order:
1. Is `vite.config.js` proxy pointing to the correct backend port?
2. Does `frontend/.env` have `VITE_API_URL=http://localhost:BACKENDPORT`?
3. Does `services/api.js` use `import.meta.env.VITE_API_URL` (not a hardcoded URL)?
4. Does the backend have CORS configured for `http://localhost:FRONTENDPORT`?
5. Does the backend have `GET /api/health` that returns 200?
Fix whichever step fails. Do not rewrite both sides from scratch.

## RULE 11 — PLAN WITH THE todo TOOL
For any task with 3 or more steps (app builds, multi-file fixes, refactors):
1. FIRST call todo with the complete plan — every step as a task, the first one in_progress.
2. Update it as you work: mark a task completed THE MOMENT it is done, set the next
   one in_progress. Exactly ONE task in_progress at a time.
3. Never end your turn while tasks are in_progress or pending — finish them, or tell
   the user explicitly why you stopped.
The current list is re-shown to you on every step — keep it accurate. This is how
you avoid forgetting steps and abandoning builds halfway.

## STOP-AND-THINK TRIGGERS — pause reasoning when:

| Trigger | Action |
|---------|--------|
| A tool returns an unexpected error | Don't immediately retry. Categorize the error first. Understand WHY it failed. |
| You've made 3+ consecutive edits to the SAME file | Step back. Is the approach itself wrong? Would a fresh start be faster? |
| The user's request is ambiguous | Ask ONE precise clarifying question. Never guess what they meant. |
| You're about to write >200 lines of NEW code | Pause. Verify the architecture makes sense. Spawn an explore subagent to confirm there's no existing code that already does this. |
| A test YOU wrote is failing | The test found a real bug. Fix the bug, NOT the test. |
| You're about to touch a file someone else owns | Check: is there a simpler way? Can you add a hook instead of editing core logic? |
| The deadline/constraint is unclear | Ask. "Should I optimize for speed or completeness here?" |

## WHEN YOU'RE STUCK — RECOVERY PATTERNS

| Symptom | Recovery Action |
|---------|----------------|
| edit_file keeps returning "not found" | Re-read the file (offset around the line), copy old_string VERBATIM from the fresh output, retry. NEVER write a helper script. |
| A fix introduced a new error | Undo the fix (git diff to see what changed), understand BOTH the original AND the new error before re-fixing. |
| You don't understand the surrounding code | Spawn an explore subagent to map the area. Better to spend 15s exploring than 5min guessing. |
| You've tried the same approach 3 times | State what you tried, propose exactly ONE alternative, ask the user if they agree before proceeding. |
| You're uncertain about an API/library behavior | Spawn a researcher subagent — fetch real docs. Never guess API signatures. |
| The task is too large and you're losing track | Decompose further. Split the current subtask into 2-3 smaller ones. |
| You realize an earlier step was wrong | Say "I need to backtrack — [what was wrong]." Fix the foundation before building more on top. |

## TOOL GUIDE
| Tool         | Use for                                                              |
|--------------|----------------------------------------------------------------------|
| bash         | Shell commands, git, npm/pip install, run tests, create dirs         |
| read_file    | Read any file — whole file, single call, any size                    |
| write_file   | Create/overwrite files under 150 lines                               |
| edit_file    | Surgical replace of 1–20 lines in existing file                      |
| glob         | Find files by pattern: **/*.py, src/**/*.ts, *.html                  |
| grep         | Search text/regex across files with line numbers                     |
| web_fetch    | Fetch URL content — docs, Stack Overflow, GitHub, PyPI               |
| list_dir     | Explore directory — sizes, file types, structure                     |
| remember     | Save facts to persistent memory (survives across sessions)           |
| todo         | Live task checklist — create at start of multi-step work, update as you go |
| process_output | Read logs/status of a background process (servers started via bash) |
| process_kill | Stop a background process you started — always clean up              |
| spawn_agent  | Spawn a specialized sub-agent for explore/code/verify/research tasks |

## SPAWN_AGENT — When and How
Use spawn_agent when you want a specialized agent to do isolated work:
- Exploring the codebase while you plan: spawn_agent(agent_type="explore", task="List all API routes in the backend and their auth requirements")
- Verifying your code after writing: spawn_agent(agent_type="verifier", task="Read the files I just wrote and find all bugs: [list files here]")
- Researching a library: spawn_agent(agent_type="researcher", task="Find the exact API for Razorpay subscription webhooks with Python example")
- Building a complex subtask: spawn_agent(agent_type="coder", task="Implement the backend auth endpoints in /project/backend/auth.py")

Important: write self-contained tasks — the subagent has no memory of your conversation.
For parallel work: call spawn_agent in separate tool calls within the same response.

## SPAWN_AGENT — Delegation Strategy (reasoning rule)
Before spawning, ask: "Should I do this myself, or delegate?"
| Situation | Decision | Why |
|-----------|----------|-----|
| You already know the codebase perfectly | Do it yourself | Spawning adds overhead for no gain |
| Unfamiliar codebase (>50 files) | Spawn explorer | Map before you build — avoids costly wrong assumptions |
| Unknown API / library / framework | Spawn researcher | Real docs beat hallucinated APIs every time |
| Just wrote a large feature (>200 lines) | Spawn verifier | Fresh eyes catch bugs you're blind to after writing |
| Large isolated feature with clear spec | Spawn coder | Parallel work — you handle the next thing while it builds |
| Simple 1-3 line fix you understand | Do it yourself | Spawning for this is slower, not faster |
| You need 3 independent things explored | Spawn 3 explorers in parallel | One response, three agents, instant results |
Never delegate something you haven't specified clearly. A confused subagent wastes tokens and creates bugs.

## INDIAN TECH EXPERTISE

### Languages & Frameworks
- Python: Django, Flask, FastAPI, Celery, SQLAlchemy, Pydantic, Pytest
- Java: Spring Boot, Spring Security, Hibernate, Maven, Gradle, JUnit
- JavaScript/TypeScript: Node.js, Express, NestJS, React, Next.js, Angular, Vue, Vite
- Mobile: React Native, Flutter/Dart, Kotlin (Android), Swift (iOS)
- Data: Pandas, NumPy, Scikit-learn, TensorFlow, PyTorch, Jupyter

### Indian Payment Integrations
- Razorpay: Orders API, Webhooks, Subscriptions, UPI AutoPay, Route (marketplace splits)
- PayU: PayUmoney, LazyPay BNPL, EMI options
- Cashfree: Payouts API, beneficiary management, bulk transfers
- UPI/NPCI: UPI deep links, QR generation, VPA validation
- NEFT/RTGS/IMPS: Bank transfer APIs via Razorpay/Cashfree
- PhonePe: PG SDK, intent flow, S2S payments
- Paytm: PG, Paytm for Business, Soundbox integration

### Indian Compliance & Regulations
- DPDP Act 2023: consent management, data principal rights (access/correction/erasure), data fiduciary obligations, grievance officer requirement, data localisation, breach notification within 72 hours
- GST: CGST/SGST (intrastate) vs IGST (interstate) split logic, tax slabs (0/5/12/18/28%), CESS, HSN/SAC codes, GSTIN validation, e-invoicing (IRN generation), e-way bill, GSTR-1/3B filing logic
- RBI: PPI guidelines (closed/semi-closed/open wallets), PA/PG licensing, card data tokenisation (no raw PAN storage), 2FA mandate, NBFC regulations, KYC (Video KYC, Aadhaar OTP, CKYC)
- Aadhaar/UIDAI: OTP-based eKYC, masked Aadhaar display (last 4 digits only), no full Aadhaar storage, UIDAI API authentication
- PAN: format validation (AAAAA0000A), PAN-Aadhaar linkage check, TDS deduction logic
- IndiaStack: DigiLocker (Aadhaar XML, driving license, marksheets), Account Aggregator framework, ONDC protocol
- TDS/TCS: Section 194C/194J/194H rates, deduction logic, Form 26AS reconciliation

### Cloud & DevOps (India focus)
- AWS Mumbai (ap-south-1): EC2, RDS, S3, CloudFront, SES, SNS, Lambda
- GCP Mumbai (asia-south1): GKE, Cloud SQL, Firebase, Vertex AI
- Azure India: App Service, Cosmos DB, Azure OpenAI
- Indian CDNs: Cloudflare India PoPs, Fastly, AWS CloudFront India
- DevOps: Docker, Kubernetes, GitHub Actions, GitLab CI, Jenkins, Nginx, Gunicorn, PM2, Supervisor
- Monitoring: Sentry, Grafana, Prometheus, ELK Stack, New Relic, Datadog

### Common Indian App Patterns
- OTP via SMS: Msg91, Twilio India, AWS SNS, 2Factor.in; TRAI DLT registration
- WhatsApp Business API: Meta Cloud API, Gupshup, Interakt
- Email: SendGrid, Mailgun, AWS SES, SparkPost (with proper SPF/DKIM for .in domains)
- Maps: Google Maps India, MapmyIndia (Mappls), OSRM for routing
- Regional language support: Unicode handling, Devanagari/Bengali/Tamil fonts, right-to-left (Urdu)
- Festivals/holidays: India public holiday calendar, regional holidays by state
"""

TOOL_ICONS = {
    "bash":         "⚡",
    "read_file":    "📖",
    "write_file":   "✏️",
    "edit_file":    "🔧",
    "glob":         "📁",
    "grep":         "🔍",
    "web_fetch":    "🌐",
    "list_dir":     "📂",
    "remember":     "🧠",
    "todo":         "📋",
    "process_output": "📜",
    "process_kill":   "🔪",
    "spawn_agent":  "🤖",
    "spawn_worker": "🚀",
    "send_message": "📨",
    "task_stop":    "🛑",
}

def _build_system(project_path: str) -> str:
    import os
    from .index import build_project_index
    abs_path = os.path.abspath(project_path)
    cwd_block = (
        f"\n\n## CURRENT SESSION\n"
        f"- **Working directory (authoritative)**: `{abs_path}`\n"
        f"- All file paths you use MUST be inside `{abs_path}` unless the user "
        f"explicitly mentions a different path.\n"
        f"- Memories below are from PAST sessions — if a memory references a "
        f"different project path, IGNORE that path and use `{abs_path}`."
    )
    parts = [SYSTEM_PROMPT]
    parts.append(cwd_block)
    parts.append(build_project_index(abs_path))    # Feature 5: project symbol index
    parts.append(load_project_instructions(project_path))
    parts.append(project_context_string(project_path))
    parts.append(memories_to_context())
    return "".join(parts)

# ── File cache helpers ────────────────────────────────────────────────────────
# file_cache maps {path: read_file output}. A reserved key holds (mtime_ns, size)
# per path so cache hits are validated against the file on disk — a file changed
# by bash (git pull, npm install, codegen) or an external editor is never served
# stale. Entries are capped so a long session can't grow memory without bound.

_CACHE_META_KEY    = "__bc_cache_meta__"
_READ_LOG_KEY      = "__bc_read_log__"   # {normcase(abspath): mtime_ns at last read}
_RESERVED_KEYS     = {_CACHE_META_KEY, _READ_LOG_KEY}
_CACHE_MAX_ENTRIES = 48        # oldest entries evicted beyond this
_CACHE_MAX_CONTENT = 400_000   # chars — don't hold giant files in RAM


def _cache_paths(file_cache: dict) -> list:
    """All real file keys in the cache (skips reserved bookkeeping keys)."""
    return [k for k in (file_cache or {}) if k not in _RESERVED_KEYS]


def _norm_path(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def _record_read(file_cache: dict, path: str):
    """Mark a file as known to the model RIGHT NOW (after a read or a write
    the model itself made). Powers the read-before-edit enforcement."""
    if file_cache is None or not path:
        return
    log = file_cache.get(_READ_LOG_KEY)
    if not isinstance(log, dict):
        log = {}
        file_cache[_READ_LOG_KEY] = log
    try:
        log[_norm_path(path)] = os.stat(path).st_mtime_ns
    except OSError:
        pass


def _check_edit_allowed(file_cache: dict, path: str):
    """Read-before-edit enforcement (mirrors Claude Code's harness rules).
    Returns None if the edit may proceed, else a blocking error string:
      - the model never read the file this session → must read first
      - the file changed on disk after the model's last read → stale knowledge,
        must re-read before editing
    """
    if not path or not os.path.exists(path) or os.path.isdir(path):
        return None   # let edit_file produce its own File-not-found error
    log = (file_cache or {}).get(_READ_LOG_KEY)
    stamp = log.get(_norm_path(path)) if isinstance(log, dict) else None
    if stamp is None:
        return (
            f"BLOCKED: you have not read {path} in this session. Editing a file "
            f"you have not seen causes failed edits. Call read_file on it first "
            f"(repeat reads are free), copy old_string VERBATIM from the output, "
            f"then retry edit_file."
        )
    try:
        if os.stat(path).st_mtime_ns != stamp:
            return (
                f"BLOCKED: {path} changed on disk AFTER you last read it (edited "
                f"externally or by another process). Your knowledge of it is stale. "
                f"Call read_file on it again, then retry edit_file with the current content."
            )
    except OSError:
        return None
    return None


def _cache_put(file_cache: dict, path: str, content: str):
    if file_cache is None or not path or content is None:
        return
    if len(content) > _CACHE_MAX_CONTENT:
        return
    meta = file_cache.get(_CACHE_META_KEY)
    if not isinstance(meta, dict):
        meta = {}
        file_cache[_CACHE_META_KEY] = meta
    try:
        st = os.stat(path)
        meta[path] = (st.st_mtime_ns, st.st_size)
    except OSError:
        meta[path] = None
    file_cache[path] = content
    # Evict oldest entries beyond the cap (dicts preserve insertion order)
    paths = _cache_paths(file_cache)
    while len(paths) > _CACHE_MAX_ENTRIES:
        oldest = paths.pop(0)
        file_cache.pop(oldest, None)
        meta.pop(oldest, None)


def _cache_get(file_cache: dict, path: str):
    """Return cached content ONLY if the file on disk is unchanged
    (same mtime + size). Stale entries are dropped so callers re-read."""
    if not file_cache or not path or path == _CACHE_META_KEY:
        return None
    content = file_cache.get(path)
    if content is None:
        return None
    meta = file_cache.get(_CACHE_META_KEY)
    stamp = meta.get(path) if isinstance(meta, dict) else None
    try:
        st = os.stat(path)
        if stamp is not None and stamp == (st.st_mtime_ns, st.st_size):
            return content
    except OSError:
        pass
    # Changed on disk (or unverifiable) — invalidate, force a fresh read
    file_cache.pop(path, None)
    if isinstance(meta, dict):
        meta.pop(path, None)
    return None


def _cache_copy(file_cache: dict) -> dict:
    """Copy a file cache for a subagent/worker thread. The meta and read-log
    dicts are copied too so parallel threads never mutate each other's state."""
    if not file_cache:
        return {}
    copied = dict(file_cache)
    for key in _RESERVED_KEYS:
        inner = copied.get(key)
        if isinstance(inner, dict):
            copied[key] = dict(inner)
    return copied


def _invalidate_cache(file_cache: dict, path: str):
    """Drop every cache entry that points at the same file on disk,
    regardless of how the path was spelled (slashes, case, relative)."""
    if not file_cache:
        return
    try:
        target = os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return
    meta = file_cache.get(_CACHE_META_KEY)
    for k in _cache_paths(file_cache):
        try:
            if os.path.normcase(os.path.abspath(str(k))) == target:
                file_cache.pop(k, None)
                if isinstance(meta, dict):
                    meta.pop(k, None)
        except Exception:
            continue


def _already_written(change_log: dict, path: str) -> bool:
    """True if this session already wrote the file (any path spelling)."""
    if not change_log or not path:
        return False
    try:
        target = os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return False
    for k, v in change_log.items():
        try:
            if (os.path.normcase(os.path.abspath(str(k))) == target
                    and isinstance(v, dict) and v.get("writes", 0) >= 1):
                return True
        except Exception:
            continue
    return False


def _extract_and_write_files(
    text: str,
    base_dir: str = None,
    file_cache: dict = None,
    change_log: dict = None,
) -> tuple[str, list[str], list[str]]:
    """
    Find <<<FILE:path>>> ... <<<END_FILE>>> blocks in the response text,
    write each to disk, return (cleaned text, written paths, blocked paths).
    If base_dir is given, relative paths are resolved inside it (so files
    for a newsite/newapp project always land in the project folder).
    Invalidates file_cache and records into change_log so later edit_file
    calls never act on stale content.

    RULE 10 ENFORCEMENT: a file already written this session may not be fully
    rewritten unless the response text explicitly declares "Rewriting <file>
    because <reason>". Blocked rewrites are reported back so the model can
    switch to surgical edit_file fixes instead of the rewrite death spiral.
    """
    written = []
    blocked = []
    _base = Path(base_dir).resolve() if base_dir else None
    _rewrite_declared = "rewrit" in text.lower()

    def _write(m):
        path    = m.group(1).strip()
        content = m.group(2)
        if content.startswith("\n"):
            content = content[1:]
        p = Path(path)
        # Anchor relative paths to base_dir when given
        if _base and not p.is_absolute():
            p = _base / p

        # RULE 10: block silent full rewrites of files written this session
        if (not _rewrite_declared and p.exists()
                and _already_written(change_log, str(p))):
            blocked.append(str(p))
            try:
                console.print(
                    f"  ⛔ [yellow]Rewrite blocked[/yellow]  [cyan]{p}[/cyan]  "
                    f"[dim](already written this session — RULE 10)[/dim]"
                )
            except UnicodeEncodeError:
                pass
            return (
                f"[BLOCKED rewrite of {p} — this file was already written this session "
                f"(RULE 10). Fix bugs with edit_file on the specific lines. If a ground-up "
                f"rewrite is truly required, state 'Rewriting {p.name} because <reason>' "
                f"in your response text and output the file block again.]"
            )
        try:
            old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception:
            old = ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines = len(content.splitlines())
        try:
            console.print(f"  ✏️  [green]Written[/green]  [cyan]{p}[/cyan]  [dim]({lines} lines)[/dim]")
        except UnicodeEncodeError:
            console.print(f"  [green]Written[/green]  {p.name}  [dim]({lines} lines)[/dim]")
        if old and old != content:
            show_file_diff(str(p), old, content)
        written.append(str(p))

        # Keep session state in sync — a marker write IS a write
        _invalidate_cache(file_cache, str(p))
        _record_read(file_cache, str(p))   # model wrote it → knows its content
        if change_log is not None:
            entry = change_log.get(str(p), {"writes": 0, "edits": 0})
            entry["writes"] += 1
            change_log[str(p)] = entry

        note = f"[File written: {p}]"
        _syn_err = _syntax_check(str(p), content)
        if _syn_err:
            note = f"[File written: {p} — ⚠ {_syn_err} — fix with edit_file]"
            try:
                console.print(f"  [bold red]⚠ Syntax error:[/bold red] [dim]{_syn_err}[/dim]")
            except UnicodeEncodeError:
                pass
        return note

    cleaned = FILE_MARKER_RE.sub(_write, text)
    return cleaned, written, blocked

def _show_tool_start(name: str, args: dict):
    icon = TOOL_ICONS.get(name, "🔧")
    try:
        if name == "spawn_agent":
            atype   = args.get("agent_type", "general")
            task_pr = str(args.get("task", ""))[:60].replace("\n", "↵")
            console.print(f"  {icon} [dim]{name}[/dim]  [cyan]{atype}[/cyan] [dim]{task_pr}[/dim]")
            return
        if name == "spawn_worker":
            atype = args.get("agent_type", "general")
            desc  = args.get("description", "") or str(args.get("task", ""))[:50]
            console.print(
                f"  {icon} [dim]{name}[/dim]  [cyan]{atype}[/cyan]  [dim]{desc}[/dim]  "
                f"[dim italic]→ background[/dim italic]"
            )
            return
        if name == "send_message":
            wid = args.get("worker_id", "?")
            msg = str(args.get("message", ""))[:55].replace("\n", "↵")
            console.print(f"  {icon} [dim]{name}[/dim]  [cyan]{wid}[/cyan]  [dim]{msg}[/dim]")
            return
        if name == "task_stop":
            wid = args.get("worker_id", "?")
            console.print(f"  {icon} [dim]{name}[/dim]  [cyan]{wid}[/cyan]")
            return
        preview_val = (
            args.get("path") or args.get("url") or args.get("command") or
            args.get("pattern") or (list(args.values())[0] if args else "")
        )
        preview = str(preview_val)[:80].replace("\n", "↵")
        console.print(f"  {icon} [dim]{name}[/dim]  [cyan]{preview}[/cyan]")
    except UnicodeEncodeError:
        console.print(f"  [dim]{name}[/dim]")

def _show_tool_done(name: str, result: str, elapsed: float):
    lines = result.count("\n") + 1
    size  = len(result)
    summary = f"{lines} lines" if name in ("read_file", "bash") else f"{size} chars"
    time_str = f"{elapsed*1000:.0f}ms" if elapsed < 1 else f"{elapsed:.1f}s"
    try:
        console.print(f"     [dim green]done[/dim green] [dim]({time_str}  {summary})[/dim]")
    except UnicodeEncodeError:
        console.print(f"     done ({time_str}  {summary})")

def _truncate_result(result: str, max_chars: int = 64000) -> str:
    if len(result) <= max_chars:
        return result
    half    = max_chars // 2
    omitted = len(result) - max_chars
    return f"{result[:half]}\n\n[... {omitted} chars omitted ...]\n\n{result[-half:]}"


def _build_file_restoration(to_summarise: list, file_cache: dict, budget: int = 40000) -> str:
    """
    Feature 6 extension: after compaction, figure out which files were
    actively used in the compacted messages and re-inject them so the
    model doesn't lose working context.

    Extracts file paths from tool_call arguments in the compacted messages,
    then pulls those files from file_cache up to the token budget.
    """
    import json as _json
    # Find files that were actually used in the compacted range
    mentioned: list[str] = []
    seen: set[str] = set()
    for m in to_summarise:
        if not m.get("tool_calls"):
            continue
        for tc in m["tool_calls"]:
            fn = tc.get("function", {})
            if fn.get("name") not in ("read_file", "write_file", "edit_file"):
                continue
            try:
                path = str(_json.loads(fn.get("arguments", "{}")).get("path", ""))
                if (path and path != _CACHE_META_KEY
                        and path not in seen and path in file_cache):
                    seen.add(path)
                    mentioned.append(path)
            except Exception:
                pass

    if not mentioned:
        return ""

    parts = []
    used  = 0
    for path in mentioned:
        content = file_cache.get(path, "")
        if not content or not isinstance(content, str):
            continue
        lines = content.count("\n") + 1
        if used + len(content) > budget:
            parts.append(f"[{path}] ({lines} lines — budget exceeded, re-read if needed)")
            continue
        parts.append(f"[{path}] ({lines} lines)\n```\n{content}\n```")
        used += len(content)

    return "\n\n".join(parts)


def _render_todos(todo_state: list):
    """Print the live checklist the way Claude Code renders its todo list."""
    console.print()
    for t in todo_state:
        st = t.get("status")
        try:
            if st == "completed":
                console.print(f"  [green]✓[/green] [dim strike]{t['content']}[/dim strike]")
            elif st == "in_progress":
                console.print(f"  [yellow]▶[/yellow] [bold]{t['content']}[/bold]")
            else:
                console.print(f"  [dim]☐ {t['content']}[/dim]")
        except UnicodeEncodeError:
            console.print(f"  [{st}] {t['content']}")
    console.print()


def _apply_todo(args: dict, todo_state: list) -> str:
    """Validate and apply a todo tool call. Mutates todo_state in place."""
    tasks = args.get("tasks", [])
    if not isinstance(tasks, list):
        return "Error: 'tasks' must be an array of {content, status} objects."
    clean = []
    for t in tasks:
        if isinstance(t, dict) and t.get("content"):
            st = t.get("status", "pending")
            if st not in ("pending", "in_progress", "completed"):
                st = "pending"
            clean.append({"content": str(t["content"])[:200], "status": st})
    todo_state[:] = clean
    done = sum(1 for t in clean if t["status"] == "completed")
    prog = sum(1 for t in clean if t["status"] == "in_progress")
    return (
        f"Task list updated: {len(clean)} tasks — {done} completed, "
        f"{prog} in progress, {len(clean) - done - prog} pending."
    )


def _todo_reminder(todo_state: list) -> dict:
    """Transient per-call reminder message (never stored in history)."""
    done = sum(1 for t in todo_state if t.get("status") == "completed")
    lines = []
    for t in todo_state:
        mark = {"completed": "[x]", "in_progress": "[>]"}.get(t.get("status"), "[ ]")
        lines.append(f"{mark} {t.get('content', '')}")
    return {"role": "user", "content": (
        f"[TASK LIST REMINDER — internal, not from the user. {done}/{len(todo_state)} done. "
        "Keep it accurate with the todo tool; finish or address every remaining item "
        "before ending your turn.]\n" + "\n".join(lines)
    )}


def _git_checkpoint(project_path: str, message: str, init: bool = False):
    """Commit all current changes as a checkpoint. Returns the short hash,
    or None when there is nothing to commit / no repo / git unavailable."""
    import subprocess as _sp
    try:
        root = Path(project_path)
        if not (root / ".git").exists():
            if not init:
                return None
            _sp.run(["git", "init", "-q"], cwd=str(root), capture_output=True, timeout=15)
        _sp.run(["git", "add", "-A"], cwd=str(root), capture_output=True, timeout=30)
        msg = f"bharatcode: {message[:60].strip()}" if message and message.strip() else "bharatcode checkpoint"
        r = _sp.run(
            ["git", "-c", "user.name=Sylithe Code", "-c", "user.email=bharatcode@local",
             "commit", "-q", "-m", msg],
            cwd=str(root), capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if r.returncode != 0:
            return None   # nothing to commit, or commit blocked
        h = _sp.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        )
        return (h.stdout or "").strip() or None
    except Exception:
        return None


def _syntax_check(path: str, content: str | None = None) -> str | None:
    """
    Feature 4 — post-write syntax check for .py, .json, and .js files.
    Returns an error description string on failure, None if clean (or untestable).
    Reads from disk if content is not provided.
    """
    ext = Path(path).suffix.lower()

    if ext == '.py':
        import ast
        try:
            if content is None:
                content = Path(path).read_text(encoding='utf-8', errors='replace')
            ast.parse(content)
            return None
        except SyntaxError as e:
            return f"Python SyntaxError at line {e.lineno}: {e.msg}"
        except Exception:
            return None

    if ext == '.json':
        import json as _json
        try:
            if content is None:
                content = Path(path).read_text(encoding='utf-8', errors='replace')
            _json.loads(content)
            return None
        except _json.JSONDecodeError as e:
            return f"Invalid JSON at line {e.lineno}: {e.msg}"
        except Exception:
            return None

    if ext in ('.js', '.mjs', '.cjs'):
        # node --check parses without executing. Skip silently if node missing.
        import subprocess as _sp
        try:
            r = _sp.run(
                ["node", "--check", str(path)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode != 0:
                err_lines = [l for l in (r.stderr or "").strip().splitlines() if l.strip()]
                detail = "; ".join(err_lines[-3:]) if err_lines else "syntax error"
                return f"JavaScript SyntaxError: {detail[:300]}"
        except (FileNotFoundError, Exception):
            return None
        return None

    return None

# ── Context management constants ──────────────────────────────────────────────

_HISTORY_FULL_RECENT  = 40      # recent messages always sent full to API
# DeepSeek V4 context window is 1M tokens (flash) — compaction is about cost
# control, not survival. Keep generous headroom: aggressive compression is what
# makes the model edit blind and fail tasks. /compact still works manually.
_COMPACT_THRESHOLD    = 150000  # ~150K estimated tokens before auto-compacting
_COMPACT_TARGET_RATIO = 0.40    # fallback: compact oldest 40% if no safe cut found
_COMPACT_KEEP_RECENT  = 20_000  # target tokens to keep after compaction

# Built from tool definitions — any tool declaring execution_mode="parallel" runs concurrently.
# Tools with no shared mutable session state (no cache writes, no RULE 10 checks) qualify.
_PARALLEL_TOOLS = {
    t["function"]["name"]
    for t in TOOLS
    if t.get("execution_mode") == "parallel"
}

# Returned by a tool to signal the agent loop should stop after this batch.
# When EVERY tool in a batch returns this sentinel the outer loop exits immediately
# without waiting for the model to produce another response.
_TERMINATE_SENTINEL = "__BHARATCODE_TERMINATE__"

# ── API error classification ──────────────────────────────────────────────────
# Three error buckets, each routed differently:
#   billing  → stop immediately (retrying hits the same wall)
#   overflow → compact history and retry the current turn
#   transient → exponential backoff, up to 5 attempts
#   unknown  → one retry then surface to user

_BILLING_ERR_RE = re.compile(
    r"billing|insufficient.?quota|out.?of.?budget|usage.?limit|available.?balance"
    r"|free.?usage.?limit|monthly.?limit|GoUsageLimitError|FreeUsageLimitError"
    r"|payment.?required|your.?account",
    re.IGNORECASE,
)
_OVERFLOW_ERR_RE = re.compile(
    r"context.?(length|window|limit|size)|maximum.?context|token.?limit"
    r"|prompt.?too.?long|input.?too.?long|reduce.?the.?length|too.?many.?token"
    r"|context_length_exceeded|maximum_context_length",
    re.IGNORECASE,
)
_TRANSIENT_ERR_RE = re.compile(
    r"overload|rate.?limit|429|5\d\d|service.?unavailable|bad.?gateway"
    r"|gateway.?timeout|network.?error|connection|timed?.?out|stream.?ended"
    r"|websocket|fetch.?failed|terminated|retry|internal.?server",
    re.IGNORECASE,
)

def _classify_api_error(exc: Exception) -> str:
    """Return 'billing', 'overflow', 'transient', or 'unknown'."""
    msg = str(exc)
    if _BILLING_ERR_RE.search(msg):
        return "billing"
    if _OVERFLOW_ERR_RE.search(msg):
        return "overflow"
    if _TRANSIENT_ERR_RE.search(msg):
        return "transient"
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        if status == 429 or status >= 500:
            return "transient"
        if status == 400 and "context" in msg.lower():
            return "overflow"
    return "unknown"

# ── Compaction summarization prompts ─────────────────────────────────────────

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. "
    "Read the conversation and produce a structured summary following the exact format. "
    "Do NOT continue the conversation or answer questions in it. ONLY output the summary."
)

_SUMMARIZE_PROMPT = """\
Create a structured context checkpoint that will let the AI continue this work.

## Goal
[What is the user trying to accomplish? List multiple items if needed.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned — or "(none)"]

## Progress
### Done
- [x] [Completed tasks/changes with exact file names]

### In Progress
- [ ] [What was being worked on when history was cut]

### Blocked
- [Issues preventing progress — or "(none)"]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what to do next]

## Critical Context
- [Data, examples, error messages, exact file paths needed to continue — or "(none)"]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

_UPDATE_SUMMARIZE_PROMPT = """\
Update the existing summary (in <previous-summary> tags) with the NEW messages above.

RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, context from the new messages
- Move items from "In Progress" → "Done" when completed
- Update "Next Steps" based on what was accomplished
- Remove resolved blockers; preserve exact file paths and error messages

Use the same format: Goal / Constraints & Preferences / Progress (Done/In Progress/Blocked) \
/ Key Decisions / Next Steps / Critical Context

Keep each section concise."""


_REASONING_PATTERNS = [
    # Full-stack / connectivity
    (r"full.?stack|frontend.{0,30}backend|backend.{0,30}frontend", "full-stack wiring"),
    (r"\bcors\b|proxy|port\s*\d{4}|vite.config|flask.cors", "server connectivity"),
    # Debugging
    (r"\b(debug|not working|broken|crash|traceback|stacktrace|500 error|404 error)\b", "debugging"),
    (r"\bwhy\s+(is|does|isn.t|doesn.t|can.t|won.t)\b", "root cause analysis"),
    (r"\b(error|exception|issue|problem|fail)\b.{0,60}\b(fix|solve|resolve)\b", "error fixing"),
    # Architecture / design
    (r"\b(architecture|system design|design pattern|data model|schema)\b", "system design"),
    (r"\bfrom scratch\b|\bbuild.{0,20}complete\b|\bentirely new\b", "complex scaffolding"),
    (r"\bnew\s+app\b|/newapp\b|bharatcode new app", "app scaffolding"),
    (r"\bnew\s+website\b|/newsite\b|bharatcode new website", "website scaffolding"),
    # Optimization / algorithms
    (r"\b(optimize|performance|bottleneck|algorithm|complexity|O\(n\))\b", "optimization"),
    (r"\b(refactor|restructure|rewrite|overhaul)\b", "refactoring"),
    # Integrations
    (r"\b(razorpay|payment|webhook|oauth|jwt|authentication|authorization)\b", "auth/payment integration"),
    (r"\b(database|migration|query|index|transaction)\b.{0,40}\b(slow|optimize|fix)\b", "DB optimization"),
]

_CHAT_PATTERNS = [
    r"^(hi|hello|hey|sup)\b",
    r"^(what is|what are|who is|when is|where is)\b",
    r"^(explain|describe|tell me about|summarize)\b",
    r"^(show me|list|print|display)\b",
    r"^(how are you|how do you)\b",
]


def _select_model(task: str, cfg: dict) -> tuple[str, str]:
    """
    Auto-select deepseek-v4-flash (fast) vs deepseek-v4-pro (deep reasoning)
    based on task complexity signals. Returns (model_id, reason).
    User's /model setting is always respected as the baseline — auto-select
    only upgrades flash→pro, never downgrades pro→flash.
    """
    import re
    from .config import MODEL_ALIASES
    configured = cfg.get("model", "deepseek-v4-flash")
    # Normalise any old model name that slipped through
    configured = MODEL_ALIASES.get(configured, configured)

    # User explicitly set pro — always honour it
    if configured == "deepseek-v4-pro":
        return configured, "user setting"

    task_lower = task.lower().strip()

    # Obvious simple queries → stay on flash
    for pat in _CHAT_PATTERNS:
        if re.match(pat, task_lower):
            return "deepseek-v4-flash", "simple query"

    if len(task) < 60 and not any(kw in task_lower for kw in ("fix", "error", "bug", "build", "create")):
        return "deepseek-v4-flash", "short request"

    # Check for complexity signals → upgrade to pro
    for pat, reason in _REASONING_PATTERNS:
        if re.search(pat, task_lower, re.IGNORECASE):
            return "deepseek-v4-pro", reason

    # Long detailed task → pro
    if len(task) > 400:
        return "deepseek-v4-pro", "complex multi-part task"

    return "deepseek-v4-flash", "general task"


def _estimate_tokens(messages: list) -> int:
    """Rough token estimate: 1 token ≈ 4 chars (safe for English + code)."""
    import json as _json
    chars = sum(len(_json.dumps(m, ensure_ascii=False)) for m in messages)
    return chars // 4


def _repair_orphaned_tool_calls(history: list) -> list:
    """
    Fix two classes of invalid tool-message sequences that cause API 400 errors:

    1. assistant(tool_calls) with no following tool results — compaction or
       interruption ate the results. Inject placeholder results.

    2. tool result with no preceding assistant(tool_calls) — compaction ate the
       assistant message but left the result. Drop the stranded tool message.

    Both errors crash the API; this function makes history safe before every call.
    """
    repaired: list = []
    i = 0
    while i < len(history):
        msg = history[i]

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            repaired.append(msg)
            # Consume all tool results that immediately follow
            j = i + 1
            found_ids: set = set()
            while j < len(history) and history[j].get("role") == "tool":
                found_ids.add(history[j].get("tool_call_id", ""))
                repaired.append(history[j])
                j += 1
            # Inject placeholders for missing results
            for tc in msg["tool_calls"]:
                if tc.get("id") not in found_ids:
                    repaired.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      "[Interrupted — tool did not complete. Ask user to retry.]",
                    })
            i = j

        elif msg.get("role") == "tool":
            # Stranded tool result — its assistant(tool_calls) was eaten by compaction.
            # Drop it; sending it would cause "tool must follow tool_calls" API error.
            i += 1

        else:
            repaired.append(msg)
            i += 1

    return repaired


def _build_api_messages(system_content: str, history: list) -> list:
    """
    Build the final messages list for each API call.
    - Repairs any orphaned tool calls first (interruption safety).
    - Recent messages go full; older ones are compressed to save tokens.
    """
    system_msg = {"role": "system", "content": system_content}
    if not history:
        return [system_msg]

    safe_history = _repair_orphaned_tool_calls(history)

    cutoff = max(0, len(safe_history) - _HISTORY_FULL_RECENT)
    old, recent = safe_history[:cutoff], safe_history[cutoff:]

    def _compress(msg: dict) -> dict:
        # Keep old messages useful — crushing them to a few hundred chars makes
        # the model lose file contents mid-task and edit blind. DeepSeek V4 has
        # a 1M context window; moderate trimming is enough.
        m = dict(msg)
        if m["role"] == "tool":
            c = m.get("content", "")
            if len(c) > 2000:
                m["content"] = c[:2000] + f" [...{len(c)-2000} chars truncated — re-read the file if you need the rest]"
        elif m["role"] == "assistant" and not m.get("tool_calls"):
            c = m.get("content", "")
            if len(c) > 1200:
                m["content"] = c[:1200] + f" [...{len(c)-1200} chars]"
        return m

    return [system_msg] + [_compress(m) for m in old] + recent


def _find_cut_point(history: list, keep_tokens: int) -> int:
    """Return the index into history where we cut:
       history[:cut] is summarized, history[cut:] is kept.
    Walks backward accumulating tokens until keep_tokens is reached,
    then snaps to the nearest 'user' message boundary so we never cut
    inside an assistant(tool_calls) + tool-result block.
    Falls back to the old 40% ratio when no safe cut is found."""
    import json as _j

    accumulated   = 0
    last_safe_cut = None   # most recent user-msg index seen while walking backward

    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "user":
            last_safe_cut = i
        accumulated += len(_j.dumps(msg, ensure_ascii=False)) // 4
        if accumulated >= keep_tokens and last_safe_cut is not None and last_safe_cut > 0:
            return last_safe_cut

    # Fallback: cut at 40% if no safe boundary found inside the budget
    return max(4, int(len(history) * _COMPACT_TARGET_RATIO))


def _auto_compact(
    history: list,
    client,
    model: str,
    file_cache: dict = None,
    last_context_tokens: int = 0,
    force: bool = False,
) -> bool:
    """
    Summarise old history into a structured checkpoint when context is too large.
    Mutates history in-place. Returns True if compaction happened.

    Improvements over the old version:
    - Uses actual API prompt-token count (last_context_tokens) when available,
      falls back to char/4 estimation
    - Smart cut point: keeps last ~20K tokens, cuts only at user-msg boundaries
      so we never split an assistant(tool_calls) + tool-result block
    - Structured summary (Goal / Progress / Key Decisions / Next Steps / Context)
    - Incremental updates: detects a prior compaction summary in history and calls
      the UPDATE prompt instead of discarding the old summary
    - Tracks read vs modified files and appends them to the summary
    """
    context_tokens = last_context_tokens if last_context_tokens > 0 else _estimate_tokens(history)
    if not force and context_tokens < _COMPACT_THRESHOLD:
        return False

    cutoff = _find_cut_point(history, _COMPACT_KEEP_RECENT)
    if cutoff <= 0:
        return False

    to_summarise = history[:cutoff]
    keep         = history[cutoff:]

    # ── Detect previous compaction for incremental update ───────────────────
    previous_summary = None
    summary_start    = 0
    for i, m in enumerate(to_summarise):
        if (m.get("role") == "assistant"
                and str(m.get("content", "")).startswith("[AUTO-COMPACTED")):
            previous_summary = m.get("content", "")
            summary_start    = i + 1   # only summarize messages AFTER old compaction
            break

    messages_to_summarise = to_summarise[summary_start:]
    if not messages_to_summarise:
        return False

    # ── Track file operations ────────────────────────────────────────────────
    import json as _j
    read_files:     set[str] = set()
    modified_files: set[str] = set()
    for m in messages_to_summarise:
        for tc in (m.get("tool_calls") or []):
            fn    = tc.get("function", {})
            fname = fn.get("name", "")
            try:
                path = _j.loads(fn.get("arguments", "{}")).get("path", "")
            except Exception:
                path = ""
            if not path:
                continue
            if fname in ("write_file", "edit_file"):
                modified_files.add(path)
                read_files.discard(path)
            elif fname == "read_file" and path not in modified_files:
                read_files.add(path)

    # ── Build conversation dump ──────────────────────────────────────────────
    lines = []
    for m in messages_to_summarise:
        role    = m["role"].upper()
        content = m.get("content") or ""
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                lines.append(f"[TOOL_CALL] {fn.get('name')}({fn.get('arguments','')[:120]})")
        elif content:
            lines.append(f"[{role}]: {content[:1200]}")
    dump = "\n".join(lines)

    # Preserve the original user task verbatim across compactions
    first_user = next(
        (m for m in to_summarise
         if m.get("role") == "user"
         and not str(m.get("content", "")).startswith(
             ("[FILE CACHE RESTORED", "[SYSTEM]", "<task-notification", "[ORIGINAL TASK"))),
        None,
    )

    # ── Build summarization prompt ───────────────────────────────────────────
    prompt = f"<conversation>\n{dump}\n</conversation>\n\n"
    if previous_summary:
        prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        prompt += _UPDATE_SUMMARIZE_PROMPT
    else:
        prompt += _SUMMARIZE_PROMPT

    # Inject file cache paths as context hints
    known = _cache_paths(file_cache)[:25] if file_cache else []
    if known:
        prompt += (
            "\n\nFILES IN SESSION CACHE — include each in Critical Context with its purpose:\n"
            + "\n".join(f"  - {p}  ({file_cache[p].count(chr(10)) + 1} lines)" for p in known)
        )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.1,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception:
        return False

    # ── Append file operation list to summary ────────────────────────────────
    if read_files or modified_files:
        summary += "\n\n## Files Accessed\n"
        if modified_files:
            summary += "**Modified:** " + ", ".join(sorted(modified_files)) + "\n"
        if read_files:
            summary += "**Read:** " + ", ".join(sorted(read_files)) + "\n"

    compact_msg = {
        "role":    "assistant",
        "content": f"[AUTO-COMPACTED SESSION SUMMARY — {cutoff} messages → 1]\n{summary}",
    }
    prefix = []
    if first_user:
        prefix.append({
            "role":    "user",
            "content": f"[ORIGINAL TASK — preserved through compaction]\n{first_user.get('content', '')}",
        })
    history[:] = prefix + [compact_msg] + keep

    # Re-inject actively-used files so the model keeps full working context
    restored = _build_file_restoration(to_summarise, file_cache or {})
    if restored:
        history.append({
            "role":    "user",
            "content": f"[FILE CACHE RESTORED AFTER COMPACTION — files you were working with]\n\n{restored}",
        })
        history.append({
            "role":    "assistant",
            "content": "File contents restored. I have full working context of all files I was editing.",
        })

    action = "incremental update" if previous_summary else "full summary"
    console.print(
        f"\n  [dim cyan]⚡ Auto-compacted {cutoff} messages → 1 ({action})"
        f"{' + ' + str(len(_cache_paths(file_cache))) + ' files restored' if restored else ''}"
        f"  ({context_tokens:,} → ~{_COMPACT_KEEP_RECENT:,} tokens)[/dim cyan]\n"
    )
    return True

def run_agent(
    task: str,
    project_path: str = ".",
    auto_approve: bool = False,
    on_done: callable = None,
    history: list = None,
    system_content: str = None,
    plan_mode: bool = False,
    file_cache: dict = None,
    allowed_tools: set = None,
    worker_pool = None,
    silent: bool = False,
    change_log: dict = None,   # Feature 7: session-scoped change tracker
    todo_state: list = None,   # live task checklist (mutated in place)
) -> str:
    """
    history:        shared list of prior messages (no system). Mutated in-place.
    system_content: pre-built system prompt — pass from interactive_mode() so it
                    is computed ONCE per session instead of once per turn.
    plan_mode:      if True, model may only read files and propose plans.
                    write_file, edit_file, bash are blocked.
    file_cache:     dict {path: content} shared across all turns. read_file hits
                    this first — so files read earlier in the session are never
                    fetched from disk again even after auto-compaction erases them
                    from visible history. Invalidated on write_file / edit_file.
    allowed_tools:  when set, only tools in this set are exposed to the model.
                    Used by subagents to restrict capabilities (explore=read-only).
    worker_pool:    WorkerPool instance when in coordinator mode. Before each API
                    call, pending worker <task-notification> messages are drained
                    from the pool and injected into history so the coordinator
                    sees completions without polling.
    """
    import time

    cfg = load_config()
    if not auto_approve:
        auto_approve = cfg.get("auto_approve", False)

    client = OpenAI(
        api_key=get_api_key(),
        base_url="https://api.deepseek.com",
    )

    if history is None:
        history = []
    if file_cache is None:
        file_cache = {}
    if todo_state is None:
        todo_state = []

    # Baseline for auto-checkpoint: did THIS call change any files?
    _cl_baseline = sum(
        v.get("writes", 0) + v.get("edits", 0)
        for v in (change_log or {}).values() if isinstance(v, dict)
    )

    # Build system content once if not cached
    if system_content is None:
        system_content = _build_system(project_path)

    # Plan mode: inject read-only restriction on top of system prompt
    if plan_mode:
        system_content = system_content + (
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "## PLAN MODE — READ ONLY\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "You are in PLAN MODE. You may ONLY read files, grep, glob, list_dir, and web_fetch.\n"
            "Do NOT call write_file, edit_file, or bash — they are DISABLED.\n"
            "Explore the codebase thoroughly, then present a clear, numbered implementation plan.\n"
            "End your plan with: **PLAN COMPLETE — type /plan to approve and execute.**"
        )

    # Empty task means "notifications already in history — don't add a new user message"
    if task:
        history.append({"role": "user", "content": task})

    total_in = total_out = 0
    max_iter = cfg.get("max_iterations", 100)

    # Auto-select model based on task complexity.
    # Coordinator synthesis always uses pro — it must reason over multiple worker reports.
    if worker_pool is not None:
        active_model   = "deepseek-v4-pro"
        model_reason   = "coordinator synthesis"
        configured_model = active_model
    else:
        active_model, model_reason = _select_model(task, cfg)
        configured_model = cfg.get("model", "deepseek-v4-flash")

    if not silent:
        from .config import model_label
        if active_model != configured_model:
            icon = "🧠" if active_model == "deepseek-v4-pro" else "⚡"
            console.print(
                f"  {icon} [dim]Auto-selected [cyan]{model_label(active_model)}[/cyan] ({model_reason})[/dim]"
            )
        else:
            icon = "🧠" if active_model == "deepseek-v4-pro" else "⚡"
            console.print(f"  {icon} [dim]{model_label(active_model)}[/dim]")

    # Build tool list for this agent:
    # - Coordinator mode gets coordinator tools (spawn_worker/send_message/task_stop + reads)
    # - Subagents with allowed_tools restriction get a filtered subset
    # - Main agent (no restrictions): all tools including spawn_agent
    if worker_pool is not None:
        # Coordinator mode — orchestration tools only, no write/bash
        active_tools = COORDINATOR_TOOLS
    elif allowed_tools is not None:
        # Restricted subagent: only allowed tool names, no spawn_agent
        active_tools = [
            t for t in _TOOLS_NO_SPAWN
            if t["function"]["name"] in allowed_tools
        ]
    else:
        # Main agent: all tools including spawn_agent
        active_tools = TOOLS

    _last_prompt_tokens = 0          # actual API prompt-token count from the previous turn
    _overflow_recovery_attempted = False  # prevent infinite compact → overflow → compact loops

    for iteration in range(max_iter):
        # Warn once at 80% of limit so user can /compact before hitting the wall
        if not silent and iteration == int(max_iter * 0.8):
            console.print(
                f"  [bold yellow]⚠  {iteration}/{max_iter} iterations used "
                f"({int(max_iter*0.8)}% of limit). "
                "Run /compact to free context if this task will take longer.[/bold yellow]"
            )

        # ── Drain worker notifications (coordinator mode only) ───────────────
        # Before every API call, check if any background workers finished.
        # Their <task-notification> XML is injected as role=user messages so
        # the coordinator sees completions in its conversation naturally.
        if worker_pool is not None:
            notifications = worker_pool.drain_notifications()
            if notifications:
                history.extend(notifications)
                console.print(
                    f"  [dim cyan]📬 {len(notifications)} worker notification(s) ready[/dim cyan]"
                )

        # Auto-compact if history is getting large (mutates history in-place)
        _auto_compact(history, client, active_model, file_cache=file_cache,
                      last_context_tokens=_last_prompt_tokens)

        # Rebuild messages — recent full, older compressed
        messages = _build_api_messages(system_content, history)

        # Re-show the live task list every call (transient — never enters history)
        if todo_state:
            messages.append(_todo_reminder(todo_state))

        content_parts: list[str] = []
        tool_calls_raw: dict[int, dict] = {}
        finish_reason  = None
        usage_in = usage_out = 0       # accumulated across retries + continuations
        _round_in = _round_out = 0     # usage reported by the current stream

        def _consume_chunk(chunk, idx_offset: int = 0):
            """Parse one SSE chunk into content_parts / tool_calls_raw / usage."""
            nonlocal finish_reason, _round_in, _round_out
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                if chunk.usage:
                    _round_in  = chunk.usage.prompt_tokens
                    _round_out = chunk.usage.completion_tokens
                return
            delta         = choice.delta
            finish_reason = choice.finish_reason or finish_reason
            if delta.content:
                content_parts.append(delta.content)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = idx_offset + tc_delta.index
                    if idx not in tool_calls_raw:
                        tool_calls_raw[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_calls_raw[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_raw[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_raw[idx]["arguments"] += tc_delta.function.arguments
            if chunk.usage:
                _round_in  = chunk.usage.prompt_tokens
                _round_out = chunk.usage.completion_tokens

        # Stream loop: retries transient API failures with backoff, and when the
        # output is cut by max_tokens mid-text it auto-continues (up to 3 times)
        # so large <<<FILE:>>> blocks are never silently written half-finished.
        _continue_msgs: list = []
        _continues = 0
        _was_truncated = False
        while True:
            _round_in = _round_out = 0
            finish_reason = None
            _idx_offset  = len(tool_calls_raw)
            _cp_len      = len(content_parts)
            _tc_snapshot = {k: dict(v) for k, v in tool_calls_raw.items()}

            _stream_err = None
            _err_class  = None
            for _attempt in range(5):  # up to 5 for transient; billing/overflow break early
                try:
                    stream = client.chat.completions.create(
                        model=active_model,
                        messages=messages + _continue_msgs,
                        tools=active_tools,
                        tool_choice="auto",
                        temperature=0.1,
                        max_tokens=16384,
                        stream=True,
                    )
                    if silent:
                        # Worker thread — consume stream without Live to avoid
                        # "Only one Live display may be active at once" across threads
                        for chunk in stream:
                            _consume_chunk(chunk, _idx_offset)
                    else:
                        with Live("", console=console, refresh_per_second=15) as live:
                            for chunk in stream:
                                _consume_chunk(chunk, _idx_offset)
                                # Update live display while streaming
                                display = "".join(content_parts)
                                if "<<<FILE:" in display:
                                    short = re.sub(r'<<<FILE:[^>]+>>>.*', '  [dim][writing file...][/dim]', display, flags=re.DOTALL)
                                    live.update(Text.from_markup(short))
                                elif display:
                                    live.update(Text(display, style="white"))
                            # Clear streamed text — Panel below is the canonical display
                            live.update("")
                    _stream_err = None
                    break
                except Exception as _api_exc:
                    _err_class  = _classify_api_error(_api_exc)
                    _stream_err = _api_exc
                    # Roll back partial state from the failed stream before retrying
                    del content_parts[_cp_len:]
                    tool_calls_raw.clear()
                    tool_calls_raw.update(_tc_snapshot)
                    finish_reason = None
                    _round_in = _round_out = 0

                    if _err_class == "billing":
                        if not silent:
                            console.print(
                                f"\n  [bold red]✗ Billing/quota error — stopping: "
                                f"{str(_api_exc)[:200]}[/bold red]\n"
                            )
                        break  # retrying hits the same wall

                    if _err_class == "overflow":
                        break  # handled by overflow recovery block below

                    # transient or unknown — exponential backoff, capped at 30s
                    if _attempt < 4:
                        _wait = min(2 ** _attempt, 30)
                        if not silent:
                            console.print(
                                f"  [yellow]⚠ API error ({_err_class}): "
                                f"{str(_api_exc)[:120]} — "
                                f"retrying in {_wait}s ({_attempt + 2}/5)[/yellow]"
                            )
                        time.sleep(_wait)

            if _stream_err is not None and _err_class != "overflow":
                err_text = f"API request failed: {_stream_err}"
                if not silent:
                    console.print(f"\n  [bold red]✗ {err_text}[/bold red]\n")
                history.append({"role": "assistant", "content": f"[{err_text}]"})
                return err_text

            if _stream_err is not None and _err_class == "overflow":
                break  # exit continuation loop — overflow handler below

            usage_in  += _round_in
            usage_out += _round_out
            if _round_in > 0:
                _last_prompt_tokens = _round_in
            _was_truncated = (finish_reason == "length")

            if _was_truncated and not tool_calls_raw and _continues < 3:
                _continues += 1
                if not silent:
                    console.print(
                        "  [dim yellow]…output hit the token limit — auto-continuing "
                        f"({_continues}/3)…[/dim yellow]"
                    )
                _continue_msgs = [
                    {"role": "assistant", "content": "".join(content_parts)},
                    {"role": "user", "content": (
                        "[SYSTEM] Your previous output was CUT OFF by the token limit "
                        "mid-response. Continue EXACTLY from the last character you wrote. "
                        "Do NOT repeat anything, do NOT restart the file block, do NOT add "
                        "any preamble — output only the continuation."
                    )},
                ]
                continue
            break

        # ── Context overflow recovery ─────────────────────────────────────────
        # The model returned a context-length error. Compact history and retry
        # this iteration rather than giving up. The _overflow_recovery_attempted
        # flag prevents infinite compact → overflow → compact loops.
        if _stream_err is not None and _err_class == "overflow":
            if not _overflow_recovery_attempted:
                _overflow_recovery_attempted = True
                if not silent:
                    console.print(
                        "\n  [bold yellow]⚠ Context overflow — compacting history "
                        "and retrying...[/bold yellow]\n"
                    )
                _auto_compact(history, client, active_model,
                              file_cache=file_cache,
                              last_context_tokens=_last_prompt_tokens,
                              force=True)
                continue  # restart the for-loop iteration with compacted history
            else:
                err_text = (
                    "Context overflow: even after compaction the context is too large. "
                    "Try /compact then re-send your task."
                )
                if not silent:
                    console.print(f"\n  [bold red]✗ {err_text}[/bold red]\n")
                return err_text

        total_in  += usage_in
        total_out += usage_out
        session_cost.add(usage_in, usage_out)
        session_cost.model = active_model

        full_content = "".join(content_parts)

        # ── Extract and write any <<<FILE:>>> blocks ─────────────────────────
        _blocked_rewrites: list[str] = []
        if "<<<FILE:" in full_content:
            console.print()
            cleaned, written, _blocked_rewrites = _extract_and_write_files(
                full_content, base_dir=project_path,
                file_cache=file_cache, change_log=change_log,
            )
            full_content = cleaned

        # ── Display text response ────────────────────────────────────────────
        if full_content.strip() and not tool_calls_raw:
            console.print()
            console.print(Panel(
                Markdown(full_content),
                border_style="green",
                title="[bold green]Sylithe Code[/bold green]",
                padding=(0, 1),
            ))

        # Append assistant turn to history — messages is rebuilt next iteration
        assistant_msg: dict = {"role": "assistant", "content": full_content}
        if tool_calls_raw:
            assistant_msg["tool_calls"] = [
                {
                    "id":   tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls_raw.values()
            ]
        history.append(assistant_msg)

        # ── Blocked rewrites: bounce back for surgical fixes, don't end turn ──
        if _blocked_rewrites and not tool_calls_raw:
            history.append({"role": "user", "content": (
                "[SYSTEM] RULE 10: your full rewrite of "
                + ", ".join(_blocked_rewrites)
                + " was BLOCKED — these files were already written this session. "
                "Do NOT output them again in full. Instead fix the specific problem "
                "with edit_file (grep → read the exact lines → edit only those lines). "
                "Only if the file is architecturally wrong from the ground up, state "
                "'Rewriting <file> because <reason>' explicitly and then rewrite it."
            )})
            continue

        # ── Done if no tool calls ────────────────────────────────────────────
        if not tool_calls_raw:
            _show_usage(total_in, total_out)
            # Auto-checkpoint: commit this turn's file changes (main agent only —
            # parallel workers committing simultaneously would race each other)
            if (not silent and not plan_mode and change_log is not None
                    and cfg.get("auto_checkpoint", True)):
                _cl_now = sum(
                    v.get("writes", 0) + v.get("edits", 0)
                    for v in change_log.values() if isinstance(v, dict)
                )
                if _cl_now > _cl_baseline:
                    _h = _git_checkpoint(project_path, task or "session changes")
                    if _h:
                        console.print(f"  [dim]📌 git checkpoint [cyan]{_h}[/cyan][/dim]")
            if on_done:
                on_done(full_content)
            return full_content

        # ── Execute tools ────────────────────────────────────────────────────
        if not silent:
            console.print()

        # Parallel pre-pass: when the model calls multiple parallel-safe tools in
        # one response, fire them all in threads simultaneously. Tools in
        # _PARALLEL_TOOLS have no shared mutable session state so this is safe.
        # spawn_agent gets its own rich display; other tools share a generic one.
        _par_results: dict[str, str] = {}
        _par_list = [(tc["id"], tc) for tc in tool_calls_raw.values()
                     if tc["name"] in _PARALLEL_TOOLS]
        if len(_par_list) > 1:
            import threading as _th
            from .subagent import AGENT_TYPES as _AT

            _spawn_calls = [(cid, tc) for cid, tc in _par_list if tc["name"] == "spawn_agent"]
            _other_calls = [(cid, tc) for cid, tc in _par_list if tc["name"] != "spawn_agent"]

            if not silent:
                if _spawn_calls:
                    _labels = "  ".join(
                        f"[cyan]{_AT.get(json.loads(tc.get('arguments') or '{}').get('agent_type','general'), _AT['general'])['icon']} "
                        f"{_AT.get(json.loads(tc.get('arguments') or '{}').get('agent_type','general'), _AT['general'])['label']}[/cyan]"
                        for _, tc in _spawn_calls
                    )
                    console.print(
                        f"\n  [bold cyan]⚡ {len(_spawn_calls)} agents launching in parallel[/bold cyan]  "
                        f"{_labels}\n"
                    )
                if _other_calls:
                    _names = "  ".join(f"[cyan]{tc['name']}[/cyan]" for _, tc in _other_calls)
                    console.print(
                        f"\n  [bold cyan]⚡ {len(_other_calls)} tools running in parallel[/bold cyan]  "
                        f"{_names}\n"
                    )

            _par_lock = _th.Lock()

            def _run_par(cid: str, tc_item: dict):
                _name = tc_item["name"]
                _args = {}
                try:
                    _args = json.loads(tc_item.get("arguments") or "{}")
                except Exception:
                    pass

                if _name == "spawn_agent":
                    _sub_type = _args.get("agent_type", "general")
                    _sub_task = _args.get("task", "")
                    _info     = _AT.get(_sub_type, _AT["general"])
                    if not _sub_task:
                        with _par_lock:
                            _par_results[cid] = "Error: spawn_agent requires a 'task' argument."
                        return
                    _sys = system_content + _info["system_suffix"]
                    _t0  = time.time()
                    try:
                        _out = run_agent(
                            task=_sub_task,
                            project_path=project_path,
                            auto_approve=True,
                            history=[],
                            system_content=_sys,
                            file_cache=_cache_copy(file_cache),
                            allowed_tools=_info["allowed_tools"],
                            silent=True,
                        ) or ""
                        _dur = time.time() - _t0
                        _r = (
                            f"[{_info['label']} Agent — {_dur:.1f}s]\n\n{_out}"
                            if _out else
                            f"[{_info['label']} Agent completed in {_dur:.1f}s — no output]"
                        )
                    except Exception as _exc:
                        _r = f"[{_info['label']} Agent failed: {_exc}]"
                    with _par_lock:
                        _par_results[cid] = _r
                    if not silent:
                        _dur = time.time() - _t0
                        console.print(
                            f"  [dim green]✓[/dim green] {_info['icon']} "
                            f"[bold]{_info['label']}[/bold]  [dim]{_dur:.1f}s[/dim]"
                        )
                else:
                    # General parallel tool — no session-state side effects
                    _t0 = time.time()
                    try:
                        _r = execute_tool(_name, _args)
                    except Exception as _exc:
                        _r = f"Error: {_exc}"
                    with _par_lock:
                        _par_results[cid] = _r
                    if not silent:
                        _dur = time.time() - _t0
                        console.print(
                            f"  [dim green]✓[/dim green] [cyan]{_name}[/cyan]  "
                            f"[dim]{_dur:.1f}s[/dim]"
                        )

            _par_threads = [
                _th.Thread(target=_run_par, args=(cid, tc_), daemon=True)
                for cid, tc_ in _par_list
            ]
            for _t in _par_threads:
                _t.start()
            for _t in _par_threads:
                _t.join()

            if not silent:
                console.print(f"\n  [dim]All {len(_par_list)} parallel tools done.[/dim]\n")

        _batch_exec_count = 0   # tools that actually ran this batch
        _batch_term_count = 0   # of those, how many returned _TERMINATE_SENTINEL

        for tc in tool_calls_raw.values():
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"])
            except Exception:
                # Broken/truncated JSON args — never run the tool with empty args;
                # tell the model exactly what happened so it can recover.
                _hint = (
                    f"Error: the arguments for this {name} call were invalid JSON"
                    + (" because your output was truncated by the token limit" if _was_truncated else "")
                    + ". If you were writing a large file, do NOT use write_file — output it "
                    "with the <<<FILE:path>>> marker in your response text instead. "
                    "Otherwise, retry the tool call with valid JSON."
                )
                if not silent:
                    console.print(f"  [red]✗ {name}: invalid/truncated JSON arguments[/red]")
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": _hint})
                continue

            # Plan mode: block all write/execute tools
            if plan_mode and name in ("write_file", "edit_file", "bash"):
                console.print(f"  [yellow]⛔ {name} blocked — plan mode is ON. Type /plan to approve.[/yellow]")
                history.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": f"Blocked: {name} is disabled in plan mode. Only reads allowed."})
                continue

            call = ToolCall(name=name, args=args)

            hook_result = hooks.run_pre(call)
            if not hook_result.proceed:
                result = f"Blocked by safety hook: {hook_result.message}"
                console.print(f"  [red]blocked[/red] {name}: {hook_result.message}")
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue

            approved, reason = needs_approval(name, args, auto_approve)
            if not approved:
                approved = ask_permission(name, args)
                if not approved:
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": "Permission denied by user."})
                    continue

            if not silent:
                _show_tool_start(name, args)
            t0 = time.time()

            _spinner = console.status("", spinner="dots") if not silent else nullcontext()
            with _spinner:
                # ── Parallel pre-results: serve any tool that ran in the pre-pass ──
                if tc["id"] in _par_results:
                    result = _par_results[tc["id"]]

                # ── Coordinator tools ─────────────────────────────────────────
                elif name == "spawn_worker" and worker_pool is not None:
                    wid = worker_pool.spawn(
                        task=args.get("task", ""),
                        agent_type=args.get("agent_type", "general"),
                        description=args.get("description", ""),
                        project_path=project_path,
                        system=None,   # workers build their own clean system — NOT coordinator-enhanced
                        file_cache=file_cache,
                    )
                    result = (
                        f"Worker spawned successfully.\n"
                        f"worker_id: {wid}\n"
                        f"agent_type: {args.get('agent_type', 'general')}\n"
                        f"description: {args.get('description', args.get('task', '')[:60])}\n\n"
                        f"The worker is running in the background. You will receive a "
                        f"<task-notification> message when it completes. "
                        f"Continue your response to the user now — do not wait."
                    )

                elif name == "send_message" and worker_pool is not None:
                    result = worker_pool.send_message(
                        args.get("worker_id", ""),
                        args.get("message", ""),
                    )

                elif name == "task_stop" and worker_pool is not None:
                    result = worker_pool.stop(args.get("worker_id", ""))

                elif name == "read_file":
                    cache_key = str(args.get("path", ""))
                    # Ranged reads (offset/limit) bypass the cache entirely — the
                    # cache only ever holds COMPLETE files, never slices.
                    _has_range = bool(args.get("offset")) or bool(args.get("limit"))
                    # Session-wide cache, validated against disk mtime+size so a
                    # file changed by bash/git/external editor is never served stale
                    _cached_content = None if _has_range else _cache_get(file_cache, cache_key)
                    if _cached_content is not None:
                        result = _cached_content
                        _record_read(file_cache, cache_key)
                        elapsed = time.time() - t0
                        hooks.run_post(call, result)
                        session_cost.add_tool()
                        if not silent:
                            lines = result.count("\n") + 1
                            time_str = f"{elapsed*1000:.0f}ms" if elapsed < 1 else f"{elapsed:.1f}s"
                            console.print(
                                f"     [dim green]done[/dim green] "
                                f"[dim]({time_str}  {lines} lines  [cyan]cached[/cyan])[/dim]"
                            )
                        history.append({
                            "role":         "tool",
                            "tool_call_id": tc["id"],
                            "content":      _truncate_result(result),
                        })
                        continue
                    result = execute_tool(name, args)
                    if cache_key and not result.startswith("Error") and not result.startswith("File not found"):
                        # Any successful read (full or partial) unlocks editing
                        _record_read(file_cache, cache_key)
                        # Cache only COMPLETE file contents — partial reads would
                        # poison later cache hits with a slice of the file
                        if not _has_range and "[file continues" not in result:
                            _cache_put(file_cache, cache_key, result)
                elif name == "write_file":
                    from .diff import capture_write
                    _wpath   = str(args.get("path", ""))
                    _wcontent = args.get("content", "")
                    _wmode   = args.get("mode", "w")
                    # RULE 10: block silent full overwrites of session-written files
                    if (_wmode != "a" and _wpath and os.path.exists(_wpath)
                            and _already_written(change_log, _wpath)
                            and "rewrit" not in full_content.lower()):
                        result = (
                            f"BLOCKED (RULE 10): {_wpath} was already written this session. "
                            "Fix bugs with edit_file on the specific lines — do not overwrite "
                            "the whole file. If a ground-up rewrite is truly required, state "
                            "'Rewriting <file> because <reason>' in your response text, then retry."
                        )
                        if not silent:
                            console.print(
                                f"  ⛔ [yellow]Rewrite blocked[/yellow]  [cyan]{_wpath}[/cyan]  "
                                f"[dim](RULE 10)[/dim]"
                            )
                        history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                        continue
                    try:
                        result = capture_write(_wpath, _wcontent, _wmode)
                    except Exception as _wexc:
                        result = f"Error writing '{_wpath}': {type(_wexc).__name__}: {_wexc}"
                    _invalidate_cache(file_cache, _wpath)

                    if not result.startswith("Error"):
                        # The model wrote this content — it knows the file's state
                        _record_read(file_cache, _wpath)
                        # Feature 4: post-write syntax check
                        _syn_content = _wcontent if _wmode == "w" else None
                        _syn_err = _syntax_check(_wpath, _syn_content)
                        if _syn_err:
                            result += f"\n\n⚠ SYNTAX ERROR DETECTED: {_syn_err}"
                            if not silent:
                                console.print(
                                    f"  [bold red]⚠ Syntax error:[/bold red] [dim]{_syn_err}[/dim]"
                                )
                        # Feature 7: change tracking
                        if change_log is not None and _wpath:
                            entry = change_log.get(_wpath, {"writes": 0, "edits": 0})
                            entry["writes"] += 1
                            change_log[_wpath] = entry
                elif name == "edit_file":
                    from .diff import capture_edit
                    _epath = str(args.get("path", ""))
                    # Read-before-edit enforcement: block edits on files the model
                    # never read this session, or that changed on disk since its
                    # last read. Blind edits are the #1 cause of failed old_strings.
                    _edit_block = _check_edit_allowed(file_cache, _epath)
                    if _edit_block is not None:
                        result = _edit_block
                        if not silent:
                            console.print(
                                f"  ⛔ [yellow]Edit blocked[/yellow]  [cyan]{_epath}[/cyan]  "
                                f"[dim](read the file first)[/dim]"
                            )
                        history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                        continue
                    # NOTE: never pre-check old_string against file_cache — the
                    # cache stores line-numbered read_file output, so multi-line
                    # old_strings can never match it. capture_edit works against
                    # the real file on disk and returns nearest-match hints.
                    result = capture_edit(
                        _epath,
                        args.get("old_string", ""),
                        args.get("new_string", ""),
                        replace_all=bool(args.get("replace_all", False)),
                    )
                    if result.startswith("Error") or result.startswith("File not found"):
                        pass
                    else:
                        # The model made this change — it knows the file's new state
                        _record_read(file_cache, _epath)
                        # Refresh cache with updated content (complete files only)
                        _refreshed = execute_tool("read_file", {"path": _epath})
                        if (not _refreshed.startswith("Error")
                                and not _refreshed.startswith("File not found")
                                and "[file continues" not in _refreshed):
                            _cache_put(file_cache, _epath, _refreshed)
                        else:
                            _invalidate_cache(file_cache, _epath)
                        # Feature 4: post-edit syntax check (reads from disk)
                        _syn_err = _syntax_check(_epath)
                        if _syn_err:
                            result += f"\n\n⚠ SYNTAX ERROR DETECTED: {_syn_err}"
                            if not silent:
                                console.print(
                                    f"  [bold red]⚠ Syntax error:[/bold red] "
                                    f"[dim]{_syn_err}[/dim]"
                                )
                        # Feature 7: change tracking
                        if change_log is not None:
                            entry = change_log.get(_epath, {"writes": 0, "edits": 0})
                            entry["edits"] += 1
                            change_log[_epath] = entry
                elif name == "todo":
                    result = _apply_todo(args, todo_state)
                    if not silent and not result.startswith("Error"):
                        _render_todos(todo_state)
                elif name == "spawn_agent":
                    # Sequential single-call path (parallel calls are handled above)
                    from .subagent import run_subagent, AGENT_TYPES
                    sub_type  = args.get("agent_type", "general")
                    sub_task  = args.get("task", "")
                    if not sub_task:
                        result = "Error: spawn_agent requires a 'task' argument."
                    else:
                        info       = AGENT_TYPES.get(sub_type, AGENT_TYPES["general"])
                        sub_result = run_subagent(
                            task=sub_task,
                            agent_type=sub_type,
                            project_path=project_path,
                            parent_system=system_content,
                            parent_file_cache=file_cache,
                        )
                        if sub_result.success and sub_result.output:
                            result = (
                                f"[{info['label']} Agent — {sub_result.duration:.1f}s]\n\n"
                                f"{sub_result.output}"
                            )
                        elif sub_result.error:
                            result = f"[{info['label']} Agent failed: {sub_result.error}]"
                        else:
                            result = f"[{info['label']} Agent completed in {sub_result.duration:.1f}s — no output]"
                else:
                    result = execute_tool(name, args)

            elapsed = time.time() - t0
            hooks.run_post(call, result)
            session_cost.add_tool()
            if not silent:
                _show_tool_done(name, result, elapsed)

            # Terminate signal: count tools that want to stop the loop
            if result == _TERMINATE_SENTINEL:
                _batch_term_count += 1
                result = "Agent signaled task completion."
            _batch_exec_count += 1

            # Store full result in history — _build_api_messages compresses old ones
            history.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      _truncate_result(result),
            })

        # If every executed tool in this batch signaled termination, exit the loop
        if _batch_exec_count > 0 and _batch_term_count == _batch_exec_count:
            if not silent:
                console.print(
                    "\n  [bold yellow]🛑 All tools signaled termination — stopping.[/bold yellow]\n"
                )
            break

        if not silent:
            console.print()

    console.print(
        f"\n  [bold yellow]⚠  Reached {max_iter}-iteration limit.[/bold yellow]  "
        "[dim]The task may be incomplete. Try /compact then re-send your task, "
        "or break it into smaller steps.[/dim]\n"
    )
    return f"Reached {max_iter}-iteration limit."

def _show_usage(prompt_tokens: int, completion_tokens: int):
    if prompt_tokens == 0:
        return
    total = prompt_tokens + completion_tokens
    console.print(
        f"\n[dim]Tokens: {prompt_tokens:,} in + {completion_tokens:,} out = {total:,} total[/dim]"
    )
