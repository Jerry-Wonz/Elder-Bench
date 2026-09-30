"""作业编排：分离子进程、进度、停止、存活判定、run 锁。设计 §4.2-§4.5、§4.7。

## 为什么必须是分离子进程（设计 §4.2）

Streamlit 每次交互都从脚本首行重跑。若把跑批放进脚本内或 session_state 里的线程，
会出现三种失败：关掉浏览器任务就没了、服务热重载任务就没了、点任何按钮整脚本重跑
导致进度组件状态错乱。因此作业用 `Popen(start_new_session=True)` 起为独立进程组，
元数据落 `job.json`，**作业生命周期与浏览器生命周期解耦**。

## 三条不解析 stdout 的替代口径（设计 §4.3、§4.4）

1. 进度 = 各卷有效结果行数 ÷ 任务条数，全部从文件推导。
2. 卷级失败 = 该卷 manifest 的 `outcome.err`，没有 manifest 即没跑过。
3. `run_id` 的收集 = 启动前后 `eval/runs/` 的文件名集合差集。

解析 `progress 1200/3960` 这类输出粒度只有 50 条一条，且打印格式一改就静默失效。

## 进程组，不是进程（设计 §4.5）

`run_suite_all.sh` 自己 spawn `python run_eval.py`。**只杀 shell 的 pid，python 子进程
会变成孤儿继续占 GPU**，界面却显示"已停止"。所以停止必须是 `os.killpg`，
`start_new_session=True` 不是可选项。

## 跨平台：Windows 上两处 API 不可用，必须绕开

- `os.killpg` 在 Windows 上不存在。
- **`os.kill(pid, 0)` 在 Windows 上会终止进程**：CPython 的 `os.kill` 对除
  `CTRL_C_EVENT` / `CTRL_BREAK_EVENT` 之外的任何 sig 都调用 `TerminateProcess`。
  用它做存活探测等于把作业杀掉。故 Windows 分支改用
  `OpenProcess` + `GetExitCodeProcess`（ctypes，仍在标准库内）。

控制台的部署目标是服务器（Linux，设计 §3.1），POSIX 是主路径；Windows 分支只为
本机开发自测能跑通，`platform_note()` 会把它标出来。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from paths import DATA_DIR, EVAL_DIR, JOBS_DIR, REPO_ROOT, RUNS_DIR, RUN_LOCK, safe_join
from paths import read_json, write_json
import artifacts
import cli
import config

IS_POSIX = os.name == "posix"

# 状态取值（设计 §4.4）。lost 与 failed 的区别：failed 有退出码，lost 没有。
RUNNING, FINISHED, FAILED, STOPPED, LOST = "running", "finished", "failed", "stopped", "lost"
TERMINAL = (FINISHED, FAILED, STOPPED, LOST)

# run 类作业占用 .run.lock，离线作业不占（设计 §4.7）。
RUN_KINDS = ("run_batch", "run_single")

BATCH_SCRIPT = cli.BATCH_SCRIPT

# 存活的 Popen 句柄。模块级即可：Streamlit 的服务进程在同一进程内反复重跑脚本，
# 但 `import jobs` 取的是 sys.modules 里的同一个模块。持有句柄才能 poll() 回收
# 僵尸进程并拿到真实退出码；服务重启后句柄丢失，退回 pid 存活判定（见 §4.5 的 lost）。
_PROCS: dict[str, subprocess.Popen] = {}


def platform_note() -> str:
    if IS_POSIX:
        return ""
    return ("当前为 Windows：作业停止退化为 taskkill /T /F，进程存活判定走 "
            "OpenProcess。控制台的部署目标是 Linux 服务器，此处仅供本机自测。")


# ---------------------------------------------------------------- 小工具
def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_job_id(subject: str, kind: str) -> str:
    return f"job_{time.strftime('%Y%m%d_%H%M%S')}_{kind}_{subject}"


def sha256_file(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> tuple[Optional[str], Optional[bool]]:
    """(commit, dirty)。启动瞬间记录，事后无法还原（与 manifest 同一纪律）。

    每一项单独 try：SMB 路径下的 git 需要 `-c safe.directory=*`，
    环境不满足时返回 (None, None) 而不是抛错让作业起不来。
    """
    def run(*args: str) -> Optional[str]:
        try:
            p = subprocess.run(["git", "-c", "safe.directory=*", *args],
                               cwd=str(REPO_ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15)
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    commit = run("rev-parse", "HEAD")
    dirty_out = run("status", "--porcelain")
    dirty = None if dirty_out is None else bool(dirty_out)
    return commit, dirty


# ---------------------------------------------------------------- 存活判定
def _posix_alive(pid: int) -> bool:
    """POSIX 存活判定。僵尸进程视为不存活：它已退出，只是还没被回收。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # 存在但不属于当前用户
    # 存在。再排除僵尸：读到 State: Z 说明已退出、只是没被回收。
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        if '") ' in stat and stat.rsplit('") ', 1)[1].split()[0] == "Z":
            return False
    except OSError:
        pass
    return True


