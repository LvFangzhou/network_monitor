"""
资源管理路由
"""
from copy import deepcopy
from datetime import datetime
import ipaddress
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.core import get_logger
from app.database import get_db
from app.models import Circuit, CircuitAudit, Customer, CustomerAudit, Datacenter, Device, IPAddressRecord, User, Vendor
from app.routers.auth import get_current_active_user
from app.utils import influx_client
from app.schemas import (
    CustomerCreate, CustomerUpdate,
    CircuitCreate, CircuitUpdate,
    IPAddressRecordCreate, IPAddressRecordUpdate,
    VendorCreate, VendorUpdate,
)

logger = get_logger(__name__)
router = APIRouter()


def escape_flux_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def parse_flux_duration_seconds(value: str, default_seconds: int = 3600) -> int:
    match = re.fullmatch(r"-?(\d+)(s|m|h|d|w)", str(value or "").strip())
    if not match:
        return default_seconds
    amount = int(match.group(1))
    unit = match.group(2)
    return max(amount * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit], 1)


def normalize_flux_range(value: str, default: str = "-1h") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"-\d+(s|m|h|d|w)", text) else default


def normalize_flux_interval(value: str, default: str = "1m") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"\d+(s|m|h|d|w)", text) else default


def parse_network_entries(raw_value: Optional[str]) -> list[dict]:
    if not raw_value:
        return []
    entries: list[dict] = []
    for item in str(raw_value).replace("，", ",").replace("；", ",").replace("\n", ",").split(","):
        text = item.strip()
        if not text:
            continue
        if "/" in text:
            prefix, mask = text.split("/", 1)
            entries.append({
                "prefix": prefix.strip(),
                "mask": mask.strip(),
                "cidr": f"{prefix.strip()}/{mask.strip()}",
            })
        else:
            entries.append({
                "prefix": text,
                "mask": "",
                "cidr": text,
            })
    return entries


def normalize_customer_site_entries(raw_sites: Optional[list], db: Session) -> list[dict]:
    datacenters = {item.id: item for item in db.query(Datacenter).all()}
    normalized_sites: list[dict] = []
    for site in raw_sites or []:
        if not isinstance(site, dict):
            continue
        datacenter_id = site.get("datacenter_id")
        datacenter = datacenters.get(datacenter_id) if datacenter_id else None
        private_entries = site.get("private_network_entries")
        if private_entries is None:
            private_entries = parse_network_entries(site.get("private_networks"))
        public_entries = site.get("public_address_entries")
        if public_entries is None:
            public_entries = parse_network_entries(site.get("public_addresses"))
        normalized_sites.append({
            "datacenter_id": datacenter_id,
            "datacenter_name": datacenter.name if datacenter else site.get("datacenter_name"),
            "datacenter_code": datacenter.code if datacenter else site.get("datacenter_code"),
            "private_network_entries": private_entries or [],
            "public_address_entries": public_entries or [],
            "bandwidth_description": site.get("bandwidth_description") or "",
            "description": site.get("description") or "",
        })
    return normalized_sites


def aggregate_customer_site_values(customer_sites: list[dict]) -> dict:
    private_networks: list[str] = []
    public_addresses: list[str] = []
    bandwidth_items: list[str] = []
    for site in customer_sites:
        datacenter_name = site.get("datacenter_name") or "未命名机房"
        for entry in site.get("private_network_entries") or []:
            cidr = entry.get("cidr") or (
                f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
            )
            if cidr:
                private_networks.append(f"{datacenter_name}:{cidr}")
        for entry in site.get("public_address_entries") or []:
            cidr = entry.get("cidr") or (
                f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
            )
            if cidr:
                public_addresses.append(f"{datacenter_name}:{cidr}")
        if site.get("bandwidth_description"):
            bandwidth_items.append(f"{datacenter_name}:{site['bandwidth_description']}")
    return {
        "private_networks": "；".join(private_networks) if private_networks else None,
        "public_addresses": "；".join(public_addresses) if public_addresses else None,
        "bandwidth_description": "；".join(bandwidth_items) if bandwidth_items else None,
    }


def build_public_segment_refs(db: Session) -> list[dict]:
    circuits = (
        db.query(Circuit)
        .options(joinedload(Circuit.vendor_ref))
        .filter(Circuit.line_type == "internet")
        .all()
    )
    segment_refs: list[dict] = []
    for circuit in circuits:
        provider_name = None
        if circuit.vendor_ref and circuit.vendor_ref.name:
            provider_name = circuit.vendor_ref.name
        elif circuit.operator_name:
            provider_name = circuit.operator_name
        else:
            provider_name = "未知运营商"
        for segment in circuit.address_segments or []:
            cidr = str(segment.get("cidr") or "").strip()
            if not cidr:
                continue
            if not segment.get("is_public"):
                continue
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            segment_refs.append({
                "network": network,
                "provider_name": provider_name,
                "circuit_name": circuit.name,
            })
    segment_refs.sort(key=lambda item: item["network"].prefixlen, reverse=True)
    return segment_refs


def enrich_public_address_entries(entries: list[dict], public_segment_refs: Optional[list[dict]] = None) -> list[dict]:
    if not entries:
        return []
    refs = public_segment_refs or []
    enriched_entries: list[dict] = []
    for entry in entries:
        cidr = str(entry.get("cidr") or "").strip()
        provider_name = None
        matched_circuit_name = None
        try:
            if "/" in cidr:
                target = ipaddress.ip_network(cidr, strict=False)
                for ref in refs:
                    if target.subnet_of(ref["network"]) or ref["network"].subnet_of(target):
                        provider_name = ref["provider_name"]
                        matched_circuit_name = ref["circuit_name"]
                        break
            else:
                target_ip = ipaddress.ip_address(cidr)
                for ref in refs:
                    if target_ip in ref["network"]:
                        provider_name = ref["provider_name"]
                        matched_circuit_name = ref["circuit_name"]
                        break
        except ValueError:
            pass
        enriched_entries.append({
            **entry,
            "provider_name": provider_name,
            "matched_circuit_name": matched_circuit_name,
        })
    return enriched_entries


def normalize_search_text(search: Optional[str]) -> str:
    return str(search or "").strip()


def parse_search_ip(search: Optional[str]):
    text = normalize_search_text(search)
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def text_contains(value: Optional[object], search: str) -> bool:
    if value is None:
        return False
    return search.lower() in str(value).lower()


def ip_matches_value(search_ip, value: Optional[object]) -> bool:
    if not search_ip or value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        if "/" in text:
            return search_ip in ipaddress.ip_network(text, strict=False)
        return search_ip == ipaddress.ip_address(text)
    except ValueError:
        return False


