"""
BharatCode Coordinator Mode — multi-worker orchestration engine.

The coordinator is the main agent in a special mode. It spawns async workers,
receives <task-notification> XML when they finish, synthesizes results, and
directs the next phase of work — all without blocking.

Internal API:
  WorkerPool.spawn()         → fires a background thread, returns worker_id instantly
  WorkerPool.send_message()  → continues an existing worker (reuses its context)
  WorkerPool.stop()          → sends abort signal to a running worker
  WorkerPool.drain()         → returns pending notifications as history messages

Notification flow:
  worker thread completes
    → _notify() builds <task-notification> XML
    → pushed to notification_queue (thread-safe Queue)
  coordinator next turn
    → drain_notifications() called before API call
    → notifications injected as role=user messages into history
    → model sees completions as if users sent them
"""

import time
import uuid
import threading
from dataclasses  import dataclass, field
from typing       import Optional
from queue        import Queue, Empty

from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich         import box

console = Console()

# ── Worker States ──────────────────────────────────────────────────────────────

PENDING  = "pending"
RUNNING  = "running"
DONE     = "completed"
FAILED   = "failed"
STOPPED  = "stopped"

_STATUS_ICON = {
    PENDING: "🔵",
    RUNNING: "⏳",
    DONE:    "✅",
    FAILED:  "❌",
    STOPPED: "🛑",
}

# ── Worker ─────────────────────────────────────────────────────────────────────

@dataclass
class Worker:
    worker_id:      str
    description:    str
    agent_type:     str
    task:           str
    status:         str  = PENDING
    result:         str  = ""
    error:          str  = ""
    started_at:     float = field(default_factory=time.time)
    duration_ms:    int  = 0
    tool_uses:      int  = 0
    history:        list = field(default_factory=list)
    # Preserved so send_message continuations run with identical context
    _project_path:  str  = field(default=".", repr=False)
    _system:        str  = field(default="",  repr=False)
    _cache:         dict = field(default_factory=dict, repr=False)
    _abort:         threading.Event  = field(default_factory=threading.Event, repr=False)
    _thread:        Optional[threading.Thread] = field(default=None, repr=False)

    @property
    def elapsed_s(self) -> float:
        return (time.time() - self.started_at)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ── Worker Pool ─────────────────────────────────────────────────────────────────