def _windows_alive(pid: int) -> bool:
    """Windows 存活判定。绝不使用 os.kill(pid, 0)：那会终止进程。"""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    return _posix_alive(pid) if IS_POSIX else _windows_alive(pid)


# ---------------------------------------------------------------- run 锁
class LockError(RuntimeError):
    def __init__(self, message: str, owner: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.owner = owner or {}


def lock_owner() -> Optional[dict[str, Any]]:
    """读锁文件内容。不存在或损坏返回 None。"""
    if not RUN_LOCK.exists():
        return None
    try:
        return read_json(RUN_LOCK)
    except (OSError, ValueError):
        return None


def lock_is_stale(owner: Optional[dict[str, Any]]) -> bool:
    """陈旧锁：持有者 pid 已不存在。与 §4.5 的存活判定复用同一段逻辑。"""
    if owner is None:
        return True
    return not pid_alive(owner.get("pid"))


def reserve_lock(job_id: str) -> None:
    """原子占锁。抛 LockError 表示已有作业在跑。

    用 O_CREAT|O_EXCL 而不是"先检查再创建"：两个浏览器标签会同时通过检查，
    于是两个批次写同一个结果文件，行会交错（设计 §4.7 明确要避免的情形）。
    先以界面进程的 pid 占位，Popen 成功后用 update_lock() 换成作业 pid。

    这里直接用 `os.open` 而不经 `paths.open_writable`，是本仓库唯一一处绕过
    写边界断言的地方。原因是 O_EXCL 的原子性由内核保证，而 `open()` 没有对应
    的创建即失败模式。`RUN_LOCK` 本身在 `JOBS_DIR`（唯一白名单根）之内，
    边界并未放松；换用 `open()` 反而会引入检查与创建之间的竞态。
    """
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(str(RUN_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = lock_owner()
            if lock_is_stale(owner):
                # 陈旧锁：持有者已死，接管。只重试一次，避免与并发接管互相踩。
                try:
                    RUN_LOCK.unlink()
                except OSError:
                    pass
                if attempt == 1:
                    continue
            raise LockError("已有 run 作业在进行中，同刻只允许一个（设计 §4.7）", owner)
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"job_id": job_id, "pid": os.getpid(),
                           "reserved_at": now_str(), "state": "reserving"}, f,
                          ensure_ascii=False)
            return
    raise LockError("无法取得 run 锁")


def update_lock(job_id: str, pid: int) -> None:
    """把占位锁换成作业 pid。此后锁的存活性即作业的存活性。"""
    write_json(RUN_LOCK, {"job_id": job_id, "pid": pid, "state": "running",
                          "reserved_at": now_str()})


def release_lock(job_id: Optional[str] = None) -> bool:
    """释放锁。仅当锁属于该 job_id（或未指定）时释放，避免误删他人的锁。"""
    owner = lock_owner()
    if owner is None:
        return False
    if job_id and owner.get("job_id") != job_id:
        return False
    try:
        RUN_LOCK.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 卷顺序
_BATCH_SUITES_RE = re.compile(r"^SUITES=\((.*?)\)\s*$", re.M | re.S)


def batch_suites() -> list[str]:
    """批次脚本的卷顺序。从脚本里读，不在此处复制一份（设计 §4.4）。

    复制一份会让界面显示的顺序与实际执行顺序可能不一致，而顺序是有语义的：
    脚本把红线相关卷排在前面，中途中断也已有门禁结论。
    """
    p = REPO_ROOT / BATCH_SCRIPT
    if not p.exists():
        return []
    m = _BATCH_SUITES_RE.search(p.read_text(encoding="utf-8"))
    if not m:
        return []
    # 数组跨行书写，行尾的续行反斜杠会作为独立 token 被切出来；与 `#` 注释一并剔掉。
    body = m.group(1).replace("\\", " ").splitlines()
    tokens = [t for line in body for t in line.split("#", 1)[0].split()]
    return tokens


# ---------------------------------------------------------------- 启动
def _popen_kwargs(log_path: Path, cwd: Path) -> dict[str, Any]:
    """分离子进程的启动参数。start_new_session 是 §4.5 的前提，不是可选项。

    cwd 必须用 `Command.cwd` 而不是仓库根：`run_eval.py` 这类 argv 是相对
    `eval/` 的，从仓库根启动会报"No such file or directory"，而日志里那句报错
    不会提示是工作目录错了。批次的 argv 是 `bash scripts/run_suite_all.sh`，
    相对仓库根，其 `Command.cwd` 也正是仓库根，两者由构造方各自给对。
    """
    log = open(log_path, "ab", buffering=0)
    kw: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    }
    if IS_POSIX:
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return kw