def entries_match_search(entries: Optional[list], search: str, search_ip) -> bool:
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        values = [
            entry.get("cidr"),
            entry.get("prefix"),
            entry.get("mask"),
            entry.get("provider_name"),
            entry.get("matched_circuit_name"),
        ]
        if any(text_contains(value, search) for value in values if value is not None):
            return True
        cidr = entry.get("cidr") or (
            f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
        )
        if ip_matches_value(search_ip, cidr):
            return True
    return False


def customer_matches_search(customer: Customer, search: str, search_ip, public_segment_refs: list[dict]) -> bool:
    if not search:
        return True
    fields = [
        customer.name,
        customer.legal_name,
        customer.private_networks,
        customer.public_addresses,
        customer.bandwidth_description,
        customer.dedicated_lines,
        customer.service_manager_name,
        customer.service_manager_contact,
        customer.sales_name,
        customer.sales_contact,
        customer.contact_info,
        customer.contact_group,
        customer.description,
    ]
    if any(text_contains(value, search) for value in fields):
        return True
    for site in customer.customer_sites or []:
        if not isinstance(site, dict):
            continue
        site_fields = [
            site.get("datacenter_name"),
            site.get("datacenter_code"),
            site.get("bandwidth_description"),
            site.get("description"),
        ]
        if any(text_contains(value, search) for value in site_fields):
            return True
        if entries_match_search(site.get("private_network_entries"), search, search_ip):
            return True
        enriched_public = enrich_public_address_entries(site.get("public_address_entries") or [], public_segment_refs)
        if entries_match_search(enriched_public, search, search_ip):
            return True
    return False


def circuit_matches_search(circuit: Circuit, search: str, search_ip) -> bool:
    if not search:
        return True
    fields = [
        circuit.name,
        circuit.operator_name,
        circuit.line_type,
        circuit.access_mode,
        circuit.status,
        circuit.ip_address,
        circuit.bandwidth_mbps,
        circuit.primary_port_rate,
        circuit.secondary_port_rate,
        circuit.dual_link_mode,
        circuit.interconnect_type,
        circuit.routing_mode,
        circuit.description,
        circuit.datacenter_ref.name if circuit.datacenter_ref else None,
        circuit.vendor_ref.name if circuit.vendor_ref else None,
        circuit.customer_ref.name if circuit.customer_ref else None,
        circuit.primary_device_ref.name if circuit.primary_device_ref else None,
        circuit.primary_device_ref.ip_address if circuit.primary_device_ref else None,
        circuit.primary_port_name,
        circuit.secondary_device_ref.name if circuit.secondary_device_ref else None,
        circuit.secondary_device_ref.ip_address if circuit.secondary_device_ref else None,
        circuit.secondary_port_name,
        circuit.aggregation_monitor_device_ref.name if circuit.aggregation_monitor_device_ref else None,
        circuit.aggregation_monitor_device_ref.ip_address if circuit.aggregation_monitor_device_ref else None,
        circuit.aggregation_interface_name,
        circuit.primary_interconnect_ip,
        circuit.secondary_interconnect_ip,
        circuit.local_interconnect_address,
        circuit.remote_interconnect_address,
        circuit.routed_cidrs,
        circuit.local_routed_cidrs,
        circuit.remote_routed_cidrs,
    ]
    if any(text_contains(value, search) for value in fields):
        return True
    ip_like_values = [
        circuit.ip_address,
        circuit.primary_device_ref.ip_address if circuit.primary_device_ref else None,
        circuit.secondary_device_ref.ip_address if circuit.secondary_device_ref else None,
        circuit.aggregation_monitor_device_ref.ip_address if circuit.aggregation_monitor_device_ref else None,
        circuit.primary_interconnect_ip,
        circuit.secondary_interconnect_ip,
        circuit.local_interconnect_address,
        circuit.remote_interconnect_address,
    ]
    if any(ip_matches_value(search_ip, value) for value in ip_like_values):
        return True
    if entries_match_search(circuit.address_segments or [], search, search_ip):
        return True
    if entries_match_search(circuit.routed_networks or [], search, search_ip):
        return True
    if entries_match_search(circuit.local_routed_networks or [], search, search_ip):
        return True
    if entries_match_search(circuit.remote_routed_networks or [], search, search_ip):
        return True
    return False


def ip_record_matches_search(record: IPAddressRecord, search: str, search_ip) -> bool:
    if not search:
        return True
    fields = [
        record.ip_address,
        record.prefix_length,
        record.status,
        record.usage_type,
        record.description,
        record.datacenter_ref.name if record.datacenter_ref else None,
        record.circuit_ref.name if record.circuit_ref else None,
    ]
    if any(text_contains(value, search) for value in fields):
        return True
    return ip_matches_value(search_ip, f"{record.ip_address}/{record.prefix_length}")


def serialize_customer(
    customer: Customer,
    circuits_by_customer: Optional[dict[int, list[dict]]] = None,
    public_segment_refs: Optional[list[dict]] = None,
) -> dict:
    data = customer.to_dict()
    customer_sites = customer.customer_sites or []
    data["customer_sites"] = [
        {
            **site,
            "public_address_entries": enrich_public_address_entries(site.get("public_address_entries") or [], public_segment_refs),
        }
        for site in customer_sites
    ]
    flattened_private_entries = []
    flattened_public_entries = []
    for site in data["customer_sites"]:
        site_name = site.get("datacenter_name") or "未命名机房"
        for entry in site.get("private_network_entries") or []:
            flattened_private_entries.append({
                **entry,
                "cidr": f"{site_name}:{entry.get('cidr') or entry.get('prefix')}",
            })
        for entry in site.get("public_address_entries") or []:
            flattened_public_entries.append({
                **entry,
                "cidr": f"{site_name}:{entry.get('cidr') or entry.get('prefix')}",
            })
    data["private_network_entries"] = flattened_private_entries
    data["public_address_entries"] = flattened_public_entries
    data["customer_resources"] = (circuits_by_customer or {}).get(customer.id, [])
    return data


def get_customer_public_cidrs(customer: Customer) -> list[str]:
    cidrs: list[str] = []
    for site in customer.customer_sites or []:
        if not isinstance(site, dict):
            continue
        for entry in site.get("public_address_entries") or []:
            if not isinstance(entry, dict):
                continue
            cidr = entry.get("cidr") or (
                f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
            )
            if cidr:
                try:
                    network_text = str(cidr).strip() if "/" in str(cidr) else f"{str(cidr).strip()}/32"
                    cidrs.append(str(ipaddress.ip_network(network_text, strict=False)))
                except ValueError:
                    continue
    return list(dict.fromkeys(cidrs))


AUDITED_CUSTOMER_FIELDS = [
    "name",
    "legal_name",
    "customer_sites",
    "private_networks",
    "public_addresses",
    "bandwidth_description",
    "dedicated_lines",
    "service_manager_name",
    "service_manager_contact",
    "sales_name",
    "sales_contact",
    "contact_info",
    "contact_group",
    "description",
    "is_active",
]


