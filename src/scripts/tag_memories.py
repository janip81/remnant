"""
Bulk-tag existing memories by keyword analysis.
Run: python src/scripts/tag_memories.py [--dry-run]
"""
import json
import os
import re
import sys

import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]

# ── Tagging rules ─────────────────────────────────────────────────────────────
# Each key is the tag to apply; value is a list of regex patterns (case-insensitive).
# First match wins per tag — order within each group doesn't matter.

RULES: dict[str, list[str]] = {
    # clusters
    "prod-k8s":  [r"\bprod-k8s\b", r"\bprod\.k8s\b"],
    "prod-mgmt": [r"\bprod-mgmt\b", r"\bprod\.mgmt\b", r"\bprod-mgmt\b"],
    "dev-k8s":   [r"\bdev-k8s\b",  r"\bdev\.k8s\b"],
    # apps
    "remnant":        [r"\bremnant\b", r"\bclaude-memory\b", r"claude_memory"],
    "n8n":            [r"\bn8n\b"],
    "frigate":        [r"\bfrigate\b"],
    "nextcloud":      [r"\bnextcloud\b"],
    "ghost":          [r"\bghost\b", r"blog\.threshold"],
    "matrix":         [r"\bmatrix\b", r"\bsynapse\b"],
    "homeassistant":  [r"\bhome.assistant\b", r"\bhomeassistant\b", r"\bha\b.*automation"],
    "minecraft":      [r"\bminecraft\b"],
    "valheim":        [r"\bvalheim\b"],
    "firefly":        [r"\bfirefly\b"],
    "zigbee":         [r"\bzigbee\b", r"\bmosquitto\b", r"\bmqtt\b"],
    "velero":         [r"\bvelero\b"],
    "argocd":         [r"\bargocd\b", r"\bargo.cd\b"],
    "keycloak":       [r"\bkeycloak\b"],
    "backup-operator":[r"\bbackup.operator\b"],
    "cnpg":           [r"\bcnpg\b", r"\bcloudnativepg\b", r"\bpg-main\b", r"\bcnpg-"],
    "cilium":         [r"\bcilium\b"],
    "cert-manager":   [r"\bcert-manager\b", r"\bcert\.manager\b"],
    "capi":           [r"\bcapi\b", r"\bcluster.api\b", r"\bcapv\b", r"\bmachinedeployment\b"],
    # topics
    "upgrade":    [r"\bupgrade\b", r"\bkubeadm\b"],
    "backup":     [r"\bbackup\b", r"\bbarman\b", r"\bsnapshot\b"],
    "etcd":       [r"\betcd\b"],
    "bgp":        [r"\bbgp\b", r"\bfrr\b", r"\bbgp.peer\b"],
    "networking": [r"\bdns\b", r"\bgateway\b", r"\bhttproute\b", r"\bcilium.bgp\b", r"\bnetwork.policy\b"],
    "monitoring": [r"\bprometheus\b", r"\bgrafana\b", r"\bservicemonitor\b", r"\balert\b"],
    "helm":       [r"\bhelm\b", r"\bchart\b"],
    "storage":    [r"\bpvc\b", r"\bpersistentvolume\b", r"\bvsphere.csi\b", r"\bvolumeattachment\b"],
    "auth":       [r"\bbearer\b", r"\bjwt\b", r"\btls\b", r"\bssl\b", r"\bsso\b"],
}

_compiled = {tag: [re.compile(p, re.IGNORECASE) for p in patterns] for tag, patterns in RULES.items()}


def infer_tags(text: str) -> list[str]:
    tags = []
    for tag, patterns in _compiled.items():
        if any(p.search(text) for p in patterns):
            tags.append(tag)
    return sorted(tags)


def run(dry_run: bool = False):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT id, payload FROM mem0")
    rows = cur.fetchall()

    tagged = 0
    skipped_already = 0
    skipped_empty = 0

    for row_id, payload in rows:
        text = payload.get("data", "")
        if not text:
            skipped_empty += 1
            continue

        # Resolve current tags
        meta = payload.get("metadata") or {}
        inner = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else meta
        existing_tags = inner.get("tags") or []
        if existing_tags:
            skipped_already += 1
            continue

        new_tags = infer_tags(text)
        if not new_tags:
            continue

        print(f"  {str(row_id)[:8]}… → {new_tags}")
        tagged += 1

        if not dry_run:
            if isinstance(meta.get("metadata"), dict):
                meta["metadata"]["tags"] = new_tags
            else:
                meta["tags"] = new_tags
            payload["metadata"] = meta
            cur.execute("UPDATE mem0 SET payload = %s WHERE id = %s",
                        (json.dumps(payload), row_id))

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nDone. tagged={tagged}  skipped_already={skipped_already}  skipped_empty={skipped_empty}")
    if dry_run:
        print("(dry run — no changes written)")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
