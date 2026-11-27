"""Keep customer evidence revisions in the original workspace, separate from shipped notes."""
from datetime import date
from pathlib import Path

from .client_workspace import journal_for, load, workspace_identity
from .commerce_csv import day
from .first_launch_progress import progress
from .journal import Journal
from .operator_review import pattern
from .operator_v1 import knowledge_search
from .validation import fail, object_only


REVISION_FIELDS = ("entry_id", "version", "action", "expires_on", "result_sha256", "approval_reference",
                   "strategy_id", "source_version", "material_sha256", "plan_sha256", "ad_id")


def revise(workspace, account, *, update=None, plan_dir=None):
    account = pattern(account, r"[1-9][0-9]{0,39}")
    today = date.today().isoformat()
    ad = None
    if update is not None:
        keys = ("schema", *REVISION_FIELDS)
        object_only(update, keys, keys)
        if update["schema"] != "workspace-evidence-revision-v1":
            fail("WORKSPACE_EVIDENCE_UPDATE_SCHEMA")
        if update["action"] not in ("RECORD", "REVOKE") or type(update["version"]) is not int or not 1 <= update["version"] <= 500:
            fail("WORKSPACE_EVIDENCE_REVISION_INVALID")
        pattern(update["entry_id"], r"EV_[A-Z0-9_]{1,60}")
        for key in ("plan_sha256", "material_sha256", "result_sha256"):
            pattern(update[key], r"[a-f0-9]{64}")
        for key in ("approval_reference", "source_version"):
            pattern(update[key], r"[A-Za-z0-9._+-]{1,80}")
        ad = pattern(update["ad_id"], r"[1-9][0-9]{0,39}")
        if update["strategy_id"] not in {r["id"] for r in knowledge_search("all")["claims"]}:
            fail("WORKSPACE_EVIDENCE_STRATEGY_NOT_FOUND")
        expires = day(update["expires_on"])
        if update["action"] == "RECORD":
            if expires < today:
                fail("WORKSPACE_EVIDENCE_ALREADY_EXPIRED")
            launch = progress(workspace, plan_dir, ad_id=ad)
            if launch["account_id"] != account or launch["plan_sha256"] != update["plan_sha256"]:
                fail("WORKSPACE_EVIDENCE_PLAN_MISMATCH")
            # Stored plan selections bind one chosen creative to its original asset fingerprint.
            binding = launch.get("creative_research")
            if "creative_research_batch" in launch:
                row = next((r for r in launch["batch_items"] if r["operation"] == "CREATE_AD"
                            and r["object_id"] == ad), None)
                if row is not None:
                    binding = next((item for item in launch["creative_research_batch"]
                                    if item["asset_sha256"] == row["material_sha256"]), None)
            if not binding or binding.get("asset_sha256") != update["material_sha256"]:
                fail("WORKSPACE_EVIDENCE_MATERIAL_NOT_BOUND")

    ws = journal_for(workspace, existing_only=True)
    with ws.locked(existing_only=True):
        profile = load(ws)
        if f"ACCOUNT:{account}" not in profile["assets"]:
            fail("WORKSPACE_EVIDENCE_ACCOUNT_NOT_MAPPED")
        identity = dict(schema="workspace-evidence-ledger-v1", workspace_sha256=workspace_identity(profile), account=account)
        if update is not None and update["action"] == "RECORD" and launch["workspace_identity_sha256"] != identity["workspace_sha256"]:
            fail("WORKSPACE_EVIDENCE_IDENTITY_CHANGED")
        location = Path(ws.directory) / "evidence-ledger" / account
        if update is None and not location.exists() and not location.is_symlink():
            history = []
        else:
            journal = Journal(location, stage_snapshots=True, existing_only=update is None)

            def validate(value):
                fields = (*identity.keys(), "history")
                object_only(value, fields, fields)
                if any(value[key] != expected for key, expected in identity.items()):
                    fail("WORKSPACE_EVIDENCE_IDENTITY_CHANGED")
                if type(value["history"]) is not list or len(value["history"]) > 200:
                    fail("WORKSPACE_EVIDENCE_HISTORY_LIMIT")

            with journal.locked(existing_only=update is None):
                current, damaged = journal.load(validate)
                if damaged or journal.unpublished_snapshot_count():
                    fail("WORKSPACE_EVIDENCE_RECOVERY_REQUIRED")
                history = list(current["history"]) if current else []
                if update is not None:
                    latest = next((row for row in reversed(history) if row["entry_id"] == update["entry_id"]), None)
                    change = {key: update[key] for key in REVISION_FIELDS}
                    if latest != change:
                        expected = 1 if latest is None else latest["version"] + 1
                        if expected != update["version"] or (latest is None and update["action"] == "REVOKE"):
                            fail("WORKSPACE_EVIDENCE_REVISION_ORDER")
                        immutable = ("plan_sha256", "ad_id", "material_sha256")
                        if latest and any(latest[key] != update[key] for key in immutable):
                            fail("WORKSPACE_EVIDENCE_TARGET_CHANGED")
                        history.append(change)
                        journal.save(dict(identity, history=history), validate)
    visible = {}
    for row in history:
        state = "REVOKED" if row["action"] == "REVOKE" else "EXPIRED" if row["expires_on"] < today else "ACTIVE"
        visible[row["entry_id"]] = dict(row, state=state, evidence_basis="CUSTOMER_WORKSPACE_DECLARATION",
                                      independent_approval_verified=False)
    return dict(status="WORKSPACE_EVIDENCE_REVIEWED", **identity, revisions=len(history), rows=list(visible.values()),
                knowledge_source="RELEASED_STRATEGY_ID_ONLY", network_used=False, actions_authorized=False)