def snapshot_customer(customer: Customer) -> dict:
    return {
        "name": customer.name,
        "legal_name": customer.legal_name,
        "customer_sites": deepcopy(customer.customer_sites or []),
        "private_networks": customer.private_networks,
        "public_addresses": customer.public_addresses,
        "bandwidth_description": customer.bandwidth_description,
        "dedicated_lines": customer.dedicated_lines,
        "service_manager_name": customer.service_manager_name,
        "service_manager_contact": customer.service_manager_contact,
        "sales_name": customer.sales_name,
        "sales_contact": customer.sales_contact,
        "contact_info": customer.contact_info,
        "contact_group": customer.contact_group,
        "description": customer.description,
        "is_active": customer.is_active,
    }


def build_customer_change_summary(before_data: Optional[dict], after_data: Optional[dict]) -> list[dict]:
    before_data = before_data or {}
    after_data = after_data or {}
    summary = []
    for field in AUDITED_CUSTOMER_FIELDS:
        before_value = before_data.get(field)
        after_value = after_data.get(field)
        if before_value != after_value:
            summary.append({
                "field": field,
                "before": before_value,
                "after": after_value,
            })
    return summary


def create_customer_audit(
    db: Session,
    *,
    customer_id: int,
    action: str,
    actor: Optional[User],
    before_data: Optional[dict],
    after_data: Optional[dict],
) -> None:
    audit = CustomerAudit(
        customer_id=customer_id,
        customer_name=(after_data or before_data or {}).get("name") or f"客户#{customer_id}",
        action=action,
        actor_user_id=actor.id if actor else None,
        actor_username=actor.username if actor else "system",
        change_summary=build_customer_change_summary(before_data, after_data),
        before_data=before_data,
        after_data=after_data,
    )
    db.add(audit)


AUDITED_CIRCUIT_FIELDS = [
    "name",
    "operator_name",
    "line_type",
    "access_mode",
    "ip_address",
    "bandwidth_mbps",
    "physical_port_rate_gbps",
    "primary_port_rate",
    "secondary_port_rate",
    "dual_link_mode",
    "is_redundant",
    "redundancy_note",
    "status",
    "datacenter_id",
    "vendor_id",
    "customer_id",
    "primary_device_id",
    "primary_port_name",
    "aggregation_monitor_device_id",
    "aggregation_interface_name",
    "primary_local_interconnect_ip",
    "primary_remote_interconnect_ip",
    "primary_interconnect_type",
    "primary_routing_mode",
    "primary_bfd_mode",
    "primary_interconnect_ip",
    "primary_vlan_id",
    "secondary_device_id",
    "secondary_port_name",
    "secondary_local_interconnect_ip",
    "secondary_remote_interconnect_ip",
    "secondary_interconnect_type",
    "secondary_routing_mode",
    "secondary_bfd_mode",
    "secondary_interconnect_ip",
    "secondary_vlan_id",
    "interconnect_address",
    "local_interconnect_address",
    "remote_interconnect_address",
    "interconnect_type",
    "routing_mode",
    "bfd_mode",
    "bfd_enabled",
    "routed_cidrs",
    "routed_networks",
    "local_routed_cidrs",
    "local_routed_networks",
    "remote_routed_cidrs",
    "remote_routed_networks",
    "address_segments",
    "description",
]


def snapshot_circuit(circuit: Circuit) -> dict:
    return {
        "name": circuit.name,
        "operator_name": circuit.operator_name,
        "line_type": circuit.line_type,
        "access_mode": circuit.access_mode,
        "ip_address": circuit.ip_address,
        "bandwidth_mbps": circuit.bandwidth_mbps,
        "physical_port_rate_gbps": circuit.physical_port_rate_gbps,
        "primary_port_rate": circuit.primary_port_rate,
        "secondary_port_rate": circuit.secondary_port_rate,
        "dual_link_mode": circuit.dual_link_mode,
        "is_redundant": circuit.is_redundant,
        "redundancy_note": circuit.redundancy_note,
        "status": circuit.status,
        "datacenter_id": circuit.datacenter_id,
        "vendor_id": circuit.vendor_id,
        "customer_id": circuit.customer_id,
        "primary_device_id": circuit.primary_device_id,
        "primary_port_name": circuit.primary_port_name,
        "aggregation_monitor_device_id": circuit.aggregation_monitor_device_id,
        "aggregation_interface_name": circuit.aggregation_interface_name,
        "primary_local_interconnect_ip": circuit.primary_local_interconnect_ip,
        "primary_remote_interconnect_ip": circuit.primary_remote_interconnect_ip,
        "primary_interconnect_type": circuit.primary_interconnect_type or circuit.interconnect_type,
        "primary_routing_mode": circuit.primary_routing_mode or circuit.routing_mode,
        "primary_bfd_mode": circuit.effective_primary_bfd_mode(),
        "primary_interconnect_ip": circuit.primary_interconnect_ip,
        "primary_vlan_id": circuit.primary_vlan_id,
        "secondary_device_id": circuit.secondary_device_id,
        "secondary_port_name": circuit.secondary_port_name,
        "secondary_local_interconnect_ip": circuit.secondary_local_interconnect_ip,
        "secondary_remote_interconnect_ip": circuit.secondary_remote_interconnect_ip,
        "secondary_interconnect_type": circuit.secondary_interconnect_type or circuit.interconnect_type,
        "secondary_routing_mode": circuit.secondary_routing_mode or circuit.routing_mode,
        "secondary_bfd_mode": circuit.effective_secondary_bfd_mode(),
        "secondary_interconnect_ip": circuit.secondary_interconnect_ip,
        "secondary_vlan_id": circuit.secondary_vlan_id,
        "interconnect_address": circuit.interconnect_address,
        "local_interconnect_address": circuit.local_interconnect_address,
        "remote_interconnect_address": circuit.remote_interconnect_address,
        "interconnect_type": circuit.interconnect_type,
        "routing_mode": circuit.routing_mode,
        "bfd_mode": circuit.effective_bfd_mode(),
        "bfd_enabled": circuit.bfd_enabled,
        "routed_cidrs": circuit.routed_cidrs,
        "routed_networks": deepcopy(circuit.routed_networks or []),
        "local_routed_cidrs": circuit.local_routed_cidrs,
        "local_routed_networks": deepcopy(circuit.local_routed_networks or []),
        "remote_routed_cidrs": circuit.remote_routed_cidrs,
        "remote_routed_networks": deepcopy(circuit.remote_routed_networks or []),
        "address_segments": deepcopy(circuit.address_segments or []),
        "description": circuit.description,
    }


