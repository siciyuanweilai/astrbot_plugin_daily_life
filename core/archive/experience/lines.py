def _pack_lines(values: list[str]) -> str:
    return "\n".join(str(item).strip() for item in values if str(item).strip())


def _unpack_lines(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").splitlines() if item.strip()]
