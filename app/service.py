"""Turns raw Activity Log / Resource Graph data into a time-ordered change feed."""
import csv
import fnmatch
import io
import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Allowed characters for resource group / management group names (also prevents filter injection)
NAME_RE = re.compile(r"^[\w\-\.\(\)]{1,90}$")

# Well-known first-party client app IDs, used to tell how a user made a change
KNOWN_CLIENT_APPS = {
    "c44b4083-3bb0-49c1-b47d-974e53cbdf3c": "Azure Portal",
    "04b07795-8ddb-461a-bbee-02f9e1bf7b46": "Azure CLI",
    "1950a258-227b-4e31-a9cf-717495945fc2": "Azure PowerShell",
    "0c1307d4-29d6-4389-a11c-5cbe7f65d7fa": "Azure Mobile App",
}

# Operations that are effectively reads or validations and are not "changes"
IGNORED_OPERATIONS = [
    "*/read",
    "*/list*/action",
    "*/validate/action",
    "*/whatif/action",
    "*/checknameavailability/action",
    "microsoft.resources/checkresourcename/action",
    "microsoft.authorization/policies/*",
    "microsoft.insights/diagnosticsettings/read",
]

CSV_COLUMNS = [
    "timestamp", "subscription_name", "subscription_id", "resource_group", "resource_name",
    "resource_type", "change_type", "operation", "operation_name", "caller", "caller_name",
    "caller_type", "client", "is_terraform", "is_clickops", "property_changes", "correlation_id",
    "resource_id",
]


class ScopeError(ValueError):
    pass


