import os
import json
import subprocess
import tempfile
import logging
import shutil
import time
from datetime import datetime
from typing import Annotated, List, Dict, Any, Union

# ============================================================
# WINDOWS PATH FIX — must run before any tool imports
# Flask/Python processes on Windows only inherit the SYSTEM
# PATH, not the user PATH where tools like r2 are installed.
# We read both PATH entries from the registry and merge them
# into os.environ["PATH"] right here, before anything else.
# ============================================================
def _inject_windows_user_path():
    if os.name != "nt":
        return
    try:
        import winreg

        path_entries = []

        # 1. Read SYSTEM PATH from registry
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
            ) as key:
                sys_path, _ = winreg.QueryValueEx(key, "Path")
                path_entries.append(sys_path)
        except Exception:
            pass  # fall back to whatever is already in os.environ

        # 2. Read USER PATH from registry
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment"
            ) as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
                path_entries.append(user_path)
        except Exception:
            pass

        if path_entries:
            # Merge registry entries with whatever is already set,
            # deduplicate while preserving order
            existing = os.environ.get("PATH", "").split(os.pathsep)
            new_entries = os.pathsep.join(path_entries).split(os.pathsep)
            seen = set()
            merged = []
            for entry in new_entries + existing:
                entry = entry.strip()
                if entry and entry.lower() not in seen:
                    seen.add(entry.lower())
                    merged.append(entry)

            os.environ["PATH"] = os.pathsep.join(merged)
            logging.getLogger("MalwareAnalyzer").info(
                "Windows PATH injected from registry successfully."
            )
    except Exception as e:
        logging.getLogger("MalwareAnalyzer").warning(
            f"Could not inject Windows PATH from registry: {e}"
        )

_inject_windows_user_path()
# ============================================================

import r2pipe
from groq import Groq, RateLimitError
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename
from langgraph.graph import StateGraph, END

# Load environment variables
load_dotenv()

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MalwareAnalyzer")

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Groq Setup
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
else:
    logger.warning("GROQ_API_KEY not found in environment. Analyst Agent will fail.")
    client = None

# --- Windows Compatibility ---
IS_WINDOWS = os.name == "nt"

def _detect_r2_cmd() -> str:
    """Find the radare2 executable by name — r2 or radare2."""
    for candidate in ("r2", "radare2", "r2.exe", "radare2.exe"):
        found = shutil.which(candidate)
        if found:
            logger.info(f"radare2 found: '{found}'")
            return found
    logger.error("radare2 not found on PATH. Install from https://rada.re")
    return ""

R2_CMD = _detect_r2_cmd()

def normalize_path(path: str) -> str:
    """Normalize path separators for r2pipe (always forward slashes)."""
    return path.replace("\\", "/")

# --- angr availability (optional on Windows) ---
ANGR_AVAILABLE = False
try:
    import angr
    ANGR_AVAILABLE = True
except ImportError:
    if IS_WINDOWS:
        logger.warning("angr not available on Windows — Solver agent will be disabled.")
    else:
        logger.warning("angr not installed — Solver agent will be disabled.")

# --- Security & Safety ---
FORBIDDEN_KEYWORDS = ["git", "curl", "wget", "ssh", "nc", "rm", "mv", ">", "|", "&", ";"]

def is_safe_command(command: str) -> bool:
    command_lower = command.lower()
    return not any(kw in command_lower for kw in FORBIDDEN_KEYWORDS)

def truncate_data(data, max_chars=5000):
    """Deeply truncate data structures for LLM consumption."""
    if isinstance(data, str):
        if len(data) > max_chars:
            return data[:max_chars] + "\n... [truncated]"
        return data
    elif isinstance(data, list):
        truncated_list = data[:50]
        if len(data) > 50:
            truncated_list.append(f"... [truncated {len(data) - 50} more items]")
        return [truncate_data(item, max_chars=500) for item in truncated_list]
    elif isinstance(data, dict):
        return {k: truncate_data(v, max_chars=500) for k, v in data.items()}
    return data

# --- Agent State ---
class AgentState(Dict):
    binary_path: str
    context: List[Dict[str, Any]]
    results: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    current_action: Dict[str, Any]
    status: str
    logs: List[str]