def job_dir(job_id: str) -> Path:
    return safe_join(JOBS_DIR, job_id)


def job_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def _persist(record: dict[str, Any]) -> None:
    """落盘作业记录，剥掉下划线开头的运行时字段。

    `job()` / `jobs()` 会给读出的 dict 补 `_dir` 等展示用键；若原样写回，
    job.json 里会混进界面内部的路径字段，与 manifest「只记事实」的纪律不符。
    """
    clean = {k: v for k, v in record.items() if not k.startswith("_")}
    write_json(job_path(clean["job_id"]), clean)


def start(cmd: cli.Command, *, kind: str, subject: str,
          note: str = "") -> dict[str, Any]:
    """起一个作业。返回 job 记录。

    run 类作业先占锁再启动；启动失败（Popen 抛错）时释放锁，不留陈旧锁。
    启动前记录 `runs/` 的既有 run_id 集合，供事后做差集（设计 §4.4）。
    """
    if not cmd.runnable:
        raise ValueError(f"命令不可执行：{cmd.explain()}")

    needs_lock = kind in RUN_KINDS
    job_id = new_job_id(subject, kind)

    # 占锁在 mkdir 之前：占锁失败会抛 LockError，若此前已建目录，被拒绝的启动
    # 会留下一个空作业目录（列表里看不见，因为 `jobs()` 只认 `*/job.json`）。
    if needs_lock:
        reserve_lock(job_id)

    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)

    runs_before = sorted(p.stem for p in RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
    commit, dirty = git_state()
    cfg_hash = sha256_file(config.CONFIG_PATH)
    # 启动瞬间的逐卷已完成条数。没有它，一个断点续跑的作业会在界面上从 100% 起步，
    # 用户会误判为界面算错（设计 §4.3 明确要求把"此前已完成"显出来）。
    done_before = snapshot_done(kind, subject, cmd)

    try:
        proc = subprocess.Popen(cmd.argv, **_popen_kwargs(d / "stdout.log", cmd.cwd))
    except OSError as e:
        if needs_lock:
            release_lock(job_id)
        shutil.rmtree(d, ignore_errors=True)
        raise RuntimeError(f"无法启动作业：{e!r}") from e

    record = {
        "job_id": job_id,
        "kind": kind,
        "subject": subject,
        "note": note,
        "argv": cmd.argv,
        "display": cmd.display,
        "cwd": str(cmd.cwd),
        "pid": proc.pid,
        # start_new_session=True 时子进程自成一组的组长，pgid == pid。
        "pgid": proc.pid,
        "started": now_str(),
        "started_ts": time.time(),
        "finished": None,
        "finished_ts": None,
        "status": RUNNING,
        "exit_code": None,
        "elapsed_s": None,
        "run_ids": [],
        "runs_before": runs_before,
        "done_before": done_before,
        "config_sha256": cfg_hash,
        "git_commit": commit,
        "git_dirty": dirty,
        "stdout_log": "stdout.log",
    }
    _persist(record)
    _PROCS[job_id] = proc
    if needs_lock:
        update_lock(job_id, proc.pid)
    return record


def start_batch(subject: str, *, concurrency: int = 16) -> dict[str, Any]:
    cmd = cli.cmd_batch(subject, concurrency=concurrency)
    return start(cmd, kind="run_batch", subject=subject,
                 note=f"全量 10 卷，并发 {concurrency}")


def start_single(suite: str, subject: str, *, limit: Optional[int] = None,
                 concurrency: Optional[int] = None) -> dict[str, Any]:
    cmd = cli.cmd_run(suite, subject, limit=limit, concurrency=concurrency)
    note = f"单卷 {suite}" + (f"，limit={limit}" if limit else "")
    return start(cmd, kind="run_single", subject=subject, note=note)


def start_offline(cmd: cli.Command, *, subject: str, kind: str, note: str = "") -> dict[str, Any]:
    """离线作业（report / rescore / compare / rulecheck / verify …）。不占 run 锁。"""
    return start(cmd, kind=kind, subject=subject, note=note)


# ---------------------------------------------------------------- 读取与刷新
def jobs() -> list[dict[str, Any]]:
    """全部作业，新的在前。"""
    if not JOBS_DIR.exists():
        return []
    out = []
    for p in sorted(JOBS_DIR.glob("*/job.json"), reverse=True):
        try:
            j = read_json(p)
        except (OSError, ValueError):
            continue
        j["_dir"] = str(p.parent)
        out.append(j)
    out.sort(key=lambda j: j.get("started_ts") or 0, reverse=True)
    return out


def job(job_id: str) -> Optional[dict[str, Any]]:
    p = job_path(job_id)
    if not p.exists():
        return None
    try:
        j = read_json(p)
    except (OSError, ValueError):
        return None
    j["_dir"] = str(p.parent)
    return j


def tail_log(job_id: str, *, max_bytes: int = 6000) -> str:
    """读 stdout.log 尾部。仅用于人工排查，不作任何状态判定（设计 §4.3）。"""
    p = job_dir(job_id) / "stdout.log"
    if not p.exists():
        return ""
    size = p.stat().st_size
    with open(p, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read()
    return data.decode("utf-8", errors="replace")


def refresh(record: dict[str, Any]) -> dict[str, Any]:
    """就地刷新作业状态并落盘。返回同一个 dict。

    状态判定顺序：
    1. 已终态 → 原样返回，不再改动（终态一旦写下就不应被后续轮询改写）。
    2. 持有 Popen 句柄 → poll()，能回收僵尸并拿到真实退出码。
    3. 无句柄（界面重启过）→ 用 pid 存活判定；进程不在且无退出码 → lost。
    """
    if record.get("status") in TERMINAL:
        return record

    job_id = record["job_id"]
    proc = _PROCS.get(job_id)

    if proc is not None:
        code = proc.poll()
        if code is None:
            return record                      # 仍在跑
        _PROCS.pop(job_id, None)
    else:
        if pid_alive(record.get("pid")):
            return record                      # 仍在跑（句柄已丢，只能靠 pid）
        # 进程已不在但没拿到退出码：交给 _exit_code_of 用 waitpid 兜底，
        # 兜不到就返回 None → lost（设计 §4.5）。
        code = _exit_code_of(record, None)

    record["exit_code"] = code
    record["finished"] = now_str()
    record["finished_ts"] = time.time()
    started_ts = record.get("started_ts")
    if started_ts:
        record["elapsed_s"] = round(record["finished_ts"] - started_ts, 1)
    record["run_ids"] = collect_run_ids(record)
    record["status"] = _status_from_exit(code, record)
    _persist(record)

    if record["kind"] in RUN_KINDS:
        release_lock(job_id)
    return record


def _exit_code_of(record: dict[str, Any], polled: Optional[int]) -> Optional[int]:
    """拿到退出码，拿不到返回 None（→ lost）。

    无 Popen 句柄时，若进程是本进程的直系子进程仍可 waitpid 取回；已被他人回收
    或跨服务重启的只能返回 None，这正是设计 §4.5 要区分 lost 与 failed 的原因。
    """
    if polled is not None:
        return polled
    if not IS_POSIX:
        return None
    try:
        pid, status = os.waitpid(int(record["pid"]), os.WNOHANG)
    except ChildProcessError:
        return None                            # 不是我们的子进程
    except OSError:
        return None
    if pid == 0:
        return None                            # 还在跑（理论上不该走到这）
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        # 被信号终止：SIGTERM/SIGKILL 多来自"停止"按钮，负数编码以示区分。
        return -os.WTERMSIG(status)
    return None


def _status_from_exit(code: Optional[int], record: dict[str, Any]) -> str:
    if code is None:
        return LOST
    if code == 0:
        # 退出码 0 但可能是"停止"后 runner 正常收尾；以是否曾发出停止为准。
        return STOPPED if record.get("stop_requested") else FINISHED
    if record.get("stop_requested"):
        return STOPPED
    return FAILED


def collect_run_ids(record: dict[str, Any]) -> list[str]:
    """本次作业新增的 run_id = 当前 runs/ 集合 − 启动前快照，再按 subject 收窄。

    目录差集是确定性的，不依赖任何打印格式（设计 §4.4）。
    """
    if not RUNS_DIR.exists():
        return []
    before = set(record.get("runs_before") or [])
    now = {p.stem for p in RUNS_DIR.glob("*.json")}
    new = now - before
    subj = record.get("subject")
    kept = sorted((r for r in new if r == subj or r.startswith(f"{subj}_")), reverse=True)
    # 若 subject 收敛不到任何新 run（例如命令实际作用于别的 subject），退回全集，
    # 避免显示"0 个 run"掩盖事实。这是记录事实而非猜测。
    return kept or sorted(new, reverse=True)


# ---------------------------------------------------------------- 停止
def stop(job_id: str, *, grace: float = 8.0) -> tuple[bool, str]:
    """停止作业：先 SIGTERM 整个进程组，超时后 SIGKILL。返回 (是否发出, 说明)。

    按**进程组**而非进程（设计 §4.5）：run_suite_all.sh 会 spawn python，
    只杀 shell 会让 python 变孤儿继续占 GPU。

    停止的代价有上界：runner 在请求返回后立即落盘，收到 SIGTERM 时丢失的是
    已发出未返回的请求，上限约为 concurrency 条。已落盘成果不受影响，
    重发同一作业即断点续跑。故界面直接说明即可，不弹警告。
    """
    record = job(job_id)
    if record is None:
        return False, "作业不存在"
    record = refresh(record)
    if record.get("status") in TERMINAL:
        return False, f"作业已处于终态：{record['status']}"

    pid = int(record["pid"])
    record["stop_requested"] = True
    record["stop_requested_at"] = now_str()
    _persist(record)

    if IS_POSIX:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as e:
            return False, f"无法向进程组发送 SIGTERM：{e!r}"
        deadline = time.time() + grace
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.25)
        if pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
    else:
        # Windows：taskkill /T 杀整棵进程树，语义上等价于 killpg。
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=grace + 5)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"taskkill 失败：{e!r}"

    time.sleep(0.3)
    refresh(job(job_id) or record)
    return True, ("已发出停止。已落盘的结果不受影响；重发同一作业会从断点续跑补齐，"
                  "不会产生重复条目。")


