"""Thin REST client for Azure Resource Manager, Activity Logs and Resource Graph.

Authenticates as an App Registration (client credentials). The service principal
needs at least `Reader` (or `Monitoring Reader`) on the scopes you want to see,
assigned at the root / management group level for tenant-wide visibility.
"""
import logging
import threading
import time
from datetime import datetime, timezone

import msal
import requests

log = logging.getLogger(__name__)

API_SUBSCRIPTIONS = "2022-12-01"
API_MANAGEMENT_GROUPS = "2023-04-01"
API_ACTIVITY_LOG = "2015-04-01"
API_RESOURCE_GRAPH = "2022-10-01"

ACTIVITY_SELECT = ",".join([
    "authorization", "caller", "claims", "correlationId", "eventDataId", "eventName",
    "category", "eventTimestamp", "id", "operationName", "resourceGroupName",
    "resourceProviderName", "resourceType", "resourceId", "status", "subStatus",
    "subscriptionId", "httpRequest", "properties",
])


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AzureClient:
    def __init__(self, config):
        self.endpoint = config["AZURE_MANAGEMENT_ENDPOINT"].rstrip("/")
        self._app = msal.ConfidentialClientApplication(
            client_id=config["AZURE_CLIENT_ID"],
            client_credential=config["AZURE_CLIENT_SECRET"],
            authority=f"{config['AZURE_AUTHORITY_HOST'].rstrip('/')}/{config['AZURE_TENANT_ID']}",
        )
        self._scope = [f"{self.endpoint}/.default"]
        self._session = requests.Session()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ HTTP
    def _token(self) -> str:
        with self._lock:
            result = self._app.acquire_token_for_client(scopes=self._scope)
        if "access_token" not in result:
            raise RuntimeError(f"Token acquisition failed: {result.get('error_description', result)}")
        return result["access_token"]

    def _request(self, method, url, **kwargs):
        if url.startswith("/"):
            url = self.endpoint + url
        for attempt in range(5):
            resp = self._session.request(
                method, url, headers={"Authorization": f"Bearer {self._token()}"}, timeout=60, **kwargs
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("Azure throttled/errored (%s) on %s; retrying in %ss", resp.status_code, url, wait)
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def _paged(self, url, params=None):
        while url:
            data = self._request("GET", url, params=params)
            yield from data.get("value", [])
            url, params = data.get("nextLink"), None

    # ---------------------------------------------------------------- Scopes
    def list_management_groups(self):
        return [
            {"id": mg["name"], "name": mg["properties"].get("displayName") or mg["name"]}
            for mg in self._paged("/providers/Microsoft.Management/managementGroups",
                                  {"api-version": API_MANAGEMENT_GROUPS})
        ]

    def list_subscriptions(self, management_group=None):
        if management_group:
            subs = []
            for d in self._paged(
                f"/providers/Microsoft.Management/managementGroups/{management_group}/descendants",
                {"api-version": API_MANAGEMENT_GROUPS},
            ):
                if d.get("type", "").lower().endswith("/subscriptions"):
                    subs.append({"id": d["name"], "name": d["properties"].get("displayName") or d["name"]})
            return subs
        return [
            {"id": s["subscriptionId"], "name": s.get("displayName", s["subscriptionId"])}
            for s in self._paged("/subscriptions", {"api-version": API_SUBSCRIPTIONS})
            if s.get("state", "Enabled") in ("Enabled", "PastDue", "Warned")
        ]

    def list_resource_groups(self, subscription_id):
        return sorted(
            rg["name"] for rg in self._paged(
                f"/subscriptions/{subscription_id}/resourcegroups", {"api-version": "2021-04-01"}
            )
        )

    # ------------------------------------------------------------- Activity
    def get_activity_events(self, subscription_id, start, end, resource_group=None):
        flt = f"eventTimestamp ge '{_iso(start)}' and eventTimestamp le '{_iso(end)}'"
        if resource_group:
            flt += f" and resourceGroupName eq '{resource_group}'"
        return list(self._paged(
            f"/subscriptions/{subscription_id}/providers/Microsoft.Insights/eventtypes/management/values",
            {"api-version": API_ACTIVITY_LOG, "$filter": flt, "$select": ACTIVITY_SELECT},
        ))

    # -------------------------------------------------------- Resource Graph
    def get_resource_changes(self, subscription_ids, start, end, resource_group=None):
        query = f"""
resourcechanges
| extend changeTime = todatetime(properties.changeAttributes.timestamp)
| where changeTime between (datetime({_iso(start)}) .. datetime({_iso(end)}))
| extend targetResourceId = tostring(properties.targetResourceId),
         changeType = tostring(properties.changeType),
         correlationId = tostring(properties.changeAttributes.correlationId),
         clientType = tostring(properties.changeAttributes.clientType),
         changedBy = tostring(properties.changeAttributes.changedBy),
         changedByType = tostring(properties.changeAttributes.changedByType),
         operation = tostring(properties.changeAttributes.operation),
         changes = properties.changes
{f"| where resourceGroup =~ '{resource_group}'" if resource_group else ""}
| project changeTime, targetResourceId, changeType, correlationId, clientType,
          changedBy, changedByType, operation, changes
"""
        rows = []
        # Resource Graph accepts up to 1000 subscriptions per request
        for i in range(0, len(subscription_ids), 1000):
            body = {
                "subscriptions": subscription_ids[i:i + 1000],
                "query": query,
                "options": {"resultFormat": "objectArray", "$top": 1000},
            }
            while True:
                data = self._request(
                    "POST", "/providers/Microsoft.ResourceGraph/resources",
                    params={"api-version": API_RESOURCE_GRAPH}, json=body,
                )
                rows.extend(data.get("data", []))
                token = data.get("$skipToken")
                if not token:
                    break
                body["options"]["$skipToken"] = token
        return rows