def parse_time(value, default):
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScopeError(f"Invalid timestamp: {value}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_subscription_excluded(sub, patterns):
    name = (sub.get("name") or "").lower()
    for p in patterns:
        p = p.strip().lower()
        if not p:
            continue
        if p == sub["id"].lower() or fnmatch.fnmatch(name, p):
            return True
    return False


def _resource_name(resource_id):
    parts = [p for p in (resource_id or "").split("/") if p]
    if "providers" in [p.lower() for p in parts]:
        return parts[-1]
    # Resource group or subscription level operations
    return parts[-1] if parts else ""


def _change_type(op, sub_status, arg_type=None):
    op = op.lower()
    if op.endswith("/delete"):
        return "Delete"
    if op.endswith("/action"):
        return "Action"
    if arg_type in ("Create", "Update", "Delete"):
        return arg_type
    if (sub_status or "").lower() == "created":
        return "Create"
    return "Update"


class DeltaService:
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.tfe_ids = {c.lower() for c in config["TFE_CLIENT_IDS"]}
        self._cache = {}
        self._cache_lock = threading.Lock()

    # --------------------------------------------------------------- Caching
    def _cached(self, key, fn):
        ttl = self.config["CACHE_TTL_SECONDS"]
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._cache_lock:
            self._cache[key] = (now, value)
            # Opportunistic eviction of stale entries
            for k in [k for k, v in self._cache.items() if now - v[0] >= ttl]:
                self._cache.pop(k, None)
        return value

    def clear_cache(self):
        with self._cache_lock:
            self._cache.clear()

    # ---------------------------------------------------------------- Scopes
    def management_groups(self):
        return self._cached(("mgs",), self.client.list_management_groups)

    def all_subscriptions(self):
        return self._cached(("subs", None), lambda: self.client.list_subscriptions())

    def resource_groups(self, subscription_id):
        if not GUID_RE.match(subscription_id or ""):
            raise ScopeError("Invalid subscription id")
        return self._cached(("rgs", subscription_id), lambda: self.client.list_resource_groups(subscription_id))

    def resolve_scope(self, scope_type, scope_id=None, resource_group=None):
        """Return (subscriptions, resource_group) for the requested scope."""
        scope_type = (scope_type or "tenant").lower()
        if scope_type == "tenant":
            return self.all_subscriptions(), None
        if scope_type == "managementgroup":
            if not NAME_RE.match(scope_id or ""):
                raise ScopeError("Invalid management group id")
            return self._cached(("subs", scope_id), lambda: self.client.list_subscriptions(scope_id)), None
        if scope_type in ("subscription", "resourcegroup"):
            if not GUID_RE.match(scope_id or ""):
                raise ScopeError("Invalid subscription id")
            subs = [s for s in self.all_subscriptions() if s["id"].lower() == scope_id.lower()]
            if not subs:
                raise ScopeError("Subscription not found or not accessible")
            if scope_type == "resourcegroup":
                if not NAME_RE.match(resource_group or ""):
                    raise ScopeError("Invalid resource group name")
                return subs, resource_group
            return subs, None
        raise ScopeError(f"Unknown scope type: {scope_type}")

    # ------------------------------------------------------------ Fetching
    def _fetch_events(self, subs, start, end, rg):
        def one(sub):
            key = ("events", sub["id"], rg, start.isoformat(), end.isoformat())
            try:
                return sub, self._cached(key, lambda: self.client.get_activity_events(sub["id"], start, end, rg))
            except Exception as exc:  # keep going if a single subscription fails
                log.exception("Failed to read activity log for %s", sub["id"])
                return sub, exc

        results, errors = [], []
        with ThreadPoolExecutor(max_workers=max(1, self.config["FETCH_CONCURRENCY"])) as pool:
            for sub, data in pool.map(one, subs):
                if isinstance(data, Exception):
                    errors.append(f"{sub['name']}: {data}")
                else:
                    results.append((sub, data))
        return results, errors

    def _fetch_resource_changes(self, subs, start, end, rg):
        if not self.config["USE_RESOURCE_GRAPH_CHANGES"] or not subs:
            return {}, None
        # resourcechanges only keeps 14 days of history
        arg_start = max(start, datetime.now(timezone.utc) - timedelta(days=14))
        if arg_start >= end:
            return {}, None
        ids = sorted(s["id"] for s in subs)
        key = ("arg", tuple(ids), rg, arg_start.replace(second=0, microsecond=0).isoformat(), end.isoformat())
        try:
            rows = self._cached(key, lambda: self.client.get_resource_changes(ids, arg_start, end, rg))
        except Exception as exc:
            log.exception("Resource Graph query failed")
            return {}, f"Resource Graph: {exc}"
        index = defaultdict(list)
        for row in rows:
            index[((row.get("correlationId") or "").lower(), (row.get("targetResourceId") or "").lower())].append(row)
        return index, None

    # ----------------------------------------------------------- Normalising
    def _normalise(self, event, sub, arg_index):
        op = (event.get("operationName") or {}).get("value", "")
        claims = event.get("claims") or {}
        caller = event.get("caller") or claims.get("appid") or "unknown"
        appid = (claims.get("appid") or "").lower()
        is_user = "@" in caller or claims.get("idtyp") == "user"
        is_tfe = not is_user and (appid in self.tfe_ids or caller.lower() in self.tfe_ids)
        caller_type = "User" if is_user else ("Terraform" if is_tfe else "Service Principal")

        resource_id = event.get("resourceId") or ""
        correlation = (event.get("correlationId") or "").lower()
        arg_rows = arg_index.get((correlation, resource_id.lower()), [])

        client = None
        props = []
        arg_type = None
        for row in arg_rows:
            client = client or row.get("clientType") or None
            arg_type = arg_type or row.get("changeType")
            changes = row.get("changes") or {}
            if isinstance(changes, str):
                try:
                    changes = json.loads(changes)
                except ValueError:
                    changes = {}
            for path, detail in sorted(changes.items()):
                props.append({
                    "path": path,
                    "before": detail.get("previousValue"),
                    "after": detail.get("newValue"),
                    "type": detail.get("propertyChangeType"),
                })
        if not client:
            client = KNOWN_CLIENT_APPS.get(appid) or ("Terraform" if is_tfe else None)

        return {
            "id": event.get("eventDataId") or event.get("id"),
            "timestamp": event.get("eventTimestamp"),
            "subscription_id": sub["id"],
            "subscription_name": sub["name"],
            "resource_group": event.get("resourceGroupName") or "",
            "resource_id": resource_id,
            "resource_name": _resource_name(resource_id),
            "resource_type": (event.get("resourceType") or {}).get("value", ""),
            "operation": op,
            "operation_name": (event.get("operationName") or {}).get("localizedValue") or op,
            "change_type": _change_type(op, (event.get("subStatus") or {}).get("value"), arg_type),
            "caller": caller,
            "caller_name": claims.get("name") or "",
            "caller_type": caller_type,
            "client_id": appid,
            "client": client or "",
            "is_terraform": is_tfe,
            "is_clickops": is_user,
            "correlation_id": event.get("correlationId") or "",
            "property_changes": props,
            "has_details": bool(arg_rows),
        }

    def _is_change(self, event):
        category = (event.get("category") or {}).get("value", "")
        status = (event.get("status") or {}).get("value", "")
        op = ((event.get("operationName") or {}).get("value") or "").lower()
        if category != "Administrative" or status != "Succeeded":
            return False
        if not (op.endswith("/write") or op.endswith("/delete") or op.endswith("/action")):
            return False
        return not any(fnmatch.fnmatch(op, p) for p in IGNORED_OPERATIONS)

    # ---------------------------------------------------------------- Public
    def get_changes(self, scope_type="tenant", scope_id=None, resource_group=None, start=None, end=None,
                    excluded=None, hide_terraform=False):
        now = datetime.now(timezone.utc)
        max_back = now - timedelta(days=self.config["MAX_LOOKBACK_DAYS"])
        end = min(end or now, now)
        start = max(start or end - timedelta(days=1), max_back)
        if start >= end:
            raise ScopeError("Start time must be before end time")
        # Round to the minute so small slider movements share cache entries
        start = start.replace(second=0, microsecond=0)
        end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)

        subs, rg = self.resolve_scope(scope_type, scope_id, resource_group)
        patterns = self.config["DEFAULT_EXCLUDED_SUBSCRIPTIONS"] if excluded is None else excluded
        # An explicitly chosen single subscription is never excluded
        if scope_type.lower() not in ("subscription", "resourcegroup"):
            subs = [s for s in subs if not is_subscription_excluded(s, patterns)]

        results, errors = self._fetch_events(subs, start, end, rg)
        arg_index, arg_error = self._fetch_resource_changes(subs, start, end, rg)
        if arg_error:
            errors.append(arg_error)

        changes, seen = [], {}
        for sub, events in results:
            for event in events:
                if not self._is_change(event):
                    continue
                item = self._normalise(event, sub, arg_index)
                # Long-running operations can log several Succeeded events; keep the latest
                key = (item["correlation_id"].lower(), item["resource_id"].lower(), item["operation"].lower())
                if key in seen:
                    if item["timestamp"] > changes[seen[key]]["timestamp"]:
                        changes[seen[key]] = item
                    continue
                seen[key] = len(changes)
                changes.append(item)

        if hide_terraform:
            changes = [c for c in changes if not c["is_terraform"]]
        changes.sort(key=lambda c: c["timestamp"], reverse=True)
        return {
            "changes": changes,
            "subscriptions": [{"id": s["id"], "name": s["name"]} for s in subs],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "errors": errors,
        }

    @staticmethod
    def clickops_report(changes, top_n=10, portal_only=False):
        human = [c for c in changes if c["is_clickops"] and (not portal_only or c["client"] == "Azure Portal")]
        by_user = defaultdict(list)
        for c in human:
            by_user[c["caller"].lower()].append(c)

        rows = []
        for caller, items in by_user.items():
            types = Counter(c["change_type"] for c in items)
            clients = Counter(c["client"] or "Unknown" for c in items)
            rows.append({
                "caller": items[0]["caller"],
                "caller_name": next((c["caller_name"] for c in items if c["caller_name"]), ""),
                "total": len(items),
                "creates": types.get("Create", 0),
                "updates": types.get("Update", 0),
                "deletes": types.get("Delete", 0),
                "actions": types.get("Action", 0),
                "resources": len({c["resource_id"].lower() for c in items}),
                "subscriptions": sorted({c["subscription_name"] for c in items}),
                "clients": dict(clients.most_common()),
                "last_change": max(c["timestamp"] for c in items),
            })
        rows.sort(key=lambda r: (-r["total"], r["caller"].lower()))

        totals = Counter(c["caller_type"] for c in changes)
        return {
            "top": rows[:top_n],
            "total_users": len(rows),
            "total_changes": len(changes),
            "clickops_changes": len(human),
            "by_caller_type": dict(totals),
        }

    @staticmethod
    def to_csv(changes):
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for c in changes:
            row = dict(c)
            row["property_changes"] = "; ".join(
                f"{p['path']}: {p['before']} -> {p['after']}" for p in c["property_changes"]
            )
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
        return buf.getvalue()

    @staticmethod
    def report_to_csv(report):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["rank", "caller", "caller_name", "total_changes", "creates", "updates",
                         "deletes", "actions", "distinct_resources", "subscriptions", "clients", "last_change"])
        for i, r in enumerate(report["top"], 1):
            writer.writerow([_csv_safe(v) for v in [
                i, r["caller"], r["caller_name"], r["total"], r["creates"], r["updates"], r["deletes"],
                r["actions"], r["resources"], "; ".join(r["subscriptions"]),
                "; ".join(f"{k}: {v}" for k, v in r["clients"].items()), r["last_change"],
            ]])
        return buf.getvalue()


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported values."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
