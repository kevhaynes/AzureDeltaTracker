"""Mock Azure client producing realistic Activity Log and Resource Graph data.

Data is generated in the same raw shape the Azure REST APIs return, so the
normalisation code paths are exercised exactly as they would be in production.
Generation is deterministic for a given seed, relative to process start time.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

PORTAL_APP_ID = "c44b4083-3bb0-49c1-b47d-974e53cbdf3c"
CLI_APP_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
POWERSHELL_APP_ID = "1950a258-227b-4e31-a9cf-717495945fc2"

# Fixed client IDs so they can be put in TFE_CLIENT_IDS for local testing
MOCK_TFE_CLIENT_IDS = [
    "11111111-aaaa-4aaa-8aaa-000000000001",  # TFE production
    "11111111-aaaa-4aaa-8aaa-000000000002",  # TFE non-production
    "11111111-aaaa-4aaa-8aaa-000000000003",  # TFE disaster recovery
]

MANAGEMENT_GROUPS = {
    # id: (display name, parent)
    "contoso-root": ("Contoso Root", None),
    "contoso-platform": ("Platform", "contoso-root"),
    "contoso-landingzones": ("Landing Zones", "contoso-root"),
    "contoso-corp": ("Corp", "contoso-landingzones"),
    "contoso-online": ("Online", "contoso-landingzones"),
    "contoso-sandboxes": ("Sandboxes", "contoso-root"),
}

SUBSCRIPTIONS = [
    # (name, management group, resource groups)
    ("Contoso-Connectivity", "contoso-platform", ["rg-hub-network", "rg-dns", "rg-firewall"]),
    ("Contoso-Identity", "contoso-platform", ["rg-identity", "rg-adds"]),
    ("Contoso-Management", "contoso-platform", ["rg-monitoring", "rg-automation", "rg-backup"]),
    ("Contoso-Corp-Prod", "contoso-corp", ["rg-erp-prod", "rg-crm-prod", "rg-data-prod"]),
    ("Contoso-Corp-PreProd", "contoso-corp", ["rg-erp-preprod", "rg-crm-preprod"]),
    ("Contoso-Online-Prod", "contoso-online", ["rg-web-prod", "rg-api-prod", "rg-shared-prod"]),
    ("Contoso-Online-Dev", "contoso-online", ["rg-web-dev", "rg-api-dev"]),
    ("Contoso-Sandbox-Alice", "contoso-sandboxes", ["rg-alice-play"]),
    ("Contoso-Sandbox-Shared", "contoso-sandboxes", ["rg-sbx-shared", "rg-sbx-labs"]),
]

# (display name, upn, weight) - weight skews who makes most changes
USERS = [
    ("Alice Admin", "alice.admin@contoso.com", 14),
    ("Bob Builder", "bob.builder@contoso.com", 11),
    ("Carol Cloud", "carol.cloud@contoso.com", 9),
    ("Dave Devops", "dave.devops@contoso.com", 7),
    ("Erin Engineer", "erin.engineer@contoso.com", 6),
    ("Frank Firewall", "frank.firewall@contoso.com", 5),
    ("Grace Gateway", "grace.gateway@contoso.com", 4),
    ("Heidi Helpdesk", "heidi.helpdesk@contoso.com", 3),
    ("Ivan Infra", "ivan.infra@contoso.com", 3),
    ("Judy Jumpbox", "judy.jumpbox@contoso.com", 2),
    ("Mallory Manual", "mallory.manual@contoso.com", 2),
    ("Niaj Network", "niaj.network@contoso.com", 1),
    ("Olivia Ops", "olivia.ops@contoso.com", 1),
]

OTHER_SERVICE_PRINCIPALS = [
    ("sp-azure-devops-pipelines", "22222222-bbbb-4bbb-8bbb-000000000001"),
    ("sp-backup-automation", "22222222-bbbb-4bbb-8bbb-000000000002"),
    ("mi-func-cleanup", "22222222-bbbb-4bbb-8bbb-000000000003"),
]

# Resource templates: type, name prefix, operation display, updatable props, actions
RESOURCE_TYPES = [
    {
        "type": "Microsoft.Network/networkSecurityGroups/securityRules",
        "parent": ("Microsoft.Network/networkSecurityGroups", "nsg"),
        "prefix": "rule",
        "display": "Create or Update Security Rule",
        "props": [
            ("properties.sourceAddressPrefix", ["10.0.0.0/8", "*", "203.0.113.24/32", "VirtualNetwork"]),
            ("properties.destinationPortRange", ["443", "3389", "22", "*", "1433"]),
            ("properties.access", ["Allow", "Deny"]),
            ("properties.priority", [100, 110, 200, 4000]),
        ],
        "actions": [],
    },
    {
        "type": "Microsoft.Storage/storageAccounts",
        "prefix": "st",
        "display": "Create/Update Storage Account",
        "props": [
            ("properties.allowBlobPublicAccess", [False, True]),
            ("properties.minimumTlsVersion", ["TLS1_0", "TLS1_2"]),
            ("properties.networkAcls.defaultAction", ["Deny", "Allow"]),
            ("tags.CostCentre", ["CC100", "CC200", "CC300"]),
        ],
        "actions": [("regeneratekey/action", "Regenerate Storage Account Keys")],
    },
    {
        "type": "Microsoft.Compute/virtualMachines",
        "prefix": "vm",
        "display": "Create or Update Virtual Machine",
        "props": [
            ("properties.hardwareProfile.vmSize", ["Standard_B2s", "Standard_D2s_v5", "Standard_D4s_v5", "Standard_E8s_v5"]),
            ("properties.diagnosticsProfile.bootDiagnostics.enabled", [True, False]),
            ("tags.Owner", ["platform-team", "app-team", "unknown"]),
        ],
        "actions": [
            ("restart/action", "Restart Virtual Machine"),
            ("deallocate/action", "Deallocate Virtual Machine"),
            ("start/action", "Start Virtual Machine"),
        ],
    },
    {
        "type": "Microsoft.Web/sites",
        "prefix": "app",
        "display": "Update website",
        "props": [
            ("properties.httpsOnly", [True, False]),
            ("properties.siteConfig.alwaysOn", [True, False]),
            ("properties.siteConfig.minTlsVersion", ["1.0", "1.2"]),
        ],
        "actions": [("restart/action", "Restart Web App")],
    },
    {
        "type": "Microsoft.KeyVault/vaults",
        "prefix": "kv",
        "display": "Update Key Vault",
        "props": [
            ("properties.enablePurgeProtection", [True, False]),
            ("properties.networkAcls.defaultAction", ["Deny", "Allow"]),
            ("properties.publicNetworkAccess", ["Disabled", "Enabled"]),
        ],
        "actions": [],
    },
    {
        "type": "Microsoft.Sql/servers/databases",
        "parent": ("Microsoft.Sql/servers", "sql"),
        "prefix": "sqldb",
        "display": "Create/Update Database",
        "props": [
            ("sku.name", ["S0", "S1", "P1", "GP_Gen5_2"]),
            ("properties.maxSizeBytes", [2147483648, 268435456000]),
        ],
        "actions": [],
    },
    {
        "type": "Microsoft.Network/publicIPAddresses",
        "prefix": "pip",
        "display": "Create or Update Public Ip Address",
        "props": [("properties.idleTimeoutInMinutes", [4, 10, 30]), ("sku.name", ["Basic", "Standard"])],
        "actions": [],
    },
    {
        "type": "Microsoft.Authorization/roleAssignments",
        "prefix": None,
        "display": "Create role assignment",
        "props": [],
        "actions": [],
        "create_delete_only": True,
    },
]

NOISE_OPERATIONS = [
    ("Microsoft.Storage/storageAccounts/listKeys/action", "List Storage Account Keys"),
    ("Microsoft.Web/sites/config/list/action", "List Web App Settings"),
    ("Microsoft.Resources/deployments/validate/action", "Validate Deployment"),
]


def _guid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


class MockAzureClient:
    def __init__(self, config, now=None):
        self.rng = random.Random(config.get("MOCK_SEED", 42))
        self.now = now or datetime.now(timezone.utc)
        self.days = config.get("MAX_LOOKBACK_DAYS", 90)
        self.subscriptions = []
        for name, mg, rgs in SUBSCRIPTIONS:
            self.subscriptions.append({
                "id": _guid(self.rng), "name": name, "mg": mg, "rgs": rgs,
            })
        self.resources = self._build_resources()
        self.events, self.changes = self._build_events()

    # ------------------------------------------------------------ Generation
    def _build_resources(self):
        resources = []
        for sub in self.subscriptions:
            env = sub["name"].split("-")[-1].lower()
            for rg in sub["rgs"]:
                for rt in RESOURCE_TYPES:
                    if rt.get("create_delete_only"):
                        continue
                    for n in range(self.rng.randint(1, 2)):
                        base = f"{rt['prefix']}-{rg.replace('rg-', '')}-{n + 1:02d}"
                        if rt["type"] == "Microsoft.Storage/storageAccounts":
                            base = base.replace("-", "")[:24]
                        rid = f"/subscriptions/{sub['id']}/resourceGroups/{rg}/providers/"
                        if "parent" in rt:
                            ptype, pprefix = rt["parent"]
                            rid += f"{ptype}/{pprefix}-{rg.replace('rg-', '')}/{rt['type'].split('/')[-1]}/{base}"
                        else:
                            rid += f"{rt['type']}/{base}"
                        state = {path: self.rng.choice(values) for path, values in rt["props"]}
                        resources.append({"sub": sub, "rg": rg, "rt": rt, "id": rid, "state": state, "env": env})
        return resources

    def _pick_actor(self, sub):
        sandbox = "sandbox" in sub["name"].lower()
        roll = self.rng.random()
        human_share = 0.75 if sandbox else 0.28
        if roll < human_share:
            name, upn, _ = self.rng.choices(USERS, weights=[u[2] for u in USERS])[0]
            client = self.rng.choices(
                [(PORTAL_APP_ID, "Azure Portal"), (CLI_APP_ID, "Azure CLI"), (POWERSHELL_APP_ID, "Azure PowerShell")],
                weights=[80, 14, 6],
            )[0]
            return {"kind": "User", "caller": upn, "name": name, "appid": client[0], "client": client[1]}
        if roll < human_share + 0.12:
            name, appid = self.rng.choice(OTHER_SERVICE_PRINCIPALS)
            return {"kind": "App", "caller": appid, "name": name, "appid": appid, "client": "Azure SDK"}
        if sandbox:
            appid = MOCK_TFE_CLIENT_IDS[1]
        elif sub["name"].endswith(("Dev", "PreProd")):
            appid = MOCK_TFE_CLIENT_IDS[1]
        else:
            appid = self.rng.choices(MOCK_TFE_CLIENT_IDS, weights=[85, 0, 15])[0]
        return {"kind": "App", "caller": appid, "name": "terraform-enterprise", "appid": appid, "client": "Terraform"}

    def _random_time(self):
        while True:
            offset = timedelta(seconds=self.rng.uniform(0, self.days * 86400))
            ts = self.now - offset
            # Bias towards working hours on weekdays
            if ts.weekday() >= 5 and self.rng.random() < 0.8:
                continue
            if not 7 <= ts.hour <= 19 and self.rng.random() < 0.7:
                continue
            return ts

    def _build_events(self):
        events, changes = [], []
        for _ in range(1800):
            res = self.rng.choice(self.resources)
            sub, rt = res["sub"], res["rt"]
            actor = self._pick_actor(sub)
            ts = self._random_time()
            correlation = _guid(self.rng)
            roll = self.rng.random()
            prop_changes = {}
            sub_status = "OK"

            if roll < 0.04 and NOISE_OPERATIONS:
                op, display = self.rng.choice(NOISE_OPERATIONS)
                resource_id, rtype = res["id"], rt["type"]
                kind = "noise"
            elif roll < 0.10:
                # Role assignment granted/removed
                rtype = "Microsoft.Authorization/roleAssignments"
                resource_id = f"/subscriptions/{sub['id']}/resourceGroups/{res['rg']}/providers/{rtype}/{_guid(self.rng)}"
                kind = self.rng.choice(["Create", "Delete"])
                op = f"{rtype}/{'write' if kind == 'Create' else 'delete'}"
                display = "Create role assignment" if kind == "Create" else "Delete role assignment"
                sub_status = "Created" if kind == "Create" else "OK"
            elif roll < 0.20 and rt["actions"]:
                action, display = self.rng.choice(rt["actions"])
                op, resource_id, rtype, kind = f"{rt['type']}/{action}", res["id"], rt["type"], "Action"
            elif roll < 0.25:
                op, display = f"{rt['type']}/delete", f"Delete {rt['type'].split('/')[-1]}"
                resource_id, rtype, kind = res["id"] + "-old", rt["type"], "Delete"
            elif roll < 0.33:
                op, display = f"{rt['type']}/write", rt["display"]
                resource_id, rtype, kind, sub_status = res["id"] + "-new", rt["type"], "Create", "Created"
            else:
                op, display = f"{rt['type']}/write", rt["display"]
                resource_id, rtype, kind = res["id"], rt["type"], "Update"
                for path, values in self.rng.sample(rt["props"], k=min(len(rt["props"]), self.rng.randint(1, 2))):
                    before = res["state"].get(path)
                    after = self.rng.choice([v for v in values if v != before] or values)
                    res["state"][path] = after
                    prop_changes[path] = {
                        "previousValue": None if before is None else str(before),
                        "newValue": str(after),
                        "changeCategory": "User",
                        "propertyChangeType": "Update",
                    }

            failed = kind != "noise" and self.rng.random() < 0.04
            begin = self._event(sub, res["rg"], resource_id, rtype, op, display, actor, correlation,
                                ts - timedelta(seconds=self.rng.randint(1, 40)), "Started", "", "BeginRequest")
            end = self._event(sub, res["rg"], resource_id, rtype, op, display, actor, correlation, ts,
                              "Failed" if failed else "Succeeded",
                              "BadRequest" if failed else sub_status, "EndRequest")
            events.extend([begin, end])

            if not failed and kind in ("Create", "Update", "Delete") and ts > self.now - timedelta(days=14):
                changes.append({
                    "changeTime": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "targetResourceId": resource_id,
                    "changeType": kind,
                    "correlationId": correlation,
                    "clientType": actor["client"],
                    "changedBy": actor["caller"],
                    "changedByType": "User" if actor["kind"] == "User" else "AppId",
                    "operation": op,
                    "changes": prop_changes,
                    "_sub": sub["id"],
                    "_rg": res["rg"],
                })
        return events, changes

    def _event(self, sub, rg, resource_id, rtype, op, display, actor, correlation, ts, status, sub_status, phase):
        claims = {
            "appid": actor["appid"],
            "idtyp": "user" if actor["kind"] == "User" else "app",
            "http://schemas.microsoft.com/identity/claims/objectidentifier": _guid(self.rng),
        }
        if actor["kind"] == "User":
            claims["name"] = actor["name"]
            claims["http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn"] = actor["caller"]
        return {
            "authorization": {"action": op, "scope": resource_id},
            "caller": actor["caller"],
            "claims": claims,
            "correlationId": correlation,
            "eventDataId": _guid(self.rng),
            "eventName": {"value": phase, "localizedValue": "Begin request" if phase == "BeginRequest" else "End request"},
            "category": {"value": "Administrative", "localizedValue": "Administrative"},
            "eventTimestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "id": f"{resource_id}/events/{_guid(self.rng)}/ticks/{int(ts.timestamp() * 1e7)}",
            "level": "Informational",
            "operationName": {"value": op, "localizedValue": display},
            "resourceGroupName": rg,
            "resourceProviderName": {"value": rtype.split("/")[0]},
            "resourceType": {"value": rtype},
            "resourceId": resource_id,
            "status": {"value": status, "localizedValue": status},
            "subStatus": {"value": sub_status, "localizedValue": sub_status},
            "subscriptionId": sub["id"],
            "httpRequest": {"clientIpAddress": f"10.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"},
        }

    # ------------------------------------------------------------ Interface
    def _mg_descendants(self, mg_id):
        ids = {mg_id}
        changed = True
        while changed:
            changed = False
            for child, (_, parent) in MANAGEMENT_GROUPS.items():
                if parent in ids and child not in ids:
                    ids.add(child)
                    changed = True
        return ids

    def list_management_groups(self):
        return [{"id": k, "name": v[0]} for k, v in MANAGEMENT_GROUPS.items()]

    def list_subscriptions(self, management_group=None):
        subs = self.subscriptions
        if management_group:
            allowed = self._mg_descendants(management_group)
            subs = [s for s in subs if s["mg"] in allowed]
        return [{"id": s["id"], "name": s["name"]} for s in subs]

    def list_resource_groups(self, subscription_id):
        for s in self.subscriptions:
            if s["id"] == subscription_id:
                return sorted(s["rgs"])
        return []

    def get_activity_events(self, subscription_id, start, end, resource_group=None):
        out = []
        for e in self.events:
            if e["subscriptionId"] != subscription_id:
                continue
            if resource_group and e["resourceGroupName"].lower() != resource_group.lower():
                continue
            ts = datetime.strptime(e["eventTimestamp"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            if start <= ts <= end:
                out.append(e)
        return out

    def get_resource_changes(self, subscription_ids, start, end, resource_group=None):
        wanted = set(subscription_ids)
        out = []
        for c in self.changes:
            if c["_sub"] not in wanted:
                continue
            if resource_group and c["_rg"].lower() != resource_group.lower():
                continue
            ts = datetime.strptime(c["changeTime"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            if start <= ts <= end:
                out.append({k: v for k, v in c.items() if not k.startswith("_")})
        return out