# ---------------------------------------------------------------- 进度
@dataclass
class SuiteProgress:
    """一卷的进度。全部从文件推导（设计 §4.3）。"""

    suite: str
    done: int                  # 有效结果行数（口径同 runner.load_results）
    total: int                 # 任务条数（--limit 时为 min(limit, total)）
    errors: int                # error 行数，不进报告但说明失败
    phase: str                 # 未开始 / 进行中 / 完成 / 未跑满 / 部分失败
    run_id: Optional[str] = None
    manifest_err: Optional[int] = None
    before: int = 0            # 作业启动瞬间该卷的有效条数

    @property
    def pct(self) -> float:
        return 0.0 if not self.total else 100.0 * self.done / self.total

    @property
    def added(self) -> int:
        """本次新增条数。断点续跑时 done 一开始就很高，这个差值才是本次的进展。"""
        return max(0, self.done - self.before)

    @property
    def label(self) -> str:
        if self.before:
            return f"{self.phase} {self.done}/{self.total}（本次新增 {self.added}）"
        return f"{self.phase} {self.done}/{self.total}"

    @property
    def resumed(self) -> bool:
        """是否是断点续跑（启动时已有成果）。界面据此提示"此前已完成"。"""
        return self.before > 0


def _limit_of(record: dict[str, Any]) -> Optional[int]:
    """从 argv 里取 --limit N。界面据此把分母改成 min(N, 任务总数)。"""
    argv = record.get("argv") or []
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
    return None