class WorkerPool:
    """
    Session-scoped pool that manages all coordinator workers.

    Thread-safety:
      _workers dict is protected by _lock.
      notification_queue is a stdlib Queue (already thread-safe).
    """

    def __init__(self):
        self._workers:           dict[str, Worker] = {}
        self._lock:              threading.Lock    = threading.Lock()
        self.notification_queue: Queue             = Queue()
        self._session_start:     float             = time.time()

    # ── Internal API ──────────────────────────────────────────────────────────

    def spawn(
        self,
        task:         str,
        agent_type:   str  = "general",
        description:  str  = "",
        project_path: str  = ".",
        system:       str  = None,
        file_cache:   dict = None,
    ) -> str:
        """
        Spawn a worker in a background thread.
        Returns worker_id immediately — does NOT block.

        The worker runs run_agent() with its own isolated history and a
        copy of the coordinator's file_cache (read-only sharing — writes
        go to the worker's own copy to prevent cache corruption).
        """
        from .subagent import AGENT_TYPES
        from .agent    import run_agent, _build_system

        worker_id   = f"w-{uuid.uuid4().hex[:6]}"
        info        = AGENT_TYPES.get(agent_type, AGENT_TYPES["general"])
        allowed     = info["allowed_tools"]
        base_system = system or _build_system(project_path)
        agent_sys   = base_system + info["system_suffix"] + _WORKER_INSTRUCTIONS

        # Workers share a read-only COPY of the coordinator's file_cache.
        # This means they benefit from already-read files without polluting
        # the coordinator's cache with their own writes.
        from .agent import _cache_copy
        worker_cache = _cache_copy(file_cache)

        worker = Worker(
            worker_id=worker_id,
            description=description or task[:55],
            agent_type=agent_type,
            task=task,
            _project_path=project_path,
            _system=agent_sys,
            _cache=worker_cache,
        )
        worker.status = RUNNING

        with self._lock:
            self._workers[worker_id] = worker

        _banner_spawn(worker_id, info, worker.description)

        def _run():
            t0 = time.time()
            try:
                output = run_agent(
                    task=task,
                    project_path=project_path,
                    auto_approve=True,
                    history=worker.history,
                    system_content=agent_sys,
                    file_cache=worker_cache,
                    allowed_tools=allowed,
                    silent=True,   # no Live display — parallel threads share one console
                ) or ""

                if worker._abort.is_set():
                    worker.status = STOPPED
                    self._push_notification(worker)
                    return

                worker.status   = DONE
                worker.result   = output
                worker.tool_uses = sum(
                    1 for m in worker.history if m.get("role") == "tool"
                )
            except Exception as exc:
                worker.status = FAILED
                worker.error  = str(exc)
            finally:
                worker.duration_ms = int((time.time() - t0) * 1000)

            self._push_notification(worker)

        t = threading.Thread(target=_run, daemon=True, name=f"bc-worker-{worker_id}")
        worker._thread = t
        t.start()
        return worker_id

    def send_message(self, worker_id: str, message: str) -> str:
        """
        Continue an existing worker with a new instruction.

        Two cases:
          RUNNING  → append user message to its live history. The worker's
                     run_agent() loop picks it up on the next iteration.
          DONE/FAILED/STOPPED → re-start the worker with accumulated history
                     so it keeps all the context it built up previously.

        This is the coordinator's primary tool for directing work without
        losing a worker's hard-won context (files it read, code it wrote, etc.)
        """
        from .subagent import AGENT_TYPES
        from .agent    import run_agent

        worker = self._get(worker_id)
        if worker is None:
            return f"[Error] No worker with id '{worker_id}' in this session."

        if worker.status == RUNNING:
            worker.history.append({"role": "user", "content": message})
            console.print(
                f"  📨 [dim]Message injected into running worker [cyan]{worker_id}[/cyan][/dim]"
            )
            return f"Message sent to running worker {worker_id}."

        if worker.status in (DONE, FAILED, STOPPED):
            worker.status = RUNNING
            worker._abort.clear()

            def _continue():
                t0 = time.time()
                try:
                    info    = AGENT_TYPES.get(worker.agent_type, AGENT_TYPES["general"])
                    allowed = info["allowed_tools"]
                    output  = run_agent(
                        task=message,
                        project_path=worker._project_path,
                        auto_approve=True,
                        history=worker.history,
                        system_content=worker._system,
                        file_cache=worker._cache,
                        allowed_tools=allowed,
                        silent=True,
                    ) or ""
                    worker.status = DONE
                    worker.result = output
                    worker.tool_uses = sum(
                        1 for m in worker.history if m.get("role") == "tool"
                    )
                except Exception as exc:
                    worker.status = FAILED
                    worker.error  = str(exc)
                finally:
                    worker.duration_ms += int((time.time() - t0) * 1000)
                self._push_notification(worker)

            t = threading.Thread(target=_continue, daemon=True, name=f"bc-cont-{worker_id}")
            worker._thread = t
            t.start()
            console.print(
                f"  🔄 [dim]Continuing worker [cyan]{worker_id}[/cyan] with new instructions[/dim]"
            )
            return f"Worker {worker_id} re-started with continuation message."

        return f"Worker {worker_id} status is '{worker.status}' — cannot message."

    def stop(self, worker_id: str) -> str:
        """
        Send an abort signal to a running worker.
        The worker checks _abort on each tool call completion; it stops cleanly
        at the next boundary rather than being killed mid-write.
        A stopped worker can be continued with send_message().
        """
        worker = self._get(worker_id)
        if worker is None:
            return f"[Error] No worker with id '{worker_id}'."
        if worker.status != RUNNING:
            return f"Worker {worker_id} is not running (status: {worker.status})."

        worker._abort.set()
        worker.status = STOPPED
        console.print(
            f"  🛑 [dim]Stop signal sent to worker [cyan]{worker_id}[/cyan][/dim]"
        )
        return f"Stop signal sent to worker {worker_id}. It will halt at the next safe boundary."

    def drain_notifications(self) -> list[dict]:
        """
        Drain all pending completion notifications from the queue.
        Returns a list of history messages (role=user) ready to inject into
        the coordinator's history before the next API call.

        Called once per coordinator turn at the top of run_agent()'s loop.
        Thread-safe: Queue.get_nowait() does not block.
        """
        messages = []
        while True:
            try:
                xml = self.notification_queue.get_nowait()
                messages.append({"role": "user", "content": xml})
            except Empty:
                break
        return messages

    def status_table(self) -> str:
        """Render all workers as a Rich table string for /workers command."""
        with self._lock:
            workers = list(self._workers.values())
        if not workers:
            return "[dim]No workers launched in this coordinator session.[/dim]"

        t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("ID",          style="cyan",  no_wrap=True)
        t.add_column("Type",        style="dim",   no_wrap=True)
        t.add_column("Status",      no_wrap=True)
        t.add_column("Time",        style="dim",   no_wrap=True)
        t.add_column("Tools",       style="dim",   no_wrap=True)
        t.add_column("Description", style="white")

        for w in workers:
            icon   = _STATUS_ICON.get(w.status, "❓")
            time_s = f"{w.duration_ms/1000:.1f}s" if w.duration_ms else (
                f"{w.elapsed_s:.0f}s…" if w.status == RUNNING else "—"
            )
            t.add_row(
                w.worker_id,
                w.agent_type,
                f"{icon} {w.status}",
                time_s,
                str(w.tool_uses),
                w.description[:50],
            )

        import io
        buf = io.StringIO()
        tmp = Console(file=buf, width=120, highlight=False, markup=True)
        tmp.print(t)
        return buf.getvalue()

    # ── Private Helpers ────────────────────────────────────────────────────────

    def _get(self, worker_id: str) -> Optional[Worker]:
        with self._lock:
            return self._workers.get(worker_id)

    def _push_notification(self, worker: Worker):
        """
        Build a <task-notification> XML block and push it to the queue.
        The coordinator will drain this on its next turn and inject it
        into conversation history as a user message.
        """
        status     = worker.status
        icon       = _STATUS_ICON.get(status, "❓")
        summary    = {
            DONE:    f'Agent "{worker.description}" completed',
            FAILED:  f'Agent "{worker.description}" failed: {worker.error[:120]}',
            STOPPED: f'Agent "{worker.description}" was stopped',
        }.get(status, f'Agent status changed to {status}')

        result_xml = (
            f"\n<result>\n{worker.result[:25000]}\n</result>"
            if worker.result.strip() else ""
        )
        error_xml = (
            f"\n<error>{worker.error}</error>"
            if worker.error else ""
        )

        xml = (
            f"<task-notification>\n"
            f"<task-id>{worker.worker_id}</task-id>\n"
            f"<agent-type>{worker.agent_type}</agent-type>\n"
            f"<description>{worker.description}</description>\n"
            f"<status>{status}</status>\n"
            f"<summary>{summary}</summary>"
            f"{result_xml}"
            f"{error_xml}\n"
            f"<usage>\n"
            f"  <tool_uses>{worker.tool_uses}</tool_uses>\n"
            f"  <duration_ms>{worker.duration_ms}</duration_ms>\n"
            f"</usage>\n"
            f"</task-notification>"
        )

        # Print completion banner to coordinator terminal
        _banner_done(worker, icon)
        self.notification_queue.put(xml)


