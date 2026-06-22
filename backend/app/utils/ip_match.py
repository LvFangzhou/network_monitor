import ipaddress


def is_exact_ip_address(value: str) -> bool:
    """Return whether a silence value is one exact IP address."""
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except ValueError:
        return False


def ip_value_matches(source: str, candidate: str) -> bool:
    """Match an IP against an exact address, CIDR network, or inclusive range."""
    source_text = (source or "").strip()
    candidate_text = (candidate or "").strip()
    if not source_text or not candidate_text:
        return False

    try:
        source_ip = ipaddress.ip_address(source_text)
    except ValueError:
        return False

    if "-" in candidate_text:
        range_parts = [part.strip() for part in candidate_text.split("-")]
        if len(range_parts) != 2 or not all(range_parts):
            return False
        try:
            start_ip = ipaddress.ip_address(range_parts[0])
            end_ip = ipaddress.ip_address(range_parts[1])
        except ValueError:
            return False
        if source_ip.version != start_ip.version or start_ip.version != end_ip.version:
            return False
        return int(start_ip) <= int(source_ip) <= int(end_ip)

    if "/" in candidate_text:
        try:
            return source_ip in ipaddress.ip_network(candidate_text, strict=False)
        except ValueError:
            return False

    try:
        return source_ip == ipaddress.ip_address(candidate_text)
    except ValueError:
        return False