# --- Executor Agent (Radare2 & Ghidra) ---
class ExecutorAgent:
    def __init__(self, binary_path: str):
        self.binary_path = normalize_path(binary_path)
        self.r2 = None

    def execute(self, tool: str, commands: List[str]) -> Dict[str, Any]:
        if tool == "radare2":
            return self._run_radare2(commands)
        elif tool == "ghidra":
            return self._run_ghidra(commands)
        else:
            return {"status": "error", "message": f"Unknown tool: {tool}"}

    def _run_radare2(self, commands: List[str]) -> Dict[str, Any]:
        try:
            if not self.r2:
                # Use radare2 on Windows, r2 on Linux/macOS
                self.r2 = r2pipe.open(self.binary_path, flags=["-2"])
                self.r2.cmd("aaa")  # Analyze all

            output_data = {}
            for cmd in commands:
                if not is_safe_command(cmd):
                    return {"status": "error", "message": f"Unsafe command blocked: {cmd}"}

                if not cmd.endswith("j"):
                    res = self.r2.cmd(cmd)
                else:
                    try:
                        res = self.r2.cmdj(cmd)
                    except Exception:
                        res = self.r2.cmd(cmd)
                output_data[cmd] = truncate_data(res)

            return {"status": "ok", "tool": "radare2", "data": output_data}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _run_ghidra(self, commands: List[str]) -> Dict[str, Any]:
        return {
            "status": "ok",
            "tool": "ghidra",
            "data": {"message": "Ghidra headless script execution simulated. Static analysis only."}
        }