# ── Terminal Banners ───────────────────────────────────────────────────────────

def _banner_spawn(worker_id: str, info: dict, description: str):
    color = info["color"]
    icon  = info["icon"]
    label = info["label"]
    console.print(
        f"\n  🚀 [bold {color}]{icon} {label}[/bold {color}] "
        f"[cyan]{worker_id}[/cyan]  "
        f"[dim]{description[:55]}[/dim]  "
        f"[dim italic]running in background[/dim italic]"
    )


def _banner_done(worker: Worker, icon: str):
    secs = worker.duration_ms / 1000
    console.print(
        f"\n  {icon} [bold]Worker done[/bold]  "
        f"[cyan]{worker.worker_id}[/cyan]  "
        f"[dim]{worker.agent_type}  {secs:.1f}s  {worker.tool_uses} tool calls[/dim]"
    )
    if worker.error:
        console.print(f"     [red]Error:[/red] [dim]{worker.error[:120]}[/dim]")


# ── Coordinator System Prompt ──────────────────────────────────────────────────

COORDINATOR_SYSTEM_PROMPT = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## COORDINATOR MODE — ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are BharatCode operating as a **coordinator**. You do NOT write code yourself.
You orchestrate specialist workers, synthesize their results, and direct the work.

═══════════════════════════════════════════════════════════
## 1. YOUR ROLE
═══════════════════════════════════════════════════════════