def build_change_summary(before_data: Optional[dict], after_data: Optional[dict]) -> list[dict]:
    before_data = before_data or {}
    after_data = after_data or {}
    field_names = set(before_data.keys()) | set(after_data.keys()) | set(AUDITED_CIRCUIT_FIELDS)
    summary = []
    for field in AUDITED_CIRCUIT_FIELDS:
        if field not in field_names:
            continue
        before_value = before_data.get(field)
        after_value = after_data.get(field)
        if before_value != after_value:
            summary.append({
                "field": field,
                "before": before_value,
                "after": after_value,
            })
    return summary


def create_circuit_audit(
    db: Session,
    *,
    circuit_id: int,
    action: str,
    actor: User,
    before_data: Optional[dict],
    after_data: Optional[dict],
) -> None:
    audit = CircuitAudit(
        circuit_id=circuit_id,
        circuit_name=(after_data or before_data or {}).get("name") or f"线路#{circuit_id}",
        action=action,
        actor_user_id=actor.id if actor else None,
        actor_username=actor.username if actor else "system",
        change_summary=build_change_summary(before_data, after_data),
        before_data=before_data,
        after_data=after_data,
    )
    db.add(audit)


@router.get("/customers", response_model=dict)
async def list_customers(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
):
    public_segment_refs = build_public_segment_refs(db)
    search_text = normalize_search_text(search)
    search_ip = parse_search_ip(search)
    all_items = db.query(Customer).order_by(Customer.id.desc()).all()
    filtered_items = [item for item in all_items if customer_matches_search(item, search_text, search_ip, public_segment_refs)]
    total = len(filtered_items)
    items = filtered_items[skip:skip + limit]
    customer_ids = [item.id for item in items]
    circuits_by_customer: dict[int, list[dict]] = {}
    if customer_ids:
        customer_circuits = (
            db.query(Circuit)
            .filter(Circuit.customer_id.in_(customer_ids), Circuit.line_type == "private_line")
            .order_by(Circuit.id.desc())
            .all()
        )
        for circuit in customer_circuits:
            circuits_by_customer.setdefault(circuit.customer_id, []).append({
                "id": circuit.id,
                "name": circuit.name,
                "line_type": circuit.line_type,
                "status": circuit.status,
                "datacenter_name": circuit.datacenter_ref.name if circuit.datacenter_ref else None,
            })
    return {
        "total": total,
        "items": [serialize_customer(item, circuits_by_customer, public_segment_refs) for item in items],
    }