def _suite_of_single(record: dict[str, Any]) -> Optional[str]:
    argv = record.get("argv") or []
    for i, a in enumerate(argv):
        if a == "--suite" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def suites_of(record: dict[str, Any]) -> list[str]:
    """该作业覆盖的卷。批次 = 脚本里的 10 卷；单卷 = argv 里的 --suite。"""
    if record.get("kind") == "run_batch":
        return batch_suites()
    one = _suite_of_single(record)
    return [one] if one else []


def snapshot_done(kind: str, subject: str, cmd: cli.Command) -> dict[str, int]:
    """启动瞬间各卷的有效条数。只读，且在 Popen 之前调用。"""
    if kind == "run_batch":
        suites = batch_suites()
    elif kind == "run_single":
        argv = cmd.argv
        suites = ([argv[i + 1] for i, a in enumerate(argv)
                   if a == "--suite" and i + 1 < len(argv)] or [])
    else:
        return {}
    out: dict[str, int] = {}
    for s in suites:
        p = artifacts.result_path(subject, s)
        out[s] = artifacts.load_counts(p).valid if p.exists() else 0
    return out


def manifest_err_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """卷名 -> 该卷 manifest 的 outcome。用于卷级失败检测（设计 §4.3）。

    一个 run_id 一份 manifest，其 `data.suites` 的键即本次跑的卷。
    """
    out: dict[str, dict[str, Any]] = {}
    for rid in record.get("run_ids") or []:
        m = artifacts.run_manifest(rid)
        if not m:
            continue
        for s in (m.get("data") or {}).get("suites", {}):
            out[s] = {"run_id": rid, "outcome": m.get("outcome") or {}, "kind": m.get("kind")}
    return out