Coordinator responsibilities:
  • Break tasks into parallel subtasks and assign them to workers
  • Receive worker results as <task-notification> XML messages
  • SYNTHESIZE findings — understand what they mean before directing next steps
  • Keep the user informed: tell them what you launched and why
  • Direct the full lifecycle: research → synthesis → implementation → verification

You answer questions directly when you can. Only delegate work that needs tools.

═══════════════════════════════════════════════════════════
## 2. YOUR TOOLS
═══════════════════════════════════════════════════════════

| Tool           | When to use                                              |
|----------------|----------------------------------------------------------|
| spawn_worker   | Launch a background specialist — returns worker_id NOW   |
| send_message   | Continue an existing worker with a follow-up task        |
| task_stop      | Kill a worker that went in the wrong direction           |
| read_file      | Read files yourself to synthesize worker findings        |
| glob / grep    | Search codebase for context before directing workers     |
| web_fetch      | Look up docs you need for synthesis                      |

You do NOT have: write_file, edit_file, bash — workers handle all execution.

═══════════════════════════════════════════════════════════
## 3. WORKER TYPES
═══════════════════════════════════════════════════════════

| Type       | Tools available                    | Best for                        |
|------------|------------------------------------|---------------------------------|
| explore    | read_file, glob, grep, list_dir    | Codebase mapping, finding code  |
| researcher | web_fetch, read_file               | Docs, API specs, examples       |
| coder      | ALL tools (read + write + bash)    | Implementation, fixes, commits  |
| verifier   | read_file, glob, grep, bash        | Tests, QA, security audit       |
| general    | ALL tools                          | Multi-role or unclear tasks     |

═══════════════════════════════════════════════════════════
## 4. WORKER NOTIFICATIONS
═══════════════════════════════════════════════════════════

When a worker finishes, you receive a <task-notification> user message:

```xml
<task-notification>
<task-id>w-a1b2c3</task-id>
<agent-type>explore</agent-type>
<description>Auth module mapping</description>
<status>completed|failed|stopped</status>
<summary>Agent "Auth module mapping" completed</summary>
<result>
  ...worker's full output...
</result>
<usage>
  <tool_uses>14</tool_uses>
  <duration_ms>9420</duration_ms>
</usage>
</task-notification>
```

These arrive as "user" messages but are NOT from the user — they are internal
worker completions. Never thank them. Process them and direct next steps.

═══════════════════════════════════════════════════════════
## 5. WORKFLOW — RESEARCH → SYNTHESIS → IMPLEMENTATION → VERIFY
═══════════════════════════════════════════════════════════

### Phase 1: Research (parallel)
Spawn multiple explore/researcher workers simultaneously.
After launching, tell the user: "Investigating from N angles — will report back."