@router.post("/customers", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    existing = db.query(Customer).filter(Customer.name == customer.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="客户名称已存在")
    payload = customer.model_dump()
    customer_sites = normalize_customer_site_entries(payload.get("customer_sites"), db)
    aggregates = aggregate_customer_site_values(customer_sites)
    payload["customer_sites"] = customer_sites
    payload["private_networks"] = aggregates["private_networks"]
    payload["public_addresses"] = aggregates["public_addresses"]
    payload["bandwidth_description"] = aggregates["bandwidth_description"] or payload.get("bandwidth_description")
    db_customer = Customer(**payload)
    db.add(db_customer)
    db.flush()
    create_customer_audit(
        db,
        customer_id=db_customer.id,
        action="create",
        actor=current_user,
        before_data=None,
        after_data=snapshot_customer(db_customer),
    )
    db.commit()
    db.refresh(db_customer)
    return serialize_customer(db_customer, public_segment_refs=build_public_segment_refs(db))


@router.put("/customers/{customer_id}", response_model=dict)
async def update_customer(
    customer_id: int,
    customer: CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    db_customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    before_data = snapshot_customer(db_customer)
    if customer.name and customer.name != db_customer.name:
        existing = db.query(Customer).filter(Customer.name == customer.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="客户名称已存在")
    update_data = customer.model_dump(exclude_unset=True)
    if "customer_sites" in update_data:
        customer_sites = normalize_customer_site_entries(update_data.get("customer_sites"), db)
        aggregates = aggregate_customer_site_values(customer_sites)
        update_data["customer_sites"] = customer_sites
        update_data["private_networks"] = aggregates["private_networks"]
        update_data["public_addresses"] = aggregates["public_addresses"]
        update_data["bandwidth_description"] = aggregates["bandwidth_description"] or update_data.get("bandwidth_description")
    for key, value in update_data.items():
        setattr(db_customer, key, value)
    db_customer.updated_at = datetime.now()
    create_customer_audit(
        db,
        customer_id=db_customer.id,
        action="update",
        actor=current_user,
        before_data=before_data,
        after_data=snapshot_customer(db_customer),
    )
    db.commit()
    db.refresh(db_customer)
    customer_circuits = (
        db.query(Circuit)
        .filter(Circuit.customer_id == db_customer.id, Circuit.line_type == "private_line")
        .order_by(Circuit.id.desc())
        .all()
    )
    circuits_by_customer = {
        db_customer.id: [
            {
                "id": circuit.id,
                "name": circuit.name,
                "line_type": circuit.line_type,
                "status": circuit.status,
                "datacenter_name": circuit.datacenter_ref.name if circuit.datacenter_ref else None,
            }
            for circuit in customer_circuits
        ]
    }
    return serialize_customer(db_customer, circuits_by_customer, build_public_segment_refs(db))


@router.delete("/customers/{customer_id}")
async def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    db_customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    before_data = snapshot_customer(db_customer)
    create_customer_audit(
        db,
        customer_id=db_customer.id,
        action="delete",
        actor=current_user,
        before_data=before_data,
        after_data=None,
    )
    db.delete(db_customer)
    db.commit()
    return {"message": "客户已删除"}


@router.get("/customers/{customer_id}/flow-traffic", response_model=dict)
async def get_customer_flow_traffic(
    customer_id: int,
    db: Session = Depends(get_db),
    range: str = Query("-1h", description="历史时间范围，例如 -30m/-1h/-6h/-24h"),
    interval: str = Query("1m", description="聚合间隔，例如 30s/1m/5m"),
    cidr: Optional[str] = Query(None, description="客户公网 IP/CIDR，留空表示聚合该客户所有公网地址"),
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")

    allowed_cidrs = get_customer_public_cidrs(customer)
    normalized_cidr = None
    if cidr:
        try:
            normalized_cidr = str(ipaddress.ip_network(cidr if "/" in cidr else f"{cidr}/32", strict=False))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="公网 IP/CIDR 格式不正确") from exc
        if normalized_cidr not in allowed_cidrs:
            raise HTTPException(status_code=400, detail="该公网 IP/CIDR 不属于当前客户")

    range_value = normalize_flux_range(range)
    interval_value = normalize_flux_interval(interval)
    interval_seconds = parse_flux_duration_seconds(interval_value, 60)
    cidr_filter = f'|> filter(fn: (r) => r.cidr == "{escape_flux_string(normalized_cidr)}")' if normalized_cidr else ""

    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "customer_ip_traffic")
      |> filter(fn: (r) => r.customer_id == "{customer_id}")
      {cidr_filter}
      |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
      |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
      |> group(columns: ["_time", "_field"])
      |> sum()
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"])
    '''
    rows = influx_client.query(flux)
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "cidr": normalized_cidr,
        "available_cidrs": allowed_cidrs,
        "range": range_value,
        "interval": interval_value,
        "interval_seconds": interval_seconds,
        "data": rows,
        "total": len(rows),
    }


@router.get("/customers/{customer_id}/audits", response_model=dict)
async def list_customer_audits(
    customer_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    items = (
        db.query(CustomerAudit)
        .filter(CustomerAudit.customer_id == customer_id)
        .order_by(CustomerAudit.created_at.desc(), CustomerAudit.id.desc())
        .limit(limit)
        .all()
    )
    return {"total": len(items), "items": [item.to_dict() for item in items]}


@router.get("/vendors", response_model=dict)
async def list_vendors(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    search: Optional[str] = None,
):
    query = db.query(Vendor)
    if search:
        query = query.filter(Vendor.name.ilike(f"%{search}%"))
    total = query.count()
    items = query.order_by(Vendor.name.asc()).offset(skip).limit(limit).all()
    return {"total": total, "items": [item.to_dict() for item in items]}


@router.post("/vendors", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_vendor(vendor: VendorCreate, db: Session = Depends(get_db)):
    existing = db.query(Vendor).filter(Vendor.name == vendor.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="供应商名称已存在")
    db_vendor = Vendor(**vendor.model_dump())
    db.add(db_vendor)
    db.commit()
    db.refresh(db_vendor)
    logger.info("供应商创建成功", vendor_id=db_vendor.id, name=db_vendor.name)
    return db_vendor.to_dict()


@router.put("/vendors/{vendor_id}", response_model=dict)
async def update_vendor(vendor_id: int, vendor: VendorUpdate, db: Session = Depends(get_db)):
    db_vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not db_vendor:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if vendor.name and vendor.name != db_vendor.name:
        existing = db.query(Vendor).filter(Vendor.name == vendor.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="供应商名称已存在")
    for key, value in vendor.model_dump(exclude_unset=True).items():
        setattr(db_vendor, key, value)
    db_vendor.updated_at = datetime.now()
    db.commit()
    db.refresh(db_vendor)
    return db_vendor.to_dict()


@router.delete("/vendors/{vendor_id}")
async def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    db_vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not db_vendor:
        raise HTTPException(status_code=404, detail="供应商不存在")
    circuit_count = db.query(Circuit).filter(Circuit.vendor_id == vendor_id).count()
    if circuit_count > 0:
        raise HTTPException(status_code=400, detail=f"该供应商下有{circuit_count}条线路，无法删除")
    db.delete(db_vendor)
    db.commit()
    return {"message": "供应商已删除"}


@router.get("/circuits", response_model=dict)
async def list_circuits(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    datacenter_id: Optional[int] = None,
    line_type: Optional[str] = None,
    access_mode: Optional[str] = None,
    customer_id: Optional[int] = None,
    vendor_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    query = db.query(Circuit).options(
        joinedload(Circuit.datacenter_ref),
        joinedload(Circuit.vendor_ref),
        joinedload(Circuit.customer_ref),
        joinedload(Circuit.primary_device_ref),
        joinedload(Circuit.secondary_device_ref),
    )
    if datacenter_id:
        query = query.filter(Circuit.datacenter_id == datacenter_id)
    if line_type:
        query = query.filter(Circuit.line_type == line_type)
    if access_mode:
        query = query.filter(Circuit.access_mode == access_mode)
    if customer_id:
        query = query.filter(Circuit.customer_id == customer_id)
    if vendor_id:
        query = query.filter(Circuit.vendor_id == vendor_id)
    if status:
        query = query.filter(Circuit.status == status)
    search_text = normalize_search_text(search)
    search_ip = parse_search_ip(search)
    filtered_items = [item for item in query.order_by(Circuit.id.desc()).all() if circuit_matches_search(item, search_text, search_ip)]
    total = len(filtered_items)
    items = filtered_items[skip:skip + limit]
    return {"total": total, "items": [item.to_dict() for item in items]}


@router.post("/circuits", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_circuit(
    circuit: CircuitCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not circuit.primary_device_id or not circuit.primary_port_name:
        raise HTTPException(status_code=400, detail="请填写主接入交换机和主接入端口")
    if not circuit.primary_port_rate:
        raise HTTPException(status_code=400, detail="请选择主端口物理速率")
    if circuit.datacenter_id:
        datacenter = db.query(Datacenter).filter(Datacenter.id == circuit.datacenter_id).first()
        if not datacenter:
            raise HTTPException(status_code=400, detail="所属机房不存在")
    if circuit.vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == circuit.vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=400, detail="供应商不存在")
    if circuit.customer_id:
        customer = db.query(Customer).filter(Customer.id == circuit.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail="客户不存在")
    if circuit.primary_device_id:
        primary_device = db.query(Device).filter(Device.id == circuit.primary_device_id).first()
        if not primary_device:
            raise HTTPException(status_code=400, detail="主接入交换机不存在")
    if circuit.secondary_device_id:
        secondary_device = db.query(Device).filter(Device.id == circuit.secondary_device_id).first()
        if not secondary_device:
            raise HTTPException(status_code=400, detail="备接入交换机不存在")
    if circuit.access_mode == "dual":
        if not circuit.secondary_device_id or not circuit.secondary_port_name:
            raise HTTPException(status_code=400, detail="双线接入时请填写备接入交换机和端口")
        if not circuit.secondary_port_rate:
            raise HTTPException(status_code=400, detail="双线接入时请选择备端口物理速率")
        if not circuit.dual_link_mode:
            raise HTTPException(status_code=400, detail="双线接入时请选择接入策略")
    if circuit.aggregation_monitor_device_id:
        aggregation_device = db.query(Device).filter(Device.id == circuit.aggregation_monitor_device_id).first()
        if not aggregation_device:
            raise HTTPException(status_code=400, detail="逻辑聚合接口设备不存在")
    is_dual_lacp_private_line = (
        circuit.line_type == "private_line"
        and circuit.access_mode == "dual"
        and circuit.dual_link_mode == "lacp"
    )
    if circuit.line_type == "private_line":
        primary_interconnect_type = circuit.primary_interconnect_type or circuit.interconnect_type
        if not circuit.primary_local_interconnect_ip:
            raise HTTPException(status_code=400, detail="请填写主本端地址")
        if not circuit.primary_remote_interconnect_ip:
            raise HTTPException(status_code=400, detail="请填写主对端地址")
        if is_dual_lacp_private_line:
            if not circuit.aggregation_monitor_device_id or not circuit.aggregation_interface_name:
                raise HTTPException(status_code=400, detail="LACP 双线专线时请选择逻辑聚合接口")
        if circuit.access_mode == "dual" and not is_dual_lacp_private_line and not circuit.secondary_local_interconnect_ip:
            raise HTTPException(status_code=400, detail="双线接入时请填写备本端地址")
        if circuit.access_mode == "dual" and not is_dual_lacp_private_line and not circuit.secondary_remote_interconnect_ip:
            raise HTTPException(status_code=400, detail="双线接入时请填写备对端地址")
        if primary_interconnect_type == "l2":
            if not circuit.primary_vlan_id:
                raise HTTPException(status_code=400, detail="二层互联时请填写主接入VLAN ID")
        secondary_interconnect_type = circuit.secondary_interconnect_type or circuit.interconnect_type
        if circuit.access_mode == "dual" and not is_dual_lacp_private_line and secondary_interconnect_type == "l2":
            if not circuit.secondary_vlan_id:
                raise HTTPException(status_code=400, detail="双线接入二层互联时请填写备接入VLAN ID")
    payload = circuit.model_dump()
    if payload.get("line_type") == "private_line" and not payload.get("operator_name"):
        payload["operator_name"] = "专线"
    if payload.get("line_type") == "private_line":
        payload["primary_interconnect_type"] = payload.get("primary_interconnect_type") or payload.get("interconnect_type")
        payload["interconnect_type"] = payload.get("primary_interconnect_type")
        is_dual_lacp_private_line = (
            payload.get("access_mode") == "dual"
            and payload.get("dual_link_mode") == "lacp"
        )
        if is_dual_lacp_private_line:
            payload["secondary_interconnect_type"] = None
            payload["secondary_local_interconnect_ip"] = None
            payload["secondary_remote_interconnect_ip"] = None
            payload["secondary_routing_mode"] = None
            payload["secondary_bfd_mode"] = "none"
            payload["secondary_interconnect_ip"] = None
            payload["secondary_vlan_id"] = None
        else:
            payload["aggregation_monitor_device_id"] = None
            payload["aggregation_interface_name"] = None
            payload["secondary_interconnect_type"] = payload.get("secondary_interconnect_type") or payload.get("interconnect_type")
        if payload.get("primary_interconnect_type") != "l2":
            payload["primary_vlan_id"] = None
        if payload.get("secondary_interconnect_type") != "l2":
            payload["secondary_vlan_id"] = None
        payload["primary_routing_mode"] = payload.get("primary_routing_mode") or payload.get("routing_mode")
        payload["secondary_routing_mode"] = None if is_dual_lacp_private_line else (payload.get("secondary_routing_mode") or payload.get("routing_mode"))
        payload["primary_bfd_mode"] = payload.get("primary_bfd_mode") or payload.get("bfd_mode") or ("bfd" if payload.get("bfd_enabled") else "none")
        payload["secondary_bfd_mode"] = "none" if is_dual_lacp_private_line else (payload.get("secondary_bfd_mode") or payload.get("bfd_mode") or ("bfd" if payload.get("bfd_enabled") else "none"))
        payload["routing_mode"] = payload.get("primary_routing_mode")
        payload["bfd_mode"] = payload.get("primary_bfd_mode") or "none"
        payload["bfd_enabled"] = payload["bfd_mode"] == "bfd"
        payload["primary_interconnect_ip"] = payload.get("primary_local_interconnect_ip")
        payload["secondary_interconnect_ip"] = None if is_dual_lacp_private_line else payload.get("secondary_local_interconnect_ip")
        payload["local_interconnect_address"] = payload.get("primary_local_interconnect_ip")
        payload["remote_interconnect_address"] = payload.get("primary_remote_interconnect_ip")
        payload["interconnect_address"] = [payload.get("primary_local_interconnect_ip"), payload.get("primary_remote_interconnect_ip")]
        payload["interconnect_address"] = " - ".join([item for item in payload["interconnect_address"] if item])
    db_circuit = Circuit(**payload)
    db.add(db_circuit)
    db.commit()
    db.refresh(db_circuit)
    db.refresh(db_circuit, attribute_names=["datacenter_ref", "vendor_ref", "customer_ref", "primary_device_ref", "secondary_device_ref", "aggregation_monitor_device_ref"])
    create_circuit_audit(
        db,
        circuit_id=db_circuit.id,
        action="create",
        actor=current_user,
        before_data=None,
        after_data=snapshot_circuit(db_circuit),
    )
    db.commit()
    return db_circuit.to_dict()


@router.put("/circuits/{circuit_id}", response_model=dict)
async def update_circuit(
    circuit_id: int,
    circuit: CircuitUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    db_circuit = db.query(Circuit).filter(Circuit.id == circuit_id).first()
    if not db_circuit:
        raise HTTPException(status_code=404, detail="线路不存在")
    before_data = snapshot_circuit(db_circuit)
    data = circuit.model_dump(exclude_unset=True)
    if "datacenter_id" in data and data["datacenter_id"]:
        datacenter = db.query(Datacenter).filter(Datacenter.id == data["datacenter_id"]).first()
        if not datacenter:
            raise HTTPException(status_code=400, detail="所属机房不存在")
    if "vendor_id" in data and data["vendor_id"]:
        vendor = db.query(Vendor).filter(Vendor.id == data["vendor_id"]).first()
        if not vendor:
            raise HTTPException(status_code=400, detail="供应商不存在")
    if "customer_id" in data and data["customer_id"]:
        customer = db.query(Customer).filter(Customer.id == data["customer_id"]).first()
        if not customer:
            raise HTTPException(status_code=400, detail="客户不存在")
    next_primary_device_id = data.get("primary_device_id", db_circuit.primary_device_id)
    next_primary_port_name = data.get("primary_port_name", db_circuit.primary_port_name)
    next_primary_port_rate = data.get("primary_port_rate", db_circuit.primary_port_rate)
    if not next_primary_device_id or not next_primary_port_name:
        raise HTTPException(status_code=400, detail="请填写主接入交换机和主接入端口")
    if not next_primary_port_rate:
        raise HTTPException(status_code=400, detail="请选择主端口物理速率")
    if "primary_device_id" in data and data["primary_device_id"]:
        primary_device = db.query(Device).filter(Device.id == data["primary_device_id"]).first()
        if not primary_device:
            raise HTTPException(status_code=400, detail="主接入交换机不存在")
    if "secondary_device_id" in data and data["secondary_device_id"]:
        secondary_device = db.query(Device).filter(Device.id == data["secondary_device_id"]).first()
        if not secondary_device:
            raise HTTPException(status_code=400, detail="备接入交换机不存在")
    next_aggregation_monitor_device_id = data.get("aggregation_monitor_device_id", db_circuit.aggregation_monitor_device_id)
    if next_aggregation_monitor_device_id:
        aggregation_device = db.query(Device).filter(Device.id == next_aggregation_monitor_device_id).first()
        if not aggregation_device:
            raise HTTPException(status_code=400, detail="逻辑聚合接口设备不存在")
    next_access_mode = data.get("access_mode", db_circuit.access_mode)
    next_secondary_device_id = data.get("secondary_device_id", db_circuit.secondary_device_id)
    next_secondary_port_name = data.get("secondary_port_name", db_circuit.secondary_port_name)
    next_secondary_port_rate = data.get("secondary_port_rate", db_circuit.secondary_port_rate)
    next_dual_link_mode = data.get("dual_link_mode", db_circuit.dual_link_mode)
    if next_access_mode == "dual":
        if not next_secondary_device_id or not next_secondary_port_name:
            raise HTTPException(status_code=400, detail="双线接入时请填写备接入交换机和端口")
        if not next_secondary_port_rate:
            raise HTTPException(status_code=400, detail="双线接入时请选择备端口物理速率")
        if not next_dual_link_mode:
            raise HTTPException(status_code=400, detail="双线接入时请选择接入策略")
    next_line_type = data.get("line_type", db_circuit.line_type)
    next_interconnect_type = data.get("interconnect_type", db_circuit.interconnect_type)
    next_primary_interconnect_type = data.get(
        "primary_interconnect_type",
        db_circuit.primary_interconnect_type or next_interconnect_type,
    )
    next_secondary_interconnect_type = data.get(
        "secondary_interconnect_type",
        db_circuit.secondary_interconnect_type or next_interconnect_type,
    )
    next_primary_local_interconnect_ip = data.get("primary_local_interconnect_ip", db_circuit.primary_local_interconnect_ip or db_circuit.primary_interconnect_ip)
    next_primary_remote_interconnect_ip = data.get("primary_remote_interconnect_ip", db_circuit.primary_remote_interconnect_ip or db_circuit.remote_interconnect_address)
    next_secondary_local_interconnect_ip = data.get("secondary_local_interconnect_ip", db_circuit.secondary_local_interconnect_ip or db_circuit.secondary_interconnect_ip)
    next_secondary_remote_interconnect_ip = data.get("secondary_remote_interconnect_ip", db_circuit.secondary_remote_interconnect_ip)
    next_primary_vlan_id = data.get("primary_vlan_id", db_circuit.primary_vlan_id)
    next_secondary_vlan_id = data.get("secondary_vlan_id", db_circuit.secondary_vlan_id)
    next_aggregation_interface_name = data.get("aggregation_interface_name", db_circuit.aggregation_interface_name)
    is_dual_lacp_private_line = (
        next_line_type == "private_line"
        and next_access_mode == "dual"
        and next_dual_link_mode == "lacp"
    )
    if next_line_type == "private_line":
        if not next_primary_local_interconnect_ip:
            raise HTTPException(status_code=400, detail="请填写主本端地址")
        if not next_primary_remote_interconnect_ip:
            raise HTTPException(status_code=400, detail="请填写主对端地址")
        if is_dual_lacp_private_line:
            if not next_aggregation_monitor_device_id or not next_aggregation_interface_name:
                raise HTTPException(status_code=400, detail="LACP 双线专线时请选择逻辑聚合接口")
        if next_access_mode == "dual" and not is_dual_lacp_private_line and not next_secondary_local_interconnect_ip:
            raise HTTPException(status_code=400, detail="双线接入时请填写备本端地址")
        if next_access_mode == "dual" and not is_dual_lacp_private_line and not next_secondary_remote_interconnect_ip:
            raise HTTPException(status_code=400, detail="双线接入时请填写备对端地址")
        if next_primary_interconnect_type == "l2":
            if not next_primary_vlan_id:
                raise HTTPException(status_code=400, detail="二层互联时请填写主接入VLAN ID")
        if next_access_mode == "dual" and not is_dual_lacp_private_line and next_secondary_interconnect_type == "l2":
            if not next_secondary_vlan_id:
                raise HTTPException(status_code=400, detail="双线接入二层互联时请填写备接入VLAN ID")
    if data.get("line_type", db_circuit.line_type) == "private_line" and not data.get("operator_name", db_circuit.operator_name):
        data["operator_name"] = "专线"
    if data.get("line_type", db_circuit.line_type) == "private_line":
        data["primary_interconnect_type"] = data.get("primary_interconnect_type", db_circuit.primary_interconnect_type or next_interconnect_type)
        data["interconnect_type"] = data.get("primary_interconnect_type")
        if is_dual_lacp_private_line:
            data["secondary_interconnect_type"] = None
            data["secondary_local_interconnect_ip"] = None
            data["secondary_remote_interconnect_ip"] = None
            data["secondary_routing_mode"] = None
            data["secondary_bfd_mode"] = "none"
            data["secondary_interconnect_ip"] = None
            data["secondary_vlan_id"] = None
        else:
            data["aggregation_monitor_device_id"] = None
            data["aggregation_interface_name"] = None
            data["secondary_interconnect_type"] = data.get("secondary_interconnect_type", db_circuit.secondary_interconnect_type or next_interconnect_type)
        if data.get("primary_interconnect_type") != "l2":
            data["primary_vlan_id"] = None
        if data.get("secondary_interconnect_type") != "l2":
            data["secondary_vlan_id"] = None
        fallback_routing_mode = data.get("routing_mode", db_circuit.routing_mode)
        fallback_bfd_mode = data.get("bfd_mode", db_circuit.effective_bfd_mode())
        data["primary_routing_mode"] = data.get("primary_routing_mode", db_circuit.primary_routing_mode or fallback_routing_mode)
        data["secondary_routing_mode"] = None if is_dual_lacp_private_line else data.get("secondary_routing_mode", db_circuit.secondary_routing_mode or fallback_routing_mode)
        data["primary_bfd_mode"] = data.get("primary_bfd_mode", db_circuit.effective_primary_bfd_mode() or fallback_bfd_mode or "none")
        data["secondary_bfd_mode"] = "none" if is_dual_lacp_private_line else data.get("secondary_bfd_mode", db_circuit.effective_secondary_bfd_mode() or fallback_bfd_mode or "none")
        data["routing_mode"] = data.get("primary_routing_mode")
        data["bfd_mode"] = data.get("primary_bfd_mode") or "none"
        data["bfd_enabled"] = data["bfd_mode"] == "bfd"
        data["primary_interconnect_ip"] = data.get("primary_local_interconnect_ip", db_circuit.primary_local_interconnect_ip or db_circuit.primary_interconnect_ip)
        data["secondary_interconnect_ip"] = None if is_dual_lacp_private_line else data.get("secondary_local_interconnect_ip", db_circuit.secondary_local_interconnect_ip or db_circuit.secondary_interconnect_ip)
        data["local_interconnect_address"] = data.get("primary_local_interconnect_ip", db_circuit.primary_local_interconnect_ip or db_circuit.primary_interconnect_ip)
        data["remote_interconnect_address"] = data.get("primary_remote_interconnect_ip", db_circuit.primary_remote_interconnect_ip or db_circuit.remote_interconnect_address)
        data["interconnect_address"] = " - ".join(
            [
                item
                for item in [
                    data.get("primary_local_interconnect_ip", db_circuit.primary_local_interconnect_ip or db_circuit.primary_interconnect_ip),
                    data.get("primary_remote_interconnect_ip", db_circuit.primary_remote_interconnect_ip or db_circuit.remote_interconnect_address),
                ]
                if item
            ]
        )
    for key, value in data.items():
        setattr(db_circuit, key, value)
    db_circuit.updated_at = datetime.now()
    db.commit()
    db.refresh(db_circuit)
    db.refresh(db_circuit, attribute_names=["datacenter_ref", "vendor_ref", "customer_ref", "primary_device_ref", "secondary_device_ref", "aggregation_monitor_device_ref"])
    create_circuit_audit(
        db,
        circuit_id=db_circuit.id,
        action="update",
        actor=current_user,
        before_data=before_data,
        after_data=snapshot_circuit(db_circuit),
    )
    db.commit()
    return db_circuit.to_dict()


@router.delete("/circuits/{circuit_id}")
async def delete_circuit(
    circuit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    db_circuit = db.query(Circuit).filter(Circuit.id == circuit_id).first()
    if not db_circuit:
        raise HTTPException(status_code=404, detail="线路不存在")
    ip_count = db.query(IPAddressRecord).filter(IPAddressRecord.circuit_id == circuit_id).count()
    if ip_count > 0:
        raise HTTPException(status_code=400, detail=f"该线路下有{ip_count}条IP记录，无法删除")
    before_data = snapshot_circuit(db_circuit)
    create_circuit_audit(
        db,
        circuit_id=db_circuit.id,
        action="delete",
        actor=current_user,
        before_data=before_data,
        after_data=None,
    )
    db.delete(db_circuit)
    db.commit()
    return {"message": "线路已删除"}


@router.get("/circuits/{circuit_id}/usage-hourly", response_model=dict)
async def get_circuit_usage_hourly(
    circuit_id: int,
    range: str = Query("-24h", description="查询范围，例如 -24h、-7d"),
    db: Session = Depends(get_db),
):
    circuit = db.query(Circuit).filter(Circuit.id == circuit_id).first()
    if not circuit:
        raise HTTPException(status_code=404, detail="线路不存在")
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range})
      |> filter(fn: (r) => r._measurement == "circuit_usage_hourly")
      |> filter(fn: (r) => r.circuit_id == "{circuit_id}")
      |> filter(fn: (r) => r._field == "avg_mbps" or r._field == "avg_bandwidth_percent")
      |> pivot(rowKey: ["_time", "role", "circuit_name", "line_type"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
    '''
    data = influx_client.query(flux)
    return {
        "circuit": circuit.to_dict(),
        "range": range,
        "items": data,
        "total": len(data),
    }


@router.get("/circuits/{circuit_id}/audits", response_model=dict)
async def list_circuit_audits(
    circuit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    limit: int = Query(20, ge=1, le=100),
):
    circuit = db.query(Circuit).filter(Circuit.id == circuit_id).first()
    if not circuit:
        raise HTTPException(status_code=404, detail="线路不存在")
    items = (
        db.query(CircuitAudit)
        .filter(CircuitAudit.circuit_id == circuit_id)
        .order_by(CircuitAudit.created_at.desc(), CircuitAudit.id.desc())
        .limit(limit)
        .all()
    )
    return {"total": len(items), "items": [item.to_dict() for item in items]}


@router.get("/ipdb", response_model=dict)
async def list_ip_records(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
):
    query = db.query(IPAddressRecord).options(
        joinedload(IPAddressRecord.datacenter_ref),
        joinedload(IPAddressRecord.circuit_ref),
    )
    search_text = normalize_search_text(search)
    search_ip = parse_search_ip(search)
    filtered_items = [item for item in query.order_by(IPAddressRecord.id.desc()).all() if ip_record_matches_search(item, search_text, search_ip)]
    total = len(filtered_items)
    items = filtered_items[skip:skip + limit]
    return {"total": total, "items": [item.to_dict() for item in items]}


@router.post("/ipdb", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_ip_record(record: IPAddressRecordCreate, db: Session = Depends(get_db)):
    existing = db.query(IPAddressRecord).filter(IPAddressRecord.ip_address == record.ip_address).first()
    if existing:
        raise HTTPException(status_code=400, detail="IP地址已存在")
    if record.datacenter_id:
        datacenter = db.query(Datacenter).filter(Datacenter.id == record.datacenter_id).first()
        if not datacenter:
            raise HTTPException(status_code=400, detail="所属机房不存在")
    if record.circuit_id:
        circuit = db.query(Circuit).filter(Circuit.id == record.circuit_id).first()
        if not circuit:
            raise HTTPException(status_code=400, detail="所属线路不存在")
    db_record = IPAddressRecord(**record.model_dump())
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    db.refresh(db_record, attribute_names=["datacenter_ref", "circuit_ref"])
    return db_record.to_dict()


@router.put("/ipdb/{record_id}", response_model=dict)
async def update_ip_record(record_id: int, record: IPAddressRecordUpdate, db: Session = Depends(get_db)):
    db_record = db.query(IPAddressRecord).filter(IPAddressRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="IP记录不存在")
    data = record.model_dump(exclude_unset=True)
    if "ip_address" in data and data["ip_address"] != db_record.ip_address:
        existing = db.query(IPAddressRecord).filter(IPAddressRecord.ip_address == data["ip_address"]).first()
        if existing:
            raise HTTPException(status_code=400, detail="IP地址已存在")
    if "datacenter_id" in data and data["datacenter_id"]:
        datacenter = db.query(Datacenter).filter(Datacenter.id == data["datacenter_id"]).first()
        if not datacenter:
            raise HTTPException(status_code=400, detail="所属机房不存在")
    if "circuit_id" in data and data["circuit_id"]:
        circuit = db.query(Circuit).filter(Circuit.id == data["circuit_id"]).first()
        if not circuit:
            raise HTTPException(status_code=400, detail="所属线路不存在")
    for key, value in data.items():
        setattr(db_record, key, value)
    db_record.updated_at = datetime.now()
    db.commit()
    db.refresh(db_record)
    db.refresh(db_record, attribute_names=["datacenter_ref", "circuit_ref"])
    return db_record.to_dict()


@router.delete("/ipdb/{record_id}")
async def delete_ip_record(record_id: int, db: Session = Depends(get_db)):
    db_record = db.query(IPAddressRecord).filter(IPAddressRecord.id == record_id).first()
    if not db_record:
        raise HTTPException(status_code=404, detail="IP记录不存在")
    db.delete(db_record)
    db.commit()
    return {"message": "IP记录已删除"}
