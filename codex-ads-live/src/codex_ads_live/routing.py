"""Version-aware method entry routing; never edits or executes a dependency."""
import hashlib
import json
from pathlib import Path
from .validation import fail
from .method_bundle import bundled_route

ROUTES={"ads_plan":"modernlinc-facebook-ads","creative":"modernlinc-creative-planning",
        "analytics":"modernlinc-commerce-data","positioning":"modernlinc-brand-brief"}
BUILTINS={"account_diagnostic":"diagnostics","live_monitor":"analysis","local_runner":"runtime"}


def route(intent,library_root=None):
    if type(intent) is not str:fail("UNKNOWN_INTENT")
    if intent in BUILTINS:return {"handler":BUILTINS[intent],"status":"LOCAL_COMPONENT","live_action_authorized":False}
    if intent not in ROUTES:fail("OUTSIDE_V1_SCOPE")
    if library_root is None:return bundled_route(intent)
    skill=ROUTES[intent]
    lock=json.loads((Path(__file__).resolve().parents[2]/"references/skill-lock.json").read_text(encoding="utf-8"))
    entry=next(x for x in lock["skills"] if x["name"]==skill)
    result={"handler":skill,"version":entry["version"],"status":"SOURCE_NOT_AVAILABLE","source_sha256":entry["entry_sha256"],
            "live_action_authorized":False,"next_step":"提供已批准的对应版本方法包，或继续不依赖该方法的工作"}
    root=Path(library_root).resolve()
    path=root/"skills"/skill/"SKILL.md"
    if not path.exists():return result
    if path.is_symlink() or not path.resolve().is_relative_to(root):fail("METHOD_PATH_OUTSIDE_LIBRARY")
    if path.stat().st_size>1_000_000:fail("METHOD_ENTRY_TOO_LARGE")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=entry["entry_sha256"]:
        result.update(status="SOURCE_VERSION_MISMATCH",next_step="保存新版本来源并审查差异；不静默替换锁定版本")
    else:
        result.update(status="ENTRY_HASH_VERIFIED_METHOD_READ_REQUIRED",next_step="按入口读取当前任务所需方法与引用；先检查其输入准入，返回原14字段合同")
    return result