def progress(record: dict[str, Any]) -> list[SuiteProgress]:
    """逐卷进度。批次作业给 10 行，单卷作业给 1 行。"""
    suites = suites_of(record)
    if not suites:
        return []

    limit = _limit_of(record)
    subject = record.get("subject") or ""
    errs = manifest_err_map(record)
    running = record.get("status") == RUNNING
    before = record.get("done_before") or {}
    rows: list[SuiteProgress] = []

    for s in suites:
        tp = DATA_DIR / f"tasks_{s}.jsonl"
        total = artifacts.count_lines(tp)
        if limit is not None:
            total = min(limit, total)
        rp = artifacts.result_path(subject, s)
        c = artifacts.load_counts(rp) if rp.exists() else None
        done = c.valid if c else 0
        errors = c.error if c else 0

        info = errs.get(s)
        merr = None
        if info and isinstance(info.get("outcome"), dict):
            merr = info["outcome"].get("err")

        if done == 0:
            phase = "未开始"
        elif total and done < total:
            phase = "进行中" if running else "未跑满"
        elif total and done > total:
            phase = "超出任务数"
        elif merr:
            phase = "部分失败"
        else:
            phase = "完成"
        rows.append(SuiteProgress(suite=s, done=done, total=total, errors=errors,
                                  phase=phase, run_id=(info or {}).get("run_id"),
                                  manifest_err=merr, before=int(before.get(s) or 0)))
    return rows