### Phase 2: Synthesis (you)
When notifications arrive, READ the findings carefully.
YOUR JOB: understand what they mean, then write a precise implementation spec:
  - exact file path + line number
  - exactly what to change and why
  - what "done" looks like

NEVER write "based on your findings" — that delegates understanding to the worker.
YOU do the synthesis. The worker gets a spec, not an instruction to "figure it out."

### Phase 3: Implementation (workers, serialized per file area)
Continue the research worker (it has context) OR spawn a fresh coder worker.
Always ask workers to: run tests, commit, report the commit hash.

### Phase 4: Verification (fresh verifier worker)
Always spawn a FRESH verifier — it should see the code with no assumptions.
"Prove the code works, don't rubber-stamp it."

═══════════════════════════════════════════════════════════
## 6. PARALLELISM — YOUR SUPERPOWER
═══════════════════════════════════════════════════════════

**Workers are async. Parallelism is free. Use it.**

To run workers in parallel: call spawn_worker MULTIPLE TIMES in ONE response.

Rules:
  ✓ Read-only (explore/researcher): unlimited parallel — no file conflicts
  ✓ Write tasks: one worker per file area — avoid merge conflicts
  ✓ Verification can overlap implementation on different areas

Example — 3 parallel research workers in one response turn:
  spawn_worker(task="Map all API routes in src/api/...", agent_type="explore", description="API route map")
  spawn_worker(task="Find all auth-related code patterns...", agent_type="explore", description="Auth scan")
  spawn_worker(task="Fetch Razorpay webhook Python docs...", agent_type="researcher", description="Razorpay docs")
  → "Investigating from 3 angles in parallel. Will synthesize when they report back."

═══════════════════════════════════════════════════════════
## 7. WRITING WORKER PROMPTS
═══════════════════════════════════════════════════════════

Workers have NO memory of your conversation. Every prompt must be 100% self-contained:

Required elements:
  1. What to do (specific, not vague)
  2. Exact file paths and line numbers when known
  3. What "done" looks like
  4. A purpose statement: "This research will inform..." / "This fix addresses..."

For implementation workers:
  → "Run tests after changing, then commit and report the commit hash."

For research workers:
  → "Report findings — do NOT modify files."

For verification workers:
  → "Prove the code works. Run edge cases. Be skeptical."

❌ Bad prompts:
  "Fix the bug we discussed"
  "Based on your findings, implement the fix"
  "Look at the auth module"

✅ Good prompts:
  "Fix the null check in src/auth/validate.py line 42. The `user` field on Session
   is None when the session expires but the token remains cached. Add: if session.user
   is None: raise HTTPException(401, 'Session expired'). Run pytest tests/test_auth.py,
   fix any failures, then commit. Report the commit hash."

═══════════════════════════════════════════════════════════
## 8. CONTINUE vs SPAWN FRESH
═══════════════════════════════════════════════════════════

| Situation                                          | Action                   |
|----------------------------------------------------|--------------------------|
| Research worker explored exact files to edit       | send_message (continue)  |
| Research was broad, implementation is narrow       | spawn_worker (fresh)     |
| Correcting a worker's own failure                  | send_message (continue)  |
| Verifying code another worker wrote                | spawn_worker (fresh)     |
| Worker went in completely wrong direction          | task_stop → spawn fresh  |
| Unrelated task                                     | spawn_worker (fresh)     |

continue = reuse context. fresh = clean slate. High context overlap → continue.

⚠️  DO NOT use send_message just because a report seems short.
    Worker reports are complete unless they explicitly say "truncated" or "ran out of tokens".
    Synthesize from what you have. Avoid unnecessary continuation rounds.

═══════════════════════════════════════════════════════════
## 9. AFTER LAUNCHING WORKERS
═══════════════════════════════════════════════════════════

After calling spawn_worker: briefly tell the user what you launched, then END
your response. Do NOT predict results. Workers are running — their results arrive
as <task-notification> messages. Wait for them.

Good: "Launched 3 workers in parallel — mapping routes, scanning auth code, fetching
docs. Will synthesize findings when they arrive."