# --- Solver Agent (angr) ---
class SolverAgent:
    def __init__(self, binary_path: str):
        self.binary_path = normalize_path(binary_path)

    def solve(self, target: str, goal: str) -> Dict[str, Any]:
        if not ANGR_AVAILABLE:
            return {
                "status": "error",
                "message": "angr is not installed. On Windows, install via WSL2 or: pip install angr"
            }

        try:
            project = angr.Project(self.binary_path, auto_load_libs=False)
            try:
                target_addr = int(target, 16)
            except Exception:
                symbol = project.loader.main_object.get_symbol(target)
                if symbol:
                    target_addr = symbol.rebase_addr
                else:
                    return {"status": "error", "message": f"Could not resolve target: {target}"}

            state = project.factory.entry_state()
            simgr = project.factory.simulation_manager(state)
            simgr.explore(find=target_addr)

            if simgr.found:
                sol = simgr.found[0].posix.dumps(0)
                return {
                    "status": "ok",
                    "solution": sol.hex() if isinstance(sol, bytes) else str(sol),
                    "notes": f"Path found to {hex(target_addr)}"
                }
            else:
                return {"status": "error", "message": "No path found to target."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

# --- Analyst Agent (Groq / LLaMA) ---
class AnalystAgent:
    def __init__(self):
        self.system_prompt = """
You are a senior malware analyst. You control a multi-agent analysis pipeline.

OUTPUT FORMAT: Respond with ONLY a JSON object. No markdown. No prose. Raw JSON only.
{
  "action": "execute" | "solve" | "stop",
  "tool": "radare2" | "ghidra" | "angr" | null,
  "commands": ["list", "of", "commands"],
  "target": "",
  "reason": "one sentence — internal note for yourself",
  "expected_output": "one sentence",
  "finding": {
    "title": "Short headline, e.g. 'Packer detected' or 'Suspicious API calls found'",
    "severity": "info" | "low" | "medium" | "high" | "critical",
    "summary": "2-3 sentences in plain English. Assume the reader is not technical. Explain what was found and why it matters.",
    "indicators": ["list", "of", "specific", "IOCs", "or", "flags"]
  }
}

ANALYSIS RULES:
- Never guess. Only report what the tool output proves.
- If nothing suspicious is found in a step, set severity to "info" and say so plainly.
- finding.summary must be understandable by a non-technical person.
- finding.indicators should be concrete: function names, strings, addresses, flags.
- Start with: ["ij", "iIj", "isj", "iEj"] — file info, imports, symbols, exports.
- Prefer targeted commands. Use 'pd 30 @ sym.main' not 'pdf @ sym.main'.
- Maximum 2 commands per turn.

SEVERITY GUIDE:
- critical: shellcode, process injection, ransomware patterns, C2 beaconing
- high: suspicious API combos (VirtualAlloc+WriteProcessMemory), anti-debug, packed sections
- medium: obfuscated strings, unusual imports, self-modifying code indicators
- low: non-standard compiler, stripped symbols, unusual section names
- info: routine findings, standard libraries, expected behaviour
"""

    def analyze(self, state: AgentState) -> Dict[str, Any]:
        is_initial = len(state['context']) == 0
        status_msg = "INITIAL START" if is_initial else "CONTINUING ANALYSIS"

        prompt = (
            f"Status: {status_msg}\n"
            f"Context: {json.dumps(state['context'][-3:])}\n"
            f"Last Results: {json.dumps(state['results'][-1:] if state['results'] else [])}\n\n"
            f"Decision?"
        )

        while True:
            try:
                if not GROQ_API_KEY or not client:
                    raise ValueError("GROQ_API_KEY is missing or client not initialized.")

                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                text = response.choices[0].message.content.strip()
                plan = json.loads(text)

                # Rate limit: ~5 requests per minute
                time.sleep(12)

                if is_initial and plan.get("action") == "stop":
                    return {
                        "action": "execute",
                        "tool": "radare2",
                        "commands": ["iIj", "isj", "iEj"],
                        "reason": "Initial analysis to identify file type, symbols, and exports.",
                        "expected_output": "Basic binary info",
                        "finding": {
                            "title": "Starting Analysis",
                            "severity": "info",
                            "summary": "The analyst is beginning the investigation by gathering basic information about the file.",
                            "indicators": []
                        }
                    }

                return plan

            except RateLimitError as e:
                error_str = str(e)
                try:
                    wait_time = int(e.response.headers.get("retry-after", 60))
                except Exception:
                    wait_time = 60

                limit_type = "per day" if "day" in error_str.lower() else "per minute"
                logger.warning(f"Groq API Rate Limit ({limit_type}) Exhausted. Waiting {wait_time}s...")

                for i in range(wait_time, -1, -1):
                    msg = f"Rate limit exhausted {limit_type}, retrying in {i} sec"
                    if state['logs'] and "retrying in" in state['logs'][-1]:
                        state['logs'][-1] = msg
                    else:
                        state['logs'].append(msg)
                    time.sleep(1)

                state['logs'].append("Retrying after rate limit...")
                continue

            except Exception as e:
                error_str = str(e)
                error_msg = f"Analyst error: {error_str}"
                logger.error(error_msg)
                state['logs'].append(f"DEBUG: {error_msg}")
                return {"action": "stop", "reason": error_msg}

# --- Orchestrator (LangGraph) ---
class Orchestrator:
    def __init__(self, binary_path: str):
        self.binary_path = normalize_path(binary_path)
        self.history = []

        # Check required tools upfront
        missing = []
        if not R2_CMD:
            missing.append(
                "radare2 — not found as 'r2' or 'radare2'. "
                "Download from https://rada.re/n/radare2.html and ensure it's on PATH."
            )
        if not ANGR_AVAILABLE:
            # angr is optional — warn but don't block
            logger.warning(
                "angr is not available. The Solver agent will be disabled. "
                "On Windows, install via: pip install angr  (or use WSL2 for best results)."
            )

        if missing:
            raise EnvironmentError(
                "Required tools not found:\n  - " + "\n  - ".join(missing)
            )

        self.executor = ExecutorAgent(binary_path)
        self.solver = SolverAgent(binary_path)
        self.analyst = AnalystAgent()

    def analyst_node(self, state: AgentState):
        state['logs'].append("Analyst is thinking...")
        plan = self.analyst.analyze(state)
        state['current_action'] = plan
        state['context'].append({"role": "analyst", "plan": plan})

        finding = plan.get('finding')
        if finding:
            if 'findings' not in state:
                state['findings'] = []
            state['findings'].append(finding)
            severity = finding.get('severity', 'info').upper()
            title = finding.get('title', '')
            state['logs'].append(f"[{severity}] {title}")

        action_str = plan.get('action')
        tool_str = plan.get('tool')
        state['logs'].append(f"Analyst decision: {action_str} → {tool_str or 'stopping'}")
        return state

    def executor_node(self, state: AgentState):
        action = state['current_action']
        state['logs'].append(f"Executor running {action['tool']} commands: {action['commands']}")
        result = self.executor.execute(action['tool'], action['commands'])
        state['results'].append(result)
        status_msg = "OK" if result['status'] == 'ok' else "ERROR"
        state['logs'].append(f"Executor completed with status: {status_msg}")
        return state

    def solver_node(self, state: AgentState):
        action = state['current_action']

        if not ANGR_AVAILABLE:
            state['logs'].append("Solver skipped — angr is not available on this system.")
            state['results'].append({
                "status": "skipped",
                "message": "angr not available. Install via pip or use WSL2 on Windows."
            })
            return state

        state['logs'].append(f"Solver running angr for target: {action['target']}")
        result = self.solver.solve(action['target'], action.get('reason', ''))
        state['results'].append(result)
        status_msg = "OK" if result['status'] == 'ok' else "ERROR"
        state['logs'].append(f"Solver completed with status: {status_msg}")
        return state

    def should_continue(self, state: AgentState):
        action = state['current_action'].get("action")
        if action == "execute":
            return "executor"
        elif action == "solve":
            # Route to solver regardless; solver_node handles the angr check gracefully
            return "solver"
        else:
            return END

    def build_graph(self):
        workflow = StateGraph(AgentState)
        workflow.add_node("analyst", self.analyst_node)
        workflow.add_node("executor", self.executor_node)
        workflow.add_node("solver", self.solver_node)

        workflow.set_entry_point("analyst")
        workflow.add_conditional_edges("analyst", self.should_continue, {
            "executor": "executor",
            "solver": "solver",
            END: END
        })
        workflow.add_edge("executor", "analyst")
        workflow.add_edge("solver", "analyst")

        return workflow.compile()

# --- Global State for UI ---
ANALYSIS_RUNS = {}

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    filepath = normalize_path(filepath)
    file.save(filepath)

    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    ANALYSIS_RUNS[run_id] = {
        "status": "running",
        "logs": ["System initialized.", f"Uploaded: {filename}"],
        "results": [],
        "findings": [],
        "filepath": filepath
    }

    def run_analysis():
        try:
            orch = Orchestrator(filepath)
            graph = orch.build_graph()
            initial_state = {
                "binary_path": filepath,
                "context": [],
                "results": [],
                "findings": [],
                "current_action": {},
                "status": "starting",
                "logs": ANALYSIS_RUNS[run_id]["logs"]
            }

            for output in graph.stream(initial_state):
                for node_name, state_update in output.items():
                    ANALYSIS_RUNS[run_id]["logs"] = state_update["logs"]
                    ANALYSIS_RUNS[run_id]["results"] = state_update["results"]
                    ANALYSIS_RUNS[run_id]["findings"] = state_update.get("findings", [])

            ANALYSIS_RUNS[run_id]["status"] = "done"
            ANALYSIS_RUNS[run_id]["logs"].append("Analysis complete.")

        except EnvironmentError as e:
            ANALYSIS_RUNS[run_id]["status"] = "error"
            ANALYSIS_RUNS[run_id]["logs"].append(f"Setup failed: {str(e)}")
        except Exception as e:
            ANALYSIS_RUNS[run_id]["status"] = "error"
            ANALYSIS_RUNS[run_id]["logs"].append(f"CRITICAL ERROR: {str(e)}")

    import threading
    threading.Thread(target=run_analysis, daemon=True).start()

    return render_template('index.html', run_id=run_id)

@app.route('/status/<run_id>')
def status(run_id):
    run = ANALYSIS_RUNS.get(run_id, {})
    return jsonify({
        "status": run.get("status", "not_found"),
        "logs": run.get("logs", []),
        "results": run.get("results", []),
        "findings": run.get("findings", [])
    })

@app.route('/stop/<run_id>', methods=['POST'])
def stop_analysis(run_id):
    if run_id in ANALYSIS_RUNS:
        ANALYSIS_RUNS[run_id]["status"] = "stopped"
        ANALYSIS_RUNS[run_id]["logs"].append("Analysis stopped by user.")
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Run not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)