def aggregate_progress(rows: list[SuiteProgress]) -> dict[str, Any]:
    """作业级进度 = 各卷有效行数之和 ÷ 任务条数之和（设计 §4.3）。"""
    done = sum(r.done for r in rows)
    total = sum(r.total for r in rows)
    return {
        "done": done,
        "total": total,
        "pct": 0.0 if not total else 100.0 * done / total,
        "errors": sum(r.errors for r in rows),
        "n_suites": len(rows),
        "n_done_suites": sum(1 for r in rows if r.phase == "完成"),
    }


def live_elapsed(record: dict[str, Any]) -> Optional[float]:
    """已运行秒数。终态用落盘的 elapsed_s，运行中用当前时间减 started_ts。

    实时算而不是只读落盘值：落盘值只在终态写一次，运行期间恒为 None，
    界面会一直显示"—"。
    """
    if record.get("elapsed_s") is not None:
        return float(record["elapsed_s"])
    if record.get("status") != RUNNING:
        return None
    started = record.get("started_ts")
    return round(time.time() - float(started), 1) if started else None


def current_lock_job() -> Optional[dict[str, Any]]:
    owner = lock_owner()
    if owner is None:
        return None
    j = job(owner.get("job_id") or "")
    return j or owner


def lock_active() -> bool:
    return not lock_is_stale(lock_owner())


# ---------------------------------------------------------------- 维护
def orphan_job_dirs() -> list[str]:
    """有目录但无 job.json 的作业目录名。

    来源：`start()` 在写 job.json 之前失败的残留、人工删掉 job.json、或旧版本
    留下的目录。这些目录对 `jobs()` 不可见（它只认 `*/job.json`），也不被
    `cleanup_job` 受理，会一直占盘且从界面上无从清理。单独列出来给维护页。
    """
    if not JOBS_DIR.exists():
        return []
    return sorted(p.name for p in JOBS_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("job_")
                  and not (p / "job.json").exists())


def cleanup_job(job_id: str, *, force: bool = False) -> tuple[bool, str]:
    """删除作业目录，返回 (是否删除, 说明)。只动 `eval/jobs/**`（写白名单内）。

    正在运行的作业默认拒绝删除。原因不只是"别删正在写的文件"：子进程还持有
    `stdout.log` 的句柄，Windows 会拒绝删除该文件，而 `rmtree` 在
    `ignore_errors=True` 下会把失败吞掉，留下一个**半删除的目录**（job.json 没了，
    日志还在），从列表里消失却仍占着盘。故这里必须让失败可见。

    `force=True` 先停止作业再删；停止后仍有句柄占用的，如实报错。

    无 job.json 的孤儿目录也受理：否则它们既不在列表里，又删不掉。
    """
    record = job(job_id)
    if record is None:
        d = job_dir(job_id)
        if not (d.exists() and job_id.startswith("job_")):
            return False, "作业不存在"
        try:
            shutil.rmtree(d)
        except OSError as e:
            return False, f"删除孤儿目录失败：{e!r}"
        return True, "已删除（无 job.json 的孤儿目录）"
    record = refresh(record)
    if record.get("status") == RUNNING:
        if not force:
            return False, "作业仍在运行，先停止再删除"
        stop(job_id)
        record = job(job_id) or record
    d = job_dir(job_id)
    if not d.exists():
        return False, "作业目录已不存在"
    try:
        shutil.rmtree(d)
    except OSError as e:
        return False, f"删除失败（可能有进程仍占用该目录）：{e!r}"
    _PROCS.pop(job_id, None)
    release_lock(job_id)
    return True, "已删除"