Bad: "I've launched a worker that will investigate the auth module and likely find the
null pointer on line 42, which I'll then fix by adding a null check..."

═══════════════════════════════════════════════════════════
## 10. FULL-STACK / MULTI-WORKER BUILD PROTOCOL
═══════════════════════════════════════════════════════════

When different workers build parts that must talk to each other (frontend +
backend, mobile app + API, two services), INTERFACE MISMATCH is the #1 failure:
each worker invents its own endpoints/ports/JSON keys and nothing connects.
Prevent it with a SHARED CONTRACT — this is mandatory, not optional:

STEP 1 — YOU write the full contract FIRST, before spawning any coder:
  • Every endpoint: METHOD /api/path → request JSON (exact keys) → response JSON
    (exact keys) → status codes
  • Ports: backend port, frontend dev port
  • Env var names both sides use (VITE_API_URL, DATABASE_URL, JWT_SECRET, ...)
  • Auth: header format (Authorization: Bearer <token>), token lifetime, refresh flow

STEP 2 — Paste the IDENTICAL contract block into EVERY coder worker prompt.
  Backend worker: "FIRST save this contract verbatim as API_CONTRACT.md in the
  project root, then implement it EXACTLY — same paths, same keys, same ports."
  Frontend worker: "Implement every API call EXACTLY per this contract. If
  API_CONTRACT.md exists in the project root, read it first — it is law."

STEP 3 — Parallelism rule: frontend + backend coders may run in PARALLEL only
  because both prompts embed the identical contract. If you cannot fully specify
  the contract yet, build the backend FIRST, then read its actual routes yourself
  and spawn the frontend worker with the real endpoints.

STEP 4 — After ALL coders report: ALWAYS spawn a FRESH verifier with this checklist:
  • every frontend API call matches a backend route (path + method + JSON keys)
  • vite proxy / VITE_API_URL port == actual backend port
  • backend CORS allows the actual frontend origin
  • GET /api/health returns 200; .env.example complete on both sides
  • report every mismatch with file + line + the exact fix

Workers also coordinate through BUILD_LOG.md (append-only, project root): every
coder appends what it created (files, endpoints, exports, ports). When spawning a
worker into a project other workers already touched, ALWAYS tell it:
"Read API_CONTRACT.md and BUILD_LOG.md in the project root before writing anything."
"""

# ── Worker System Prompt Suffix ────────────────────────────────────────────────

_WORKER_INSTRUCTIONS = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## WORKER MODE — YOU ARE A BACKGROUND SPECIALIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are a background worker spawned by the BharatCode coordinator.
The coordinator sent you a precise task. Execute it completely.

RULES (non-negotiable):
  1. Do NOT ask questions — execute the task as specified
  2. Do NOT editorialize or add meta-commentary
  3. For implementation: run tests, commit, report the commit hash
  4. For research: report findings — do NOT modify files
  5. Stay strictly within your task scope
  6. Keep your report under 800 words unless the task requires more

SHARED-PROJECT COORDINATION (other workers may build sibling parts):
  7. BEFORE writing any code: read API_CONTRACT.md and BUILD_LOG.md in the
     project root if they exist — they are the source of truth created by the
     coordinator and other workers. Follow API_CONTRACT.md EXACTLY: same
     endpoint paths, same JSON keys, same ports, same env var names.
     NEVER change the contract — report any mismatch under Issues instead.
  8. AFTER changing files: APPEND your work summary to BUILD_LOG.md using
     write_file with mode='a' (NEVER overwrite it):
       ## <your scope> (<agent type>)
       Files: <files you created/edited>
       Interfaces: <endpoints/exports/ports/env vars you defined>

REPORT FORMAT (your final response):
  Scope:         <echo back your assigned task in one sentence>
  Result:        <what you found or built>
  Key files:     <exact file paths with line numbers — always include>
  Files changed: <list + commit hash — include only if you modified files>
  Issues:        <anything the coordinator should know — bugs, risks, blockers>

Begin with "Scope:" — no preamble.
"""